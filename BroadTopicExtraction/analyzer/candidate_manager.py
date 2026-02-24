# -*- coding: utf-8 -*-
"""
候选话题管理器

消费 signals collection 中的信号，管理候选话题生命周期：
1. 聚类 — cross_platform 信号直接建候选，Layer 1 信号用 jieba 关键词重叠率匹配
2. 准入 — 聚类命中 / cross_platform / velocity / position_jump 直接进，new_entry 需 position ≤ 10
3. 时间序列 — 每轮追加 snapshot (score_pos, sum_hot)，无信号轮次衰减 ×0.8
4. 状态机 — emerging → rising → confirmed → exploded → tracking → closed / faded

嵌入采集调度器：cross_platform 检测完成后调用 run_cycle()。
"""

import hashlib
import json
import time
from datetime import date
from typing import Optional
from urllib.parse import quote_plus

import yaml
from loguru import logger
from pymongo import UpdateOne

from BroadTopicExtraction.analyzer.signal_detector import _extract_keywords
from BroadTopicExtraction.pipeline.mongo_writer import MongoWriter

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from ms_config import settings

COLLECTION = "candidates"
CRAWL_TASKS_COLLECTION = "crawl_tasks"

# 表层采集平台名 → 深层采集平台代码
_SURFACE_TO_DEEP = {
    "weibo": "wb", "bilibili": "bili", "douyin": "dy",
    "zhihu": "zhihu", "kuaishou": "ks", "tieba": "tieba",
    "xiaohongshu": "xhs", "xhs": "xhs",
}

# 按候选状态定义爬取规模
# 按候选状态定义爬取规模（只在进入对应状态时触发）
# 目前只在 exploded 时触发，如需更早介入可取消注释：
_CRAWL_SCALE = {
    # "rising":    {"platforms": 3, "max_notes": 10, "priority": 1},
    # "confirmed": {"platforms": 5, "max_notes": 30, "priority": 2},
    "exploded":  {"platforms": 7, "max_notes": 20, "priority": 3},
}

# 加载平台权重（从 platforms.yaml）
_PLATFORMS_YAML = Path(__file__).parent.parent / "config" / "platforms.yaml"
_PLATFORM_WEIGHTS: dict[str, float] = {}
try:
    with open(_PLATFORMS_YAML, "r", encoding="utf-8") as _f:
        _platforms_config = yaml.safe_load(_f) or {}
    _PLATFORM_WEIGHTS = {k: v.get("weight", 0.5) for k, v in _platforms_config.items() if isinstance(v, dict)}
except Exception:
    logger.warning(f"无法加载平台权重配置: {_PLATFORMS_YAML}")

DEFAULT_PLATFORM_WEIGHT = 0.5  # 未配置平台的默认权重

# 活跃状态（参与衰减和状态机评估）
_ACTIVE_STATUSES = frozenset({"emerging", "rising", "confirmed", "exploded", "tracking"})

# 默认候选阈值
DEFAULT_CANDIDATE_THRESHOLDS = {
    "keyword_overlap_min": 0.6,  # jieba 关键词重叠率阈值
    "new_entry_max_position": 10,  # new_entry 准入最高排名
    "decay_factor": 0.8,  # 无信号轮次衰减系数
}

# 状态机转换规则（按优先级排列，先匹配先生效）
TRANSITION_RULES = [
    # 终态优先
    {"from": "*", "to": "faded", "condition": lambda c: c["score_pos"] < 100,
     "reason": "score_pos < 100"},
    {"from": "tracking", "to": "closed", "condition": lambda c: c["score_pos"] < 300,
     "reason": "score_pos < 300 while tracking"},
    # 下降趋势
    {"from": "*", "to": "tracking",
     "condition": lambda c: _is_declining(c, 3),
     "reason": "score_pos declining for 3 rounds"},
    # 上升阶梯
    {"from": "confirmed", "to": "exploded",
     "condition": lambda c: c["score_pos"] >= 10000,
     "reason": "score_pos >= 10000"},
    {"from": "rising", "to": "confirmed",
     "condition": lambda c: c["score_pos"] >= 4000,
     "reason": "score_pos >= 4000"},
    {"from": "emerging", "to": "rising",
     "condition": lambda c: c["score_pos"] >= 1500,
     "reason": "score_pos >= 1500"},
]


