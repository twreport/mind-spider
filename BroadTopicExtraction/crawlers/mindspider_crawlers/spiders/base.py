# -*- coding: utf-8 -*-
"""
MindSpider 爬虫基类

提供所有爬虫的通用功能和接口
"""

from abc import abstractmethod
from typing import Any, Dict, Generator, List, Optional
import scrapy
from scrapy.http import Response

from ..items import HotSearchItem, MediaItem, VerticalHotItem, LocalHotItem


class BaseSpider(scrapy.Spider):
    """爬虫基类"""

    # 子类需要定义的属性
    name: str = "base"
    source_name: str = ""  # 对应 YAML 配置中的 key
    platform: str = ""  # 平台标识
    allowed_domains: List[str] = []
    start_urls: List[str] = []

    # 自定义设置
    custom_settings: Dict[str, Any] = {}

    def __init__(self, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        # 如果没有设置 source_name，使用 name
        if not self.source_name:
            self.source_name = self.name

    @abstractmethod
    def parse(self, response: Response) -> Generator:
        """
        解析响应，子类必须实现

        Args:
            response: Scrapy Response 对象

        Yields:
            Item 对象
        """
        pass

    def make_item(self, **kwargs: Any) -> Dict:
        """
        创建数据项的便捷方法

        Args:
            **kwargs: 数据字段

        Returns:
            数据字典
        """
        # 添加平台标识
        if "platform" not in kwargs:
            kwargs["platform"] = self.platform
        return kwargs


class HotSearchSpider(BaseSpider):
    """热搜爬虫基类"""

    category = "hot_national"

    def make_hot_item(
        self,
        title: str,
        url: str,
        position: int,
        hot_value: Optional[int] = None,
        **kwargs: Any,
    ) -> HotSearchItem:
        """
        创建热搜数据项

        Args:
            title: 标题
            url: 链接
            position: 排名
            hot_value: 热度值
            **kwargs: 其他字段

        Returns:
            HotSearchItem
        """
        item = HotSearchItem()
        item["title"] = title.strip()
        item["url"] = url
        item["position"] = position
        item["platform"] = self.platform

        if hot_value is not None:
            item["hot_value"] = hot_value

        # 添加额外字段
        for key, value in kwargs.items():
            if key in HotSearchItem.fields and value is not None:
                item[key] = value

        return item


class LocalHotSpider(BaseSpider):
    """地方热搜爬虫基类"""

    category = "hot_local"
    region: str = ""  # 地区代码

    def make_local_item(
        self,
        title: str,
        url: str,
        position: int,
        hot_value: Optional[int] = None,
        **kwargs: Any,
    ) -> LocalHotItem:
        """创建地方热搜数据项"""
        item = LocalHotItem()
        item["title"] = title.strip()
        item["url"] = url
        item["position"] = position
        item["platform"] = self.platform
        item["region"] = kwargs.pop("region", self.region)

        if hot_value is not None:
            item["hot_value"] = hot_value

        for key, value in kwargs.items():
            if key in LocalHotItem.fields and value is not None:
                item[key] = value

        return item


class VerticalHotSpider(BaseSpider):
    """行业/垂直榜单爬虫基类"""

    category = "hot_vertical"
    vertical: str = ""  # 垂直领域

    def make_vertical_item(
        self,
        title: str,
        url: str,
        position: int,
        **kwargs: Any,
    ) -> VerticalHotItem:
        """创建垂直榜单数据项"""
        item = VerticalHotItem()
        item["title"] = title.strip()
        item["url"] = url
        item["position"] = position
        item["platform"] = self.platform
        item["vertical"] = kwargs.pop("vertical", self.vertical)

        for key, value in kwargs.items():
            if key in VerticalHotItem.fields and value is not None:
                item[key] = value

        return item


class MediaSpider(BaseSpider):
    """传统媒体爬虫基类"""

    category = "media"
    media_type: str = "central"  # central, finance, local

    def make_media_item(
        self,
        title: str,
        url: str,
        publish_date: Optional[str] = None,
        content: Optional[str] = None,
        **kwargs: Any,
    ) -> MediaItem:
        """
        创建媒体文章数据项

        Args:
            title: 标题
            url: 链接
            publish_date: 发布日期
            content: 正文内容
            **kwargs: 其他字段

        Returns:
            MediaItem
        """
        item = MediaItem()
        item["title"] = title.strip()
        item["url"] = url
        item["platform"] = self.platform
        item["media_type"] = kwargs.pop("media_type", self.media_type)

        if publish_date:
            item["publish_date"] = publish_date
        if content:
            item["content"] = content

        for key, value in kwargs.items():
            if key in MediaItem.fields and value is not None:
                item[key] = value

        return item
