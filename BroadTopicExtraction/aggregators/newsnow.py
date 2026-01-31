# -*- coding: utf-8 -*-
"""
NewsNow 聚合器

基于 NewsNow API (https://newsnow.busiyi.world) 获取热搜数据
改造自原有的 get_today_news.py
"""

from typing import Dict, List, Any
from loguru import logger

from .base import BaseAggregator, AggregatorResult


class NewsNowAggregator(BaseAggregator):
    """NewsNow 聚合器"""

    name = "newsnow"
    display_name = "NewsNow 热搜聚合"
    base_url = "https://newsnow.busiyi.world"

    # 支持的数据源映射
    SOURCE_MAP = {
        "weibo": {"path": "/weibo", "name": "微博热搜"},
        "zhihu": {"path": "/zhihu", "name": "知乎热榜"},
        "bilibili-hot-search": {"path": "/bilibili-hot-search", "name": "B站热搜"},
        "toutiao": {"path": "/toutiao", "name": "今日头条"},
        "douyin": {"path": "/douyin", "name": "抖音热搜"},
        "github-trending-today": {"path": "/github-trending-today", "name": "GitHub Trending"},
        "coolapk": {"path": "/coolapk", "name": "酷安热榜"},
        "tieba": {"path": "/tieba", "name": "贴吧热议"},
        "wallstreetcn": {"path": "/wallstreetcn", "name": "华尔街见闻"},
        "thepaper": {"path": "/thepaper", "name": "澎湃新闻"},
        "cls-hot": {"path": "/cls-hot", "name": "财联社热榜"},
        "xueqiu": {"path": "/xueqiu", "name": "雪球热帖"},
        "baidu": {"path": "/baidu", "name": "百度热搜"},
        "36kr": {"path": "/36kr", "name": "36氪"},
        "sspai": {"path": "/sspai", "name": "少数派"},
        "ithome": {"path": "/ithome", "name": "IT之家"},
        "juejin": {"path": "/juejin", "name": "掘金"},
        "v2ex": {"path": "/v2ex", "name": "V2EX"},
    }

    def get_supported_sources(self) -> List[str]:
        """获取支持的数据源列表"""
        return list(self.SOURCE_MAP.keys())

    def get_source_name(self, source: str) -> str:
        """获取数据源显示名称"""
        return self.SOURCE_MAP.get(source, {}).get("name", source)

    async def fetch(self, source: str, **kwargs: Any) -> AggregatorResult:
        """
        从 NewsNow API 获取数据

        Args:
            source: 数据源 ID (如 weibo, zhihu 等)

        Returns:
            AggregatorResult 结果对象
        """
        if source not in self.SOURCE_MAP:
            return self._make_error_result(source, f"不支持的数据源: {source}")

        source_info = self.SOURCE_MAP[source]
        url = f"{self.base_url}{source_info['path']}"

        try:
            client = await self._get_client()
            response = await client.get(url)
            response.raise_for_status()

            data = response.json()

            # 解析数据
            items = self._parse_items(data, source)

            return self._make_success_result(source, items, raw_data=data)

        except Exception as e:
            logger.error(f"[NewsNow] 获取 {source} 失败: {e}")
            return self._make_error_result(source, str(e))

    def _parse_items(self, data: Any, source: str) -> List[Dict]:
        """
        解析 API 返回数据

        Args:
            data: API 返回的原始数据
            source: 数据源 ID

        Returns:
            标准化的数据项列表
        """
        items = []

        # NewsNow API 返回格式: {"data": [...]} 或直接返回列表
        raw_items = []
        if isinstance(data, dict):
            raw_items = data.get("data", [])
            if not raw_items and "items" in data:
                raw_items = data.get("items", [])
        elif isinstance(data, list):
            raw_items = data

        for rank, item in enumerate(raw_items, start=1):
            parsed = self._parse_single_item(item, source, rank)
            if parsed:
                items.append(parsed)

        return items

    def _parse_single_item(self, item: Any, source: str, rank: int) -> Dict | None:
        """
        解析单条数据

        Args:
            item: 原始数据项
            source: 数据源 ID
            rank: 排名

        Returns:
            标准化的数据项，解析失败返回 None
        """
        if not isinstance(item, dict):
            return None

        # 提取标题 (不同源可能用不同字段名)
        title = (
            item.get("title")
            or item.get("name")
            or item.get("word")
            or item.get("query")
        )
        if not title:
            return None

        # 提取 URL
        url = (
            item.get("url")
            or item.get("link")
            or item.get("mobileUrl")
            or ""
        )

        # 提取热度值 (不同源可能用不同字段名)
        hot_value = (
            item.get("hot")
            or item.get("hotValue")
            or item.get("heat")
            or item.get("score")
            or item.get("num")
        )

        # 构建标准化数据
        result = {
            "title": str(title).strip(),
            "url": url,
            "position": rank,
            "platform": source,
        }

        # 添加热度值（如果有）
        if hot_value is not None:
            try:
                result["hot_value"] = int(hot_value)
            except (ValueError, TypeError):
                result["hot_value"] = 0

        # 保留原始数据中的额外字段
        extra_fields = ["desc", "description", "pic", "image", "category", "tag"]
        for field in extra_fields:
            if field in item and item[field]:
                result[field] = item[field]

        return result