def _is_declining(candidate: dict, rounds: int) -> bool:
    """检查 score_pos 是否连续 N 轮下降"""
    snapshots = candidate.get("snapshots", [])
    if len(snapshots) < rounds + 1:
        return False
    recent = snapshots[-(rounds + 1):]
    return all(recent[i]["score_pos"] > recent[i + 1]["score_pos"] for i in range(rounds))


class CandidateManager:
    """候选话题管理器"""

    def __init__(
        self,
        signal_writer: Optional[MongoWriter] = None,
        thresholds: Optional[dict] = None,
    ):
        self.signal_writer = signal_writer or MongoWriter(
            db_name=settings.MONGO_SIGNAL_DB_NAME
        )
        self.thresholds = {**DEFAULT_CANDIDATE_THRESHOLDS, **(thresholds or {})}

    def ensure_indexes(self) -> None:
        """创建 candidates collection 索引"""
        self.signal_writer.connect()
        self.signal_writer.create_indexes(COLLECTION, [
            {"keys": [("candidate_id", 1)], "options": {"unique": True}},
            {"keys": [("status", 1)]},
            {"keys": [("updated_at", -1)]},
        ])

    # ==================== 信号消费 ====================

    def _fetch_all_signals(self) -> list[dict]:
        """读取 signals collection 全部文档"""
        self.signal_writer.connect()
        return self.signal_writer.find("signals", {})

    def _delete_all_signals(self) -> int:
        """清空 signals collection"""
        col = self.signal_writer.get_collection("signals")
        result = col.delete_many({})
        return result.deleted_count

    # ==================== 聚类 + 匹配 ====================

    def _compute_overlap(self, kw_a: set[str], kw_b: set[str]) -> float:
        """计算两组关键词的重叠率 = |交集| / min(|A|, |B|)"""
        if not kw_a or not kw_b:
            return 0.0
        intersection = len(kw_a & kw_b)
        return intersection / min(len(kw_a), len(kw_b))

    def _match_candidate(
        self, signal_keywords: set[str], existing_candidates: list[dict]
    ) -> Optional[dict]:
        """用 jieba 关键词重叠率匹配已有候选，返回最高重叠率的候选"""
        min_overlap = self.thresholds["keyword_overlap_min"]
        best_candidate = None
        best_overlap = 0.0

        for cand in existing_candidates:
            cand_keywords = set()
            for title in cand.get("source_titles", []):
                cand_keywords |= _extract_keywords(title)
            overlap = self._compute_overlap(signal_keywords, cand_keywords)
            if overlap >= min_overlap and overlap > best_overlap:
                best_overlap = overlap
                best_candidate = cand

        return best_candidate

    def _check_admission(self, signal: dict) -> bool:
        """准入标准检查"""
        stype = signal.get("signal_type")
        if stype in ("cross_platform", "velocity", "position_jump"):
            return True
        if stype == "new_entry":
            pos = signal.get("details", {}).get("position", 999)
            return pos <= self.thresholds["new_entry_max_position"]
        return False

    def _calc_score_pos(self, signal: dict) -> int:
        """从信号计算 score_pos = sum(int(10000/pos * weight))"""
        if signal.get("signal_type") == "cross_platform":
            total = 0
            for plat, plat_info in signal.get("details", {}).get("platform_items", {}).items():
                pos = plat_info.get("position")
                if pos and pos > 0:
                    w = _PLATFORM_WEIGHTS.get(plat, DEFAULT_PLATFORM_WEIGHT)
                    total += int(10000 / pos * w)
            return total
        # Layer 1: 单平台
        plat = signal.get("platform", "")
        w = _PLATFORM_WEIGHTS.get(plat, DEFAULT_PLATFORM_WEIGHT)
        pos_history = signal.get("position_history", [])
        if pos_history:
            pos = pos_history[-1].get("val", 0)
            if pos > 0:
                return int(10000 / pos * w)
        # fallback: details 里的 position
        pos = signal.get("details", {}).get("position") or signal.get("details", {}).get("current_position")
        if pos and pos > 0:
            return int(10000 / pos * w)
        return 0

    def _calc_sum_hot(self, signal: dict) -> int:
        """从信号计算 sum_hot"""
        if signal.get("signal_type") == "cross_platform":
            total = 0
            for plat_info in signal.get("details", {}).get("platform_items", {}).values():
                hv = plat_info.get("hot_value") or 0
                total += hv
            return total
        hv_history = signal.get("hot_value_history", [])
        if hv_history:
            return hv_history[-1].get("val", 0)
        return signal.get("details", {}).get("hot_value", 0)

    def _create_candidate(self, signal: dict, now: int) -> dict:
        """从信号创建新候选"""
        title = signal.get("title", "")
        title_hash = hashlib.md5(title.encode("utf-8")).hexdigest()[:12]
        score_pos = self._calc_score_pos(signal)
        sum_hot = self._calc_sum_hot(signal)

        # 收集所有标题
        source_titles = [title]
        if signal.get("signal_type") == "cross_platform":
            for plat_info in signal.get("details", {}).get("platform_items", {}).values():
                t = plat_info.get("title", "")
                if t and t != title:
                    source_titles.append(t)

        # 平台列表
        if signal.get("signal_type") == "cross_platform":
            platforms = signal.get("platforms", [])
        else:
            plat = signal.get("platform")
            platforms = [plat] if plat else []

        return {
            "candidate_id": f"cand_{title_hash}",
            "canonical_title": title,
            "source_titles": source_titles,
            "status": "emerging",
            "platforms": platforms,
            "platform_count": len(platforms),
            "snapshots": [{"ts": now, "score_pos": score_pos, "sum_hot": sum_hot}],
            "first_seen_at": now,
            "updated_at": now,
            "status_history": [
                {"ts": now, "status": "emerging", "reason": f"{signal.get('signal_type')} signal"},
            ],
        }

    def _update_candidate(self, candidate: dict, signal: dict, now: int) -> dict:
        """用新信号更新已有候选"""
        title = signal.get("title", "")
        if title and title not in candidate["source_titles"]:
            candidate["source_titles"].append(title)

        # 合并 cross_platform 的所有标题
        if signal.get("signal_type") == "cross_platform":
            for plat_info in signal.get("details", {}).get("platform_items", {}).values():
                t = plat_info.get("title", "")
                if t and t not in candidate["source_titles"]:
                    candidate["source_titles"].append(t)

        # 合并平台
        if signal.get("signal_type") == "cross_platform":
            new_plats = signal.get("platforms", [])
        else:
            plat = signal.get("platform")
            new_plats = [plat] if plat else []
        for p in new_plats:
            if p not in candidate["platforms"]:
                candidate["platforms"].append(p)
        candidate["platform_count"] = len(candidate["platforms"])

        # 累加 score_pos 到本轮 snapshot
        new_score = self._calc_score_pos(signal)
        new_hot = self._calc_sum_hot(signal)

        # 如果本轮已有 snapshot（同一 ts），累加；否则新建
        if candidate["snapshots"] and candidate["snapshots"][-1]["ts"] == now:
            candidate["snapshots"][-1]["score_pos"] += new_score
            candidate["snapshots"][-1]["sum_hot"] += new_hot
        else:
            candidate["snapshots"].append({"ts": now, "score_pos": new_score, "sum_hot": new_hot})

        candidate["updated_at"] = now
        candidate["_has_signal"] = True
        return candidate

    # ==================== 衰减 ====================

    def _apply_decay(self, candidates: list[dict], now: int) -> None:
        """对本轮没有新信号的活跃候选，追加衰减数据点"""
        decay = self.thresholds["decay_factor"]
        for cand in candidates:
            if cand.get("_has_signal"):
                continue
            if cand["status"] not in _ACTIVE_STATUSES:
                continue
            if not cand["snapshots"]:
                continue
            last = cand["snapshots"][-1]
            cand["snapshots"].append({
                "ts": now,
                "score_pos": int(last["score_pos"] * decay),
                "sum_hot": int(last["sum_hot"] * decay),
            })
            cand["updated_at"] = now

    # ==================== 状态机 ====================

    def _evaluate_transitions(self, candidates: list[dict], now: int) -> int:
        """评估所有活跃候选的状态机转换，返回转换数量"""
        transition_count = 0
        for cand in candidates:
            if cand["status"] not in _ACTIVE_STATUSES:
                continue
            if not cand["snapshots"]:
                continue

            # 构造评估上下文
            latest = cand["snapshots"][-1]
            ctx = {
                "score_pos": latest["score_pos"],
                "snapshots": cand["snapshots"],
            }

            for rule in TRANSITION_RULES:
                from_status = rule["from"]
                if from_status != "*" and from_status != cand["status"]:
                    continue
                # tracking 不能被上升规则覆盖
                if cand["status"] == "tracking" and rule["to"] in ("rising", "confirmed", "exploded"):
                    continue
                try:
                    if rule["condition"](ctx):
                        self._apply_transition(cand, rule["to"], rule["reason"], now)
                        transition_count += 1
                        break  # 每轮每个候选只触发一次转换
                except Exception as e:
                    logger.warning(f"规则评估异常 {rule['to']}: {e}")

        return transition_count

    def _apply_transition(self, candidate: dict, new_status: str, reason: str, now: int) -> None:
        """应用状态转换"""
        old_status = candidate["status"]
        candidate["status"] = new_status
        candidate["status_history"].append({
            "ts": now,
            "status": new_status,
            "reason": reason,
        })
        candidate["updated_at"] = now
        logger.info(
            f"[Candidate] {candidate['canonical_title'][:30]} "
            f"{old_status} → {new_status} ({reason})"
        )
        # 状态跃迁到 rising/confirmed/exploded 时生成爬取任务
        if new_status in _CRAWL_SCALE:
            self._emit_crawl_tasks(candidate, new_status, now)

    def _emit_crawl_tasks(self, candidate: dict, status: str, now: int) -> None:
        """根据候选状态生成深层采集任务并写入 crawl_tasks collection + Redis 任务队列"""
        scale = _CRAWL_SCALE.get(status)
        if not scale:
            return

        # 从候选的平台列表映射到深层采集平台代码
        deep_platforms = []
        for plat in candidate.get("platforms", []):
            code = _SURFACE_TO_DEEP.get(plat)
            if code and code not in deep_platforms:
                deep_platforms.append(code)

        # 限制爬取平台数
        deep_platforms = deep_platforms[:scale["platforms"]]
        if not deep_platforms:
            return

        # 生成搜索关键词：canonical_title + 其他 source_titles（去重）
        keywords = [candidate["canonical_title"]]
        for t in candidate.get("source_titles", []):
            if t and t != candidate["canonical_title"] and t not in keywords:
                keywords.append(t)
                if len(keywords) >= 3:
                    break

        cand_id = candidate["candidate_id"]
        col = self.signal_writer.get_collection(CRAWL_TASKS_COLLECTION)

        tasks_created = 0
        for plat in deep_platforms:
            # 去重：跳过已有活跃任务（pending/running）
            existing = col.find_one({
                "candidate_id": cand_id,
                "platform": plat,
                "status": {"$in": ["pending", "running"]},
            })
            if existing:
                continue

            task_doc = {
                "task_id": f"ct_{cand_id}_{plat}_{now}",
                "candidate_id": cand_id,
                "topic_title": candidate["canonical_title"],
                "search_keywords": keywords,
                "platform": plat,
                "max_notes": scale["max_notes"],
                "priority": scale["priority"],
                "status": "pending",
                "created_at": now,
                "attempts": 0,
            }
            # 写入 MongoDB（作为任务状态日志）
            col.insert_one(task_doc)
            # 推送到 Redis 任务队列
            try:
                from DeepSentimentCrawling.task_queue import get_task_queue
                queue = get_task_queue()
                queue.push_candidate_task(cand_id, status, task_doc)
            except Exception as e:
                logger.warning(f"[Candidate] Redis 推送失败（任务已在 MongoDB）: {e}")
            tasks_created += 1

        if tasks_created:
            logger.info(
                f"[Candidate] 为 {candidate['canonical_title'][:20]} "
                f"生成 {tasks_created} 个爬取任务 (status={status}) 并推送到 Redis 队列"
            )

    # ==================== 持久化 ====================

    def _save_candidates(self, candidates: list[dict]) -> dict:
        """批量 upsert candidates"""
        ops = []
        for cand in candidates:
            cand.pop("_has_signal", None)
            cand.pop("_id", None)
            cid = cand["candidate_id"]
            set_fields = {k: v for k, v in cand.items() if k != "candidate_id"}
            ops.append(
                UpdateOne(
                    {"candidate_id": cid},
                    {
                        "$set": set_fields,
                        "$setOnInsert": {"candidate_id": cid},
                    },
                    upsert=True,
                )
            )
        if ops:
            return self.signal_writer.bulk_write(COLLECTION, ops)
        return {"inserted": 0, "modified": 0, "upserted": 0}

    # ==================== 主循环 ====================

    def run_cycle(self) -> dict:
        """执行一轮候选管理

        Returns:
            统计信息 dict
        """
        now = int(time.time())
        self.ensure_indexes()

        # 1. 读取所有信号
        signals = self._fetch_all_signals()
        if not signals:
            # 即使没有新信号，也要对活跃候选做衰减和状态机评估
            active = self.signal_writer.find(
                COLLECTION, {"status": {"$in": list(_ACTIVE_STATUSES)}}
            )
            if active:
                self._apply_decay(active, now)
                transitions = self._evaluate_transitions(active, now)
                self._save_candidates(active)
                logger.info(
                    f"[Candidate] 无新信号，衰减 {len(active)} 个活跃候选，"
                    f"{transitions} 个状态转换"
                )
            return {
                "signals_consumed": 0,
                "candidates_created": 0,
                "candidates_updated": 0,
                "transitions": transitions if active else 0,
                "signals_deleted": 0,
            }

        logger.info(f"[Candidate] 开始消费 {len(signals)} 个信号")

        # 2. 加载已有活跃候选
        existing = self.signal_writer.find(
            COLLECTION, {"status": {"$in": list(_ACTIVE_STATUSES)}}
        )
        # candidate_id -> candidate dict
        cand_map: dict[str, dict] = {c["candidate_id"]: c for c in existing}

        # 3. 分类信号
        cross_signals = [s for s in signals if s.get("signal_type") == "cross_platform"]
        layer1_signals = [s for s in signals if s.get("signal_type") != "cross_platform"]

        created_count = 0
        updated_count = 0

        # 4. 先处理 cross_platform 信号 — 直接建/更新候选
        for sig in cross_signals:
            sig_kw = _extract_keywords(sig.get("title", ""))
            matched = self._match_candidate(sig_kw, list(cand_map.values()))
            if matched:
                self._update_candidate(matched, sig, now)
                updated_count += 1
            else:
                new_cand = self._create_candidate(sig, now)
                new_cand["_has_signal"] = True
                cand_map[new_cand["candidate_id"]] = new_cand
                created_count += 1

        # 5. 处理 Layer 1 信号 — jieba 重叠率匹配
        # 先收集所有 Layer 1 信号的关键词，用于聚类计数
        unmatched_signals: list[tuple[dict, set]] = []
        for sig in layer1_signals:
            sig_kw = _extract_keywords(sig.get("title", ""))
            matched = self._match_candidate(sig_kw, list(cand_map.values()))
            if matched:
                self._update_candidate(matched, sig, now)
                updated_count += 1
            else:
                unmatched_signals.append((sig, sig_kw))

        # 6. 未匹配的 Layer 1 信号：互相聚类 + 准入检查
        # 先尝试互相聚类
        min_overlap = self.thresholds["keyword_overlap_min"]
        clusters: list[list[tuple[dict, set]]] = []
        used = set()

        for i, (sig_i, kw_i) in enumerate(unmatched_signals):
            if i in used:
                continue
            cluster = [(sig_i, kw_i)]
            used.add(i)
            for j, (sig_j, kw_j) in enumerate(unmatched_signals):
                if j in used:
                    continue
                if self._compute_overlap(kw_i, kw_j) >= min_overlap:
                    cluster.append((sig_j, kw_j))
                    used.add(j)
            clusters.append(cluster)

        for cluster in clusters:
            if len(cluster) >= 2:
                # 聚类命中（≥2 信号匹配同一话题）→ 直接建候选
                primary_sig = cluster[0][0]
                new_cand = self._create_candidate(primary_sig, now)
                new_cand["_has_signal"] = True
                for sig, _ in cluster[1:]:
                    self._update_candidate(new_cand, sig, now)
                cand_map[new_cand["candidate_id"]] = new_cand
                created_count += 1
                updated_count += len(cluster) - 1
            else:
                # 单条信号，检查准入
                sig = cluster[0][0]
                if self._check_admission(sig):
                    new_cand = self._create_candidate(sig, now)
                    new_cand["_has_signal"] = True
                    cand_map[new_cand["candidate_id"]] = new_cand
                    created_count += 1

        # 7. 衰减：对本轮没有新信号的活跃候选
        all_candidates = list(cand_map.values())
        self._apply_decay(all_candidates, now)

        # 8. 状态机评估
        transitions = self._evaluate_transitions(all_candidates, now)

        # 9. 持久化
        save_result = self._save_candidates(all_candidates)
        logger.info(
            f"[Candidate] 保存候选: upserted={save_result.get('upserted', 0)}, "
            f"modified={save_result.get('modified', 0)}"
        )

        # 10. 清空信号
        deleted = self._delete_all_signals()
        logger.info(f"[Candidate] 已删除 {deleted} 个信号")

        stats = {
            "signals_consumed": len(signals),
            "candidates_created": created_count,
            "candidates_updated": updated_count,
            "transitions": transitions,
            "signals_deleted": deleted,
        }
        logger.info(f"[Candidate] 本轮统计: {stats}")
        return stats
