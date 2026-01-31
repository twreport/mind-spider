# -*- coding: utf-8 -*-
"""
MindSpider Scrapy 管道

将爬取的数据通过统一数据管道写入 MongoDB
"""

import sys
from pathlib import Path
from typing import Dict, Any

from scrapy import Spider
from scrapy.exceptions import DropItem
from itemadapter import ItemAdapter
from loguru import logger

# 添加项目路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from pipeline import DataProcessor, ConfigLoader


class MongoPipeline:
    """MongoDB 写入管道"""

    def __init__(self, mongo_uri: str, config_dir: str):
        self.mongo_uri = mongo_uri
        self.config_dir = config_dir
        self.processor: DataProcessor | None = None
        self.config_loader: ConfigLoader | None = None

    @classmethod
    def from_crawler(cls, crawler):
        return cls(
            mongo_uri=crawler.settings.get("MONGO_URI"),
            config_dir=crawler.settings.get("CONFIG_DIR"),
        )

    def open_spider(self, spider: Spider) -> None:
        """爬虫启动时初始化"""
        self.processor = DataProcessor(
            mongo_uri=self.mongo_uri,
            config_dir=self.config_dir,
        )
        self.processor.connect()
        self.config_loader = self.processor.config_loader
        logger.info(f"[{spider.name}] MongoDB 管道已初始化")

    def close_spider(self, spider: Spider) -> None:
        """爬虫关闭时清理"""
        if self.processor:
            self.processor.close()
        logger.info(f"[{spider.name}] MongoDB 管道已关闭")

    def process_item(self, item: Any, spider: Spider) -> Any:
        """处理每个数据项"""
        adapter = ItemAdapter(item)
        data = dict(adapter)

        # 获取信源名称 (从 spider 的 source_name 属性或 name)
        source_name = getattr(spider, "source_name", spider.name)

        # 验证必填字段
        if not data.get("title"):
            raise DropItem(f"缺少标题: {data}")

        try:
            # 通过统一数据处理器写入
            result = self.processor.process(data, source_name)
            logger.debug(
                f"[{spider.name}] {result.action}: {data.get('title', '')[:30]}"
            )
        except ValueError as e:
            # 信源配置不存在，尝试直接写入
            logger.warning(f"[{spider.name}] 信源配置不存在，跳过: {e}")
            raise DropItem(str(e))
        except Exception as e:
            logger.error(f"[{spider.name}] 写入失败: {e}")
            raise DropItem(str(e))

        return item


class DuplicateFilterPipeline:
    """去重管道 (基于内存，用于单次爬取)"""

    def __init__(self):
        self.seen_titles: set = set()

    def process_item(self, item: Any, spider: Spider) -> Any:
        adapter = ItemAdapter(item)
        title = adapter.get("title", "")

        if title in self.seen_titles:
            raise DropItem(f"重复项: {title[:30]}")

        self.seen_titles.add(title)
        return item


class ValidationPipeline:
    """数据验证管道"""

    REQUIRED_FIELDS = ["title"]

    def process_item(self, item: Any, spider: Spider) -> Any:
        adapter = ItemAdapter(item)

        for field in self.REQUIRED_FIELDS:
            if not adapter.get(field):
                raise DropItem(f"缺少必填字段 {field}")

        # 清理标题
        title = adapter.get("title", "")
        adapter["title"] = str(title).strip()

        # 确保 position 是整数
        position = adapter.get("position")
        if position is not None:
            try:
                adapter["position"] = int(position)
            except (ValueError, TypeError):
                adapter["position"] = 0

        return item
