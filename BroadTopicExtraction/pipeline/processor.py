# -*- coding: utf-8 -*-
"""
统一数据处理器

实现去重 + 历史追踪逻辑：
- 基于 dedup_fields 生成唯一 item_id
- 新数据直接插入
- 已存在数据：
  - 无时变字段：跳过
  - 有时变字段：更新当前值并追加历史记录
"""

import hashlib
import time
from typing import Dict, List, Optional, Any, Literal
from pymongo import UpdateOne
from loguru import logger

from .config_loader import ConfigLoader
from .mongo_writer import MongoWriter


ActionType = Literal["inserted", "updated", "skipped"]


class ProcessResult:
    """处理结果"""

    def __init__(self, action: ActionType, item_id: str, source: str):
        self.action = action
        self.item_id = item_id
        self.source = source

    def to_dict(self) -> Dict:
        return {
            "action": self.action,
            "item_id": self.item_id,
            "source": self.source,
        }


class DataProcessor:
    """统一数据处理器 - 去重 + 历史追踪"""

    def __init__(
        self,
        mongo_uri: Optional[str] = None,
        config_dir: Optional[str] = None,
    ):
        """
        初始化数据处理器

        Args:
            mongo_uri: MongoDB 连接 URI，默认从配置读取
            config_dir: YAML 配置目录，默认为 config/sources
        """
        self.mongo_writer = MongoWriter(mongo_uri)
        self.config_loader = ConfigLoader(config_dir)
        self._connected = False

    def connect(self) -> None:
        """建立连接"""
        if not self._connected:
            self.mongo_writer.connect()
            self._connected = True

    def close(self) -> None:
        """关闭连接"""
        if self._connected:
            self.mongo_writer.close()
            self._connected = False

    def __enter__(self) -> "DataProcessor":
        self.connect()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    def process(self, item: Dict, source_name: str) -> ProcessResult:
        """
        处理单条数据

        Args:
            item: 数据项
            source_name: 信源名称（对应 YAML 配置中的 key）

        Returns:
            ProcessResult 处理结果
        """
        config = self.config_loader.get_source(source_name)
        if not config:
            raise ValueError(f"未知信源: {source_name}")

        collection_name = config["mongo_collection"]
        dedup_fields = config["dedup_fields"]
        time_varying_fields = config.get("time_varying_fields", [])

        # 确保连接
        self.connect()

        # 生成 item_id
        item_id = self._generate_item_id(item, source_name, dedup_fields)

        # 查询是否已存在
        existing = self.mongo_writer.find_one(collection_name, {"item_id": item_id})
        now = int(time.time())

        if existing:
            if not time_varying_fields:
                # 无时变字段，跳过
                return ProcessResult("skipped", item_id, source_name)
            else:
                # 有时变字段，更新历史
                self._update_with_history(
                    collection_name, existing, item, time_varying_fields, now
                )
                return ProcessResult("updated", item_id, source_name)
        else:
            # 新数据，插入
            self._insert_new(
                collection_name, item, item_id, source_name, time_varying_fields, now
            )
            return ProcessResult("inserted", item_id, source_name)

    def process_batch(
        self, items: List[Dict], source_name: str
    ) -> Dict[str, List[ProcessResult]]:
        """
        批量处理数据

        Args:
            items: 数据项列表
            source_name: 信源名称

        Returns:
            按操作类型分组的处理结果
        """
        results: Dict[str, List[ProcessResult]] = {
            "inserted": [],
            "updated": [],
            "skipped": [],
        }

        for item in items:
            try:
                result = self.process(item, source_name)
                results[result.action].append(result)
            except Exception as e:
                logger.error(f"处理数据失败: {e}, item={item}")

        logger.info(
            f"[{source_name}] 处理完成: "
            f"插入 {len(results['inserted'])}, "
            f"更新 {len(results['updated'])}, "
            f"跳过 {len(results['skipped'])}"
        )

        return results

    def process_batch_optimized(
        self, items: List[Dict], source_name: str
    ) -> Dict[str, int]:
        """
        优化的批量处理（使用 bulk_write 减少数据库往返）

        Args:
            items: 数据项列表
            source_name: 信源名称

        Returns:
            操作统计 {inserted, updated, skipped}
        """
        config = self.config_loader.get_source(source_name)
        if not config:
            raise ValueError(f"未知信源: {source_name}")

        collection_name = config["mongo_collection"]
        dedup_fields = config["dedup_fields"]
        time_varying_fields = config.get("time_varying_fields", [])

        self.connect()
        now = int(time.time())

        # 生成所有 item_id
        item_ids = []
        items_with_id = []
        for item in items:
            item_id = self._generate_item_id(item, source_name, dedup_fields)
            item_ids.append(item_id)
            items_with_id.append((item, item_id))

        # 批量查询已存在的文档
        existing_docs = self.mongo_writer.find(
            collection_name,
            {"item_id": {"$in": item_ids}},
            projection={"item_id": 1, "_id": 1},
        )
        existing_ids = {doc["item_id"] for doc in existing_docs}

        # 分类处理
        operations = []
        stats = {"inserted": 0, "updated": 0, "skipped": 0}

        for item, item_id in items_with_id:
            if item_id in existing_ids:
                if not time_varying_fields:
                    stats["skipped"] += 1
                else:
                    # 构建更新操作
                    update_ops = self._build_update_ops(item, time_varying_fields, now)
                    operations.append(
                        UpdateOne({"item_id": item_id}, update_ops)
                    )
                    stats["updated"] += 1
            else:
                # 构建插入操作（使用 upsert）
                doc = self._build_new_doc(
                    item, item_id, source_name, time_varying_fields, now
                )
                operations.append(
                    UpdateOne({"item_id": item_id}, {"$setOnInsert": doc}, upsert=True)
                )
                stats["inserted"] += 1

        # 执行批量操作
        if operations:
            self.mongo_writer.bulk_write(collection_name, operations)

        logger.info(
            f"[{source_name}] 批量处理完成: "
            f"插入 {stats['inserted']}, "
            f"更新 {stats['updated']}, "
            f"跳过 {stats['skipped']}"
        )

        return stats

    def _generate_item_id(
        self, item: Dict, source: str, dedup_fields: List[str]
    ) -> str:
        """
        生成唯一 ID

        Args:
            item: 数据项
            source: 信源名称
            dedup_fields: 去重字段列表

        Returns:
            MD5 哈希的唯一 ID
        """
        parts = [source]
        for field in dedup_fields:
            value = item.get(field, "")
            parts.append(str(value))
        content = "_".join(parts)
        return hashlib.md5(content.encode("utf-8")).hexdigest()

    def _insert_new(
        self,
        collection_name: str,
        item: Dict,
        item_id: str,
        source_name: str,
        time_varying_fields: List[str],
        now: int,
    ) -> None:
        """插入新文档"""
        doc = self._build_new_doc(item, item_id, source_name, time_varying_fields, now)
        self.mongo_writer.insert_one(collection_name, doc)

    def _build_new_doc(
        self,
        item: Dict,
        item_id: str,
        source_name: str,
        time_varying_fields: List[str],
        now: int,
    ) -> Dict:
        """构建新文档"""
        doc = dict(item)
        doc["item_id"] = item_id
        doc["source"] = source_name
        doc["first_seen_at"] = now
        doc["last_seen_at"] = now

        # 初始化时变字段的历史
        for field in time_varying_fields:
            if field in doc and doc[field] is not None:
                doc[f"{field}_history"] = [{"ts": now, "val": doc[field]}]

        return doc

    def _update_with_history(
        self,
        collection_name: str,
        existing: Dict,
        new_item: Dict,
        time_varying_fields: List[str],
        now: int,
    ) -> None:
        """更新文档并追加历史"""
        update_ops = self._build_update_ops(new_item, time_varying_fields, now)
        self.mongo_writer.update_one(
            collection_name, {"_id": existing["_id"]}, update_ops
        )

    def _build_update_ops(
        self, item: Dict, time_varying_fields: List[str], now: int
    ) -> Dict:
        """构建更新操作"""
        update_set: Dict[str, Any] = {"last_seen_at": now}
        update_push: Dict[str, Any] = {}

        for field in time_varying_fields:
            new_val = item.get(field)
            if new_val is not None:
                update_set[field] = new_val
                update_push[f"{field}_history"] = {"ts": now, "val": new_val}

        update_ops: Dict[str, Any] = {"$set": update_set}
        if update_push:
            update_ops["$push"] = update_push

        return update_ops

    def get_stats(self, collection_name: str) -> Dict:
        """
        获取集合统计信息

        Args:
            collection_name: 集合名称

        Returns:
            统计信息
        """
        self.connect()
        total = self.mongo_writer.count_documents(collection_name, {})
        return {
            "collection": collection_name,
            "total_documents": total,
        }
