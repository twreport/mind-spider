# -*- coding: utf-8 -*-
"""
MongoDB 数据读取器

从 MongoDB 各 collection 读取数据，供信号检测使用。
"""

import time
from pathlib import Path
from typing import Any, Optional

import yaml
from loguru import logger

from BroadTopicExtraction.pipeline.mongo_writer import MongoWriter

# 各 collection 的字段投影
_HOT_PROJECTION = {
    "_id": 0,
    "item_id": 1,
    "title": 1,
    "platform": 1,
    "source": 1,
    "position": 1,
    "hot_value": 1,
    "hot_value_history": 1,
    "position_history": 1,
    "first_seen_at": 1,
    "last_seen_at": 1,
}

_HOT_VERTICAL_PROJECTION = {**_HOT_PROJECTION, "vertical": 1}

_MEDIA_PROJECTION = {
    "_id": 0,
    "item_id": 1,
    "title": 1,
    "platform": 1,
    "source": 1,
    "content": 1,
    "publish_time": 1,
    "first_seen_at": 1,
    "last_seen_at": 1,
}

DEFAULT_LOOKBACK = 3600  # 默认回看 1 小时


class DataReader:
    """MongoDB 数据读取器，为信号检测提供数据"""

    def __init__(self, mongo_writer: Optional[MongoWriter] = None):
        self._owned = mongo_writer is None
        self._mongo = mongo_writer or MongoWriter()
        self._filters = self._load_filters()

    def __enter__(self) -> "DataReader":
        self._mongo.connect()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._owned:
            self._mongo.close()

    # ------ 单 collection 读取 ------

    def get_hot_national(self, since_ts: Optional[int] = None) -> list[dict]:
        """读取 hot_national 中 last_seen_at >= since_ts 的文档"""
        since_ts = since_ts or _default_since()
        return self._query("hot_national", since_ts, _HOT_PROJECTION)

    def get_hot_vertical(self, since_ts: Optional[int] = None) -> list[dict]:
        """读取 hot_vertical，额外返回 vertical 字段"""
        since_ts = since_ts or _default_since()
        return self._query("hot_vertical", since_ts, _HOT_VERTICAL_PROJECTION)

    def get_aggregator(self, since_ts: Optional[int] = None) -> list[dict]:
        """读取 aggregator"""
        since_ts = since_ts or _default_since()
        return self._query("aggregator", since_ts, _HOT_PROJECTION)

    def get_media(self, since_ts: Optional[int] = None) -> list[dict]:
        """读取 media"""
        since_ts = since_ts or _default_since()
        return self._query("media", since_ts, _MEDIA_PROJECTION)

    def get_hot_local(self, since_ts: Optional[int] = None) -> list[dict]:
        """预留接口，暂返回空列表"""
        return []

    # ------ 按信源读取 ------

    def get_items_by_source(
        self, collection: str, source: str, since_ts: Optional[int] = None
    ) -> list[dict]:
        """读取指定 collection 中指定 source 的文档"""
        since_ts = since_ts or _default_since()
        projection = _HOT_VERTICAL_PROJECTION if collection == "hot_vertical" else _HOT_PROJECTION
        query = {"last_seen_at": {"$gte": since_ts}, "source": source}
        items = self._mongo.find(collection, query, projection=projection)
        items = self._apply_filters(items)
        logger.debug(f"[DataReader] {collection}/{source}: {len(items)} 条")
        return items

    # ------ 聚合读取 ------

    def get_all_hot_items(self, since_ts: Optional[int] = None) -> list[dict]:
        """聚合 hot_national + hot_vertical + aggregator，按 title 去重

        去重优先级: hot_national > hot_vertical > aggregator
        """
        since_ts = since_ts or _default_since()

        national = self.get_hot_national(since_ts)
        vertical = self.get_hot_vertical(since_ts)
        aggregator = self.get_aggregator(since_ts)

        seen_titles: set[str] = set()
        merged: list[dict] = []

        for items in (national, vertical, aggregator):
            for item in items:
                title = item.get("title", "")
                if title and title not in seen_titles:
                    seen_titles.add(title)
                    merged.append(item)

        logger.debug(
            f"聚合热搜: national={len(national)}, vertical={len(vertical)}, "
            f"aggregator={len(aggregator)}, 去重后={len(merged)}"
        )
        return merged

    # ------ 过滤 ------

    def _load_filters(self) -> dict:
        """加载 config/filters.yaml"""
        path = Path(__file__).parent.parent / "config" / "filters.yaml"
        try:
            with open(path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
        except FileNotFoundError:
            logger.warning(f"[DataReader] 过滤配置不存在: {path}")
            return {}
        # 将 titles list 转为 frozenset 加速查找
        for _src, bl in cfg.get("source_blacklist", {}).items():
            if "titles" in bl:
                bl["titles"] = frozenset(bl["titles"])
        return cfg

    def _apply_filters(self, items: list[dict]) -> list[dict]:
        """应用黑名单和辟谣置顶过滤"""
        if not self._filters:
            return items

        blacklist = self._filters.get("source_blacklist", {})
        debunk_rules = self._filters.get("pinned_debunk", [])

        filtered = []
        dropped = 0
        for item in items:
            source = item.get("source", "")
            title = item.get("title", "")

            # 1. 数据源黑名单 — 精确标题匹配 / 标题长度限制
            bl = blacklist.get(source)
            if bl:
                if title in bl.get("titles", frozenset()):
                    dropped += 1
                    continue
                max_len = bl.get("max_title_len")
                if max_len and len(title) <= max_len:
                    dropped += 1
                    continue

            # 2. 辟谣置顶
            skip = False
            for rule in debunk_rules:
                if source != rule.get("source"):
                    continue
                if item.get("position") != rule.get("position"):
                    continue
                keywords = rule.get("title_contains_any", [])
                if any(kw in title for kw in keywords):
                    skip = True
                    break
            if skip:
                dropped += 1
                continue

            filtered.append(item)

        if dropped:
            logger.info(f"[DataReader] 过滤掉 {dropped} 条永驻/置顶条目")
        return filtered

    # ------ 内部方法 ------

    def _query(self, collection: str, since_ts: int, projection: dict) -> list[dict]:
        query = {"last_seen_at": {"$gte": since_ts}}
        items = self._mongo.find(collection, query, projection=projection)
        items = self._apply_filters(items)
        logger.debug(f"[DataReader] {collection}: {len(items)} 条 (since_ts={since_ts})")
        return items


def _default_since() -> int:
    return int(time.time()) - DEFAULT_LOOKBACK
