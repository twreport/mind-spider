# -*- coding: utf-8 -*-
"""
信号检测器

4 种检测算法:
- velocity: 热度飙升
- new_entry: 新上榜高热
- position_jump: 排名跃升
- cross_platform: 跨平台共振（jieba 粗筛）

检测结果写入 MongoDB mindspider_signal 库的 signals collection，
consumed=false 供候选管理消费。
"""

import hashlib
import time
from collections import defaultdict
from typing import Optional

import jieba
from loguru import logger
from pymongo import UpdateOne

from BroadTopicExtraction.analyzer.data_reader import DataReader
from BroadTopicExtraction.pipeline.mongo_writer import MongoWriter

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from config import settings

# jieba 粗筛停用词（单字、标点、常见套话）
_STOPWORDS = frozenset(
    "的 了 在 是 我 有 和 就 不 人 都 一 一个 上 也 很 到 说 要 去 你 会 着 没有 看 好 "
    "自己 这 他 她 它 们 那 被 从 把 让 用 为 什么 怎么 如何 如何看待 哪些 为什么 "
    "怎样 可以 这个 那个 还是 或者 以及 但是 然而 因为 所以 如果 虽然 已经 正在 "
    "关于 对于 通过 进行 开始 之后 之前 以来 目前 今天 昨天 明天 最新 最近 突发 "
    "热搜 曝光 回应 官方 发布 公布 通报".split()
)

# 平台名归一化：聚合器变体 → 标准平台名
# 同一平台的聚合源和原生爬虫不算跨平台
_PLATFORM_ALIAS = {
    "bilibili-hot-search": "bilibili",
    "cls-hot": "cls",
    "douban-movie": "douban",
    "github-trending-today": "github",
}

DEFAULT_THRESHOLDS = {
    "velocity_growth_rate": 0.5,  # 热度增长率 > 50%
    "velocity_min_hot_value": 10000,  # 最低热度基线
    "new_entry_max_age": 1800,  # 新上榜: first_seen 在最近 30 分钟内
    "new_entry_min_hot_value": 50000,  # 新上榜最低热度
    "new_entry_max_position": 10,  # 新上榜最高排名
    "position_jump_min": 10,  # 排名跃升最小幅度
    "cross_platform_min_keywords": 2,  # 关键词交集最少个数
    "cross_platform_min_platforms": 3,  # 跨平台最少平台数
}


