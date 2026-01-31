# -*- coding: utf-8 -*-
"""
MongoDB 写入器

提供 MongoDB 连接管理和写入操作
"""

from typing import Dict, List, Optional, Any
from pymongo import MongoClient, UpdateOne
from pymongo.collection import Collection
from pymongo.database import Database
from loguru import logger

import sys
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from config import settings


class MongoWriter:
    """MongoDB 写入器"""

    def __init__(self, mongo_uri: Optional[str] = None, db_name: Optional[str] = None):
        """
        初始化 MongoDB 写入器

        Args:
            mongo_uri: MongoDB 连接 URI，默认从配置读取
            db_name: 数据库名称，默认从配置读取
        """
        self.mongo_uri = mongo_uri or settings.MONGO_URI
        self.db_name = db_name or settings.MONGO_DB_NAME
        self._client: Optional[MongoClient] = None
        self._db: Optional[Database] = None

    def connect(self) -> None:
        """建立 MongoDB 连接"""
        if self._client is None:
            try:
                self._client = MongoClient(self.mongo_uri)
                self._db = self._client[self.db_name]
                # 测试连接
                self._client.admin.command("ping")
                logger.info(f"MongoDB 连接成功: {self.db_name}")
            except Exception as e:
                logger.error(f"MongoDB 连接失败: {e}")
                raise

    def close(self) -> None:
        """关闭 MongoDB 连接"""
        if self._client:
            self._client.close()
            self._client = None
            self._db = None
            logger.debug("MongoDB 连接已关闭")

    def __enter__(self) -> "MongoWriter":
        self.connect()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()

    @property
    def db(self) -> Database:
        """获取数据库实例"""
        if self._db is None:
            self.connect()
        return self._db  # type: ignore

    def get_collection(self, collection_name: str) -> Collection:
        """
        获取集合实例

        Args:
            collection_name: 集合名称

        Returns:
            MongoDB 集合实例
        """
        return self.db[collection_name]

    def insert_one(self, collection_name: str, document: Dict) -> str:
        """
        插入单个文档

        Args:
            collection_name: 集合名称
            document: 文档数据

        Returns:
            插入的文档 ID
        """
        collection = self.get_collection(collection_name)
        result = collection.insert_one(document)
        return str(result.inserted_id)

    def insert_many(self, collection_name: str, documents: List[Dict]) -> List[str]:
        """
        批量插入文档

        Args:
            collection_name: 集合名称
            documents: 文档列表

        Returns:
            插入的文档 ID 列表
        """
        if not documents:
            return []
        collection = self.get_collection(collection_name)
        result = collection.insert_many(documents)
        return [str(id) for id in result.inserted_ids]

    def find_one(self, collection_name: str, query: Dict) -> Optional[Dict]:
        """
        查询单个文档

        Args:
            collection_name: 集合名称
            query: 查询条件

        Returns:
            文档数据，不存在则返回 None
        """
        collection = self.get_collection(collection_name)
        return collection.find_one(query)

    def find(
        self,
        collection_name: str,
        query: Dict,
        projection: Optional[Dict] = None,
        limit: int = 0,
        sort: Optional[List] = None,
    ) -> List[Dict]:
        """
        查询多个文档

        Args:
            collection_name: 集合名称
            query: 查询条件
            projection: 字段投影
            limit: 限制返回数量
            sort: 排序规则

        Returns:
            文档列表
        """
        collection = self.get_collection(collection_name)
        cursor = collection.find(query, projection)
        if sort:
            cursor = cursor.sort(sort)
        if limit > 0:
            cursor = cursor.limit(limit)
        return list(cursor)

    def update_one(
        self, collection_name: str, query: Dict, update: Dict, upsert: bool = False
    ) -> int:
        """
        更新单个文档

        Args:
            collection_name: 集合名称
            query: 查询条件
            update: 更新操作
            upsert: 是否在不存在时插入

        Returns:
            修改的文档数量
        """
        collection = self.get_collection(collection_name)
        result = collection.update_one(query, update, upsert=upsert)
        return result.modified_count

    def bulk_write(self, collection_name: str, operations: List[UpdateOne]) -> Dict:
        """
        批量写入操作

        Args:
            collection_name: 集合名称
            operations: 操作列表

        Returns:
            操作结果统计
        """
        if not operations:
            return {"inserted": 0, "modified": 0, "upserted": 0}
        collection = self.get_collection(collection_name)
        result = collection.bulk_write(operations, ordered=False)
        return {
            "inserted": result.inserted_count,
            "modified": result.modified_count,
            "upserted": result.upserted_count,
        }

    def count_documents(self, collection_name: str, query: Dict) -> int:
        """
        统计文档数量

        Args:
            collection_name: 集合名称
            query: 查询条件

        Returns:
            文档数量
        """
        collection = self.get_collection(collection_name)
        return collection.count_documents(query)

    def create_indexes(self, collection_name: str, indexes: List[Dict]) -> List[str]:
        """
        创建索引

        Args:
            collection_name: 集合名称
            indexes: 索引定义列表，每个元素包含 keys 和可选的 options

        Returns:
            创建的索引名称列表
        """
        collection = self.get_collection(collection_name)
        created = []
        for index in indexes:
            keys = index.get("keys", [])
            options = index.get("options", {})
            name = collection.create_index(keys, **options)
            created.append(name)
        return created