class SignalDetector:
    """信号检测器，发现异动并写入 signals collection

    信号写入独立的 mindspider_signal 库，与原始数据分离。
    """

    def __init__(
        self,
        data_reader: Optional[DataReader] = None,
        signal_writer: Optional[MongoWriter] = None,
        thresholds: Optional[dict] = None,
    ):
        self.data_reader = data_reader or DataReader()
        # 信号写入独立的 signal 库
        self.signal_writer = signal_writer or MongoWriter(
            db_name=settings.MONGO_SIGNAL_DB_NAME
        )
        self.thresholds = {**DEFAULT_THRESHOLDS, **(thresholds or {})}

    def detect(self, since_ts: Optional[int] = None) -> list[dict]:
        """执行一次完整检测（Layer 1 + Layer 2），返回所有发现的信号

        适用于手动触发或定时全量检测。生产环境建议用
        detect_for_source() + detect_cross_platform() 分开调用。
        """
        since_ts = since_ts or int(time.time()) - 3600

        signals = self._detect_layer1_all(since_ts)
        signals += self.detect_cross_platform(since_ts)

        logger.info(f"全量信号检测完成: 共 {len(signals)} 个信号")

        return signals

    def detect_for_source(
        self, source: str, source_collection: str, since_ts: Optional[int] = None
    ) -> list[dict]:
        """单信源 Layer 1 检测：每爬完一个信源后调用

        只跑 velocity / new_entry / position_jump，不跑跨平台。

        Args:
            source: 信源名称，如 "baidu_hot", "tophub_weibo"
            source_collection: 该信源写入的 collection，如 "hot_national", "aggregator"
        """
        since_ts = since_ts or int(time.time()) - 3600
        signals: list[dict] = []

        items = self.data_reader.get_items_by_source(source_collection, source, since_ts)
        if items:
            signals += self._detect_velocity(items, source_collection)
            signals += self._detect_new_entry(items, source_collection)
            signals += self._detect_position_jump(items, source_collection)

        logger.info(f"信号检测 [{source}] Layer 1: {len(signals)} 个信号")
        for s in signals:
            logger.debug(f"  [{s['signal_type']}] {s['title']}")

        if signals:
            self._write_signals(signals)

        return signals

    def detect_cross_platform(self, since_ts: Optional[int] = None) -> list[dict]:
        """跨平台共振检测（Layer 2）

        独立调用，适合在一轮采集全部完成后跑一次，或独立定时（如每 30 分钟）。
        """
        since_ts = since_ts or int(time.time()) - 3600

        all_items = self.data_reader.get_all_hot_items(since_ts)
        signals = self._detect_cross_platform(all_items)

        logger.info(f"跨平台信号检测: {len(signals)} 个信号")
        for s in signals:
            logger.debug(f"  [{s['signal_type']}] {s['title']}")

        if signals:
            self._write_signals(signals)

        return signals

    def _detect_layer1_all(self, since_ts: int) -> list[dict]:
        """对 hot_national + hot_vertical 全量跑 Layer 1"""
        signals: list[dict] = []
        for collection, getter in (
            ("hot_national", self.data_reader.get_hot_national),
            ("hot_vertical", self.data_reader.get_hot_vertical),
        ):
            items = getter(since_ts)
            signals += self._detect_velocity(items, collection)
            signals += self._detect_new_entry(items, collection)
            signals += self._detect_position_jump(items, collection)

        if signals:
            self._write_signals(signals)

        return signals

    # ==================== Layer 1 算法 ====================

    def _detect_velocity(self, items: list[dict], source_collection: str) -> list[dict]:
        """热度飙升检测

        从 hot_value_history 取最近两个快照，计算增长率。
        增长率 > threshold 且当前 hot_value > min_baseline → 生成信号。
        """
        signals = []
        min_val = self.thresholds["velocity_min_hot_value"]
        min_rate = self.thresholds["velocity_growth_rate"]

        for item in items:
            history = item.get("hot_value_history") or []
            if len(history) < 2:
                continue

            prev = history[-2].get("val", 0)
            curr = history[-1].get("val", 0)

            if prev <= 0 or curr < min_val:
                continue

            growth_rate = (curr - prev) / prev
            if growth_rate >= min_rate:
                signals.append(
                    self._build_signal(
                        signal_type="velocity",
                        item=item,
                        source_collection=source_collection,
                        details={
                            "previous_value": prev,
                            "current_value": curr,
                            "growth_rate": round(growth_rate, 3),
                        },
                    )
                )
        return signals

    def _detect_new_entry(self, items: list[dict], source_collection: str) -> list[dict]:
        """新上榜检测

        first_seen_at 在最近 max_age 秒内，
        且 hot_value >= threshold 或 position <= threshold → 生成信号。
        """
        signals = []
        now = int(time.time())
        max_age = self.thresholds["new_entry_max_age"]
        min_hot = self.thresholds["new_entry_min_hot_value"]
        max_pos = self.thresholds["new_entry_max_position"]

        for item in items:
            first_seen = item.get("first_seen_at", 0)
            if not first_seen or (now - first_seen) > max_age:
                continue

            hot_value = item.get("hot_value", 0) or 0
            position = item.get("position") or 999

            if hot_value >= min_hot or position <= max_pos:
                signals.append(
                    self._build_signal(
                        signal_type="new_entry",
                        item=item,
                        source_collection=source_collection,
                        details={
                            "hot_value": hot_value,
                            "position": position,
                            "age_seconds": now - first_seen,
                        },
                    )
                )
        return signals

    def _detect_position_jump(self, items: list[dict], source_collection: str) -> list[dict]:
        """排名跃升检测

        从 position_history 取最近两个快照，排名上升 >= threshold → 生成信号。
        注意: position 数值越小排名越高，所以 prev - curr >= threshold 表示跃升。
        """
        signals = []
        min_jump = self.thresholds["position_jump_min"]

        for item in items:
            history = item.get("position_history") or []
            if len(history) < 2:
                continue

            prev_pos = history[-2].get("val", 0)
            curr_pos = history[-1].get("val", 0)

            if prev_pos <= 0 or curr_pos <= 0:
                continue

            jump = prev_pos - curr_pos  # 正数表示排名上升
            if jump >= min_jump:
                signals.append(
                    self._build_signal(
                        signal_type="position_jump",
                        item=item,
                        source_collection=source_collection,
                        details={
                            "previous_position": prev_pos,
                            "current_position": curr_pos,
                            "jump": jump,
                        },
                    )
                )
        return signals

    # ==================== Layer 2 算法 ====================

    def _detect_cross_platform(self, items: list[dict]) -> list[dict]:
        """跨平台共振检测（jieba 粗筛）

        1. 对所有 title 用 jieba 提取关键词
        2. 用倒排索引找关键词交集 >= 2 的配对
        3. 贪心聚类，同一话题出现在 >= 3 个不同 platform → 生成信号

        NOTE: 超级话题（如春晚）可能命中 20+ 平台，此处不做上限过滤，
        留给候选管理阶段根据话题生命周期和客户兴趣做降噪处理。
        """
        if not items:
            return []

        min_kw = self.thresholds["cross_platform_min_keywords"]
        min_plat = self.thresholds["cross_platform_min_platforms"]

        # 1. 提取关键词
        item_data: dict[str, dict] = {}  # item_id -> {item, keywords}
        for item in items:
            item_id = item.get("item_id", "")
            if not item_id:
                continue
            words = _extract_keywords(item.get("title", ""))
            if words:
                item_data[item_id] = {"item": item, "keywords": words}

        if len(item_data) < min_plat:
            return []

        # 2. 倒排索引: keyword -> [item_ids]
        kw_index: dict[str, list[str]] = defaultdict(list)
        for item_id, data in item_data.items():
            for kw in data["keywords"]:
                kw_index[kw].append(item_id)

        # 3. 找配对 — 共享关键词 >= min_kw 的 item 对
        pair_count: dict[tuple[str, str], set[str]] = defaultdict(set)
        for kw, ids in kw_index.items():
            if len(ids) > 50:
                # 太常见的词跳过，避免噪音
                continue
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    a, b = ids[i], ids[j]
                    key = (min(a, b), max(a, b))
                    pair_count[key].add(kw)

        # 4. 贪心聚类 — Union-Find
        parent: dict[str, str] = {}

        def find(x: str) -> str:
            while parent.get(x, x) != x:
                parent[x] = parent.get(parent[x], parent[x])
                x = parent[x]
            return x

        def union(x: str, y: str) -> None:
            rx, ry = find(x), find(y)
            if rx != ry:
                parent[rx] = ry

        for (a, b), common_kws in pair_count.items():
            if len(common_kws) >= min_kw:
                union(a, b)

        # 5. 按组聚合
        groups: dict[str, list[str]] = defaultdict(list)
        for item_id in item_data:
            groups[find(item_id)].append(item_id)

        # 6. 检查每组的平台数（归一化后）
        signals = []
        for group_ids in groups.values():
            platforms: dict[str, dict] = {}  # normalized_platform -> item info
            common_keywords: set[str] = set()

            for item_id in group_ids:
                data = item_data[item_id]
                raw_plat = data["item"].get("platform", "unknown")
                plat = _normalize_platform(raw_plat)
                if plat not in platforms:
                    item = data["item"]
                    platforms[plat] = {
                        "title": item.get("title", ""),
                        "hot_value": item.get("hot_value"),
                        "position": item.get("position"),
                        "hot_value_history": item.get("hot_value_history", []),
                        "position_history": item.get("position_history", []),
                    }
                if not common_keywords:
                    common_keywords = set(data["keywords"])
                else:
                    common_keywords &= data["keywords"]

            if len(platforms) >= min_plat:
                representative_title = next(iter(platforms.values()))["title"]
                signals.append(
                    self._build_signal(
                        signal_type="cross_platform",
                        item=None,
                        source_collection="cross",
                        details={
                            "platform_count": len(platforms),
                            "platform_items": platforms,
                            "common_keywords": sorted(common_keywords),
                        },
                        title_override=representative_title,
                        platforms=sorted(platforms.keys()),
                    )
                )

        return signals

    # ==================== 输出 ====================

    def _write_signals(self, signals: list[dict]) -> None:
        """批量 upsert 到 signals collection（mindspider_signal 库）

        用 signal_id 做唯一键：
        - 新信号: 插入完整文档，设置 detected_at
        - 已有信号: 更新 details、历史数据、updated_at
        """
        try:
            self.signal_writer.connect()
            ops = []
            for s in signals:
                sid = s["signal_id"]
                # $set 更新除 signal_id 外的所有字段
                set_fields = {k: v for k, v in s.items() if k != "signal_id"}
                set_fields["updated_at"] = int(time.time())
                ops.append(
                    UpdateOne(
                        {"signal_id": sid},
                        {
                            "$set": set_fields,
                            "$setOnInsert": {
                                "signal_id": sid,
                                "detected_at": int(time.time()),
                                "consumed": False,
                            },
                        },
                        upsert=True,
                    )
                )
            result = self.signal_writer.bulk_write("signals", ops)
            logger.info(
                f"信号 upsert: inserted={result['upserted']}, "
                f"updated={result['modified']}"
            )
        except Exception as e:
            logger.error(f"写入信号失败: {e}")
            raise

    def _build_signal(
        self,
        signal_type: str,
        item: Optional[dict],
        source_collection: str,
        details: dict,
        title_override: Optional[str] = None,
        platforms: Optional[list[str]] = None,
    ) -> dict:
        """构造信号文档

        signal_id 稳定（不含时间戳），同一话题重复检测时 upsert 更新。
        Layer 1: signal_type + title_hash + platform
        Layer 2: signal_type + title_hash
        """
        title = title_override or (item.get("title", "") if item else "")
        platform = (item.get("platform", "") if item else None) if not platforms else None
        title_hash = hashlib.md5(title.encode("utf-8")).hexdigest()[:12]
        layer = 2 if signal_type == "cross_platform" else 1

        if layer == 1:
            signal_id = f"{signal_type}_{title_hash}_{platform}"
        else:
            signal_id = f"{signal_type}_{title_hash}"

        doc = {
            "signal_id": signal_id,
            "signal_type": signal_type,
            "layer": layer,
            "title": title,
            "platform": platform,
            "platforms": platforms or [],
            "source_collection": source_collection,
            "details": details,
        }

        # Layer 1: 带上完整历史数据
        if item and layer == 1:
            doc["hot_value_history"] = item.get("hot_value_history", [])
            doc["position_history"] = item.get("position_history", [])
            doc["first_seen_at"] = item.get("first_seen_at")
            doc["last_seen_at"] = item.get("last_seen_at")

        return doc


def _extract_keywords(title: str) -> set[str]:
    """用 jieba 从标题提取关键词，过滤停用词和单字"""
    if not title:
        return set()
    words = jieba.cut(title)
    return {w for w in words if len(w) >= 2 and w not in _STOPWORDS}


def _normalize_platform(platform: str) -> str:
    """归一化平台名，聚合器变体映射到标准名"""
    return _PLATFORM_ALIAS.get(platform, platform)
