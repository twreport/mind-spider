# -*- coding: utf-8 -*-
"""
官方 API 聚合器

直接调用各平台官方公开 API，稳定性最高

测试状态: ✅ 可用
"""

from typing import Dict, List, Any
from loguru import logger
import json

from .base import BaseAggregator, AggregatorResult


class OfficialAPIAggregator(BaseAggregator):
    """官方 API 聚合器 - 最稳定"""

    name = "official"
    display_name = "官方API"
    base_url = ""

    # 官方 API 端点 (已验证可用)
    SOURCE_MAP = {
        "baidu": {
            "name": "百度热搜",
            "url": "https://top.baidu.com/api/board?platform=wise&tab=realtime",
            "method": "GET",
        },
        "douyin": {
            "name": "抖音热搜",
            "url": "https://www.douyin.com/aweme/v1/web/hot/search/list/",
            "method": "GET",
        },
        "tieba": {
            "name": "贴吧热议",
            "url": "https://tieba.baidu.com/hottopic/browse/topicList",
            "method": "GET",
        },
        "juejin": {
            "name": "掘金热榜",
            "url": "https://api.juejin.cn/recommend_api/v1/article/recommend_all_feed",
            "method": "POST",
            "payload": {"id_type": 2, "sort_type": 3, "cursor": "0", "limit": 50},
        },
        "bilibili_search": {
            "name": "B站热搜词",
            "url": "https://s.search.bilibili.com/main/hotword",
            "method": "GET",
        },
    }

    def get_supported_sources(self) -> List[str]:
        """获取支持的数据源列表"""
        return list(self.SOURCE_MAP.keys())

    def get_source_name(self, source: str) -> str:
        """获取数据源显示名称"""
        return self.SOURCE_MAP.get(source, {}).get("name", source)

    async def fetch(self, source: str, **kwargs: Any) -> AggregatorResult:
        """从官方 API 获取数据"""
        if source not in self.SOURCE_MAP:
            return self._make_error_result(source, f"不支持的数据源: {source}")

        source_info = self.SOURCE_MAP[source]
        url = source_info["url"]
        method = source_info.get("method", "GET")

        try:
            client = await self._get_client()

            if method == "POST":
                payload = source_info.get("payload", {})
                response = await client.post(
                    url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
            else:
                response = await client.get(url)

            response.raise_for_status()
            data = response.json()

            # 根据不同源解析数据
            items = self._parse_by_source(data, source)

            return self._make_success_result(source, items, raw_data=data)

        except Exception as e:
            logger.error(f"[Official] 获取 {source} 失败: {e}")
            return self._make_error_result(source, str(e))

    def _parse_by_source(self, data: Any, source: str) -> List[Dict]:
        """根据数据源解析数据"""
        if source == "baidu":
            return self._parse_baidu(data)
        elif source == "douyin":
            return self._parse_douyin(data)
        elif source == "tieba":
            return self._parse_tieba(data)
        elif source == "juejin":
            return self._parse_juejin(data)
        elif source == "bilibili_search":
            return self._parse_bilibili_search(data)
        return []

    def _parse_baidu(self, data: Any) -> List[Dict]:
        """解析百度热搜"""
        items = []
        raw_items = data.get("data", {}).get("cards", [])

        if raw_items and len(raw_items) > 0:
            content = raw_items[0].get("content", [])
            for rank, item in enumerate(content, start=1):
                title = item.get("word") or item.get("query")
                if not title:
                    continue

                items.append({
                    "title": title,
                    "url": item.get("url", f"https://www.baidu.com/s?wd={title}"),
                    "position": rank,
                    "platform": "baidu",
                    "hot_value": item.get("hotScore", 0),
                    "description": item.get("desc", ""),
                })

        return items

    def _parse_douyin(self, data: Any) -> List[Dict]:
        """解析抖音热搜"""
        items = []
        raw_items = data.get("data", {}).get("word_list", [])

        for rank, item in enumerate(raw_items, start=1):
            title = item.get("word")
            if not title:
                continue

            items.append({
                "title": title,
                "url": f"https://www.douyin.com/search/{title}",
                "position": rank,
                "platform": "douyin",
                "hot_value": item.get("hot_value", 0),
            })

        return items

    def _parse_tieba(self, data: Any) -> List[Dict]:
        """解析贴吧热议"""
        items = []
        raw_items = data.get("data", {}).get("bang_topic", {}).get("topic_list", [])

        for rank, item in enumerate(raw_items, start=1):
            title = item.get("topic_name")
            if not title:
                continue

            items.append({
                "title": title,
                "url": item.get("topic_url", ""),
                "position": rank,
                "platform": "tieba",
                "hot_value": item.get("discuss_num", 0),
            })

        return items

    def _parse_juejin(self, data: Any) -> List[Dict]:
        """解析掘金热榜"""
        items = []
        raw_items = data.get("data", [])

        for rank, item in enumerate(raw_items, start=1):
            article = item.get("article_info", {})
            author = item.get("author_user_info", {})

            title = article.get("title")
            if not title:
                continue

            article_id = article.get("article_id", "")

            items.append({
                "title": title,
                "url": f"https://juejin.cn/post/{article_id}" if article_id else "",
                "position": rank,
                "platform": "juejin",
                "hot_value": article.get("view_count", 0),
                "likes": article.get("digg_count", 0),
                "replies": article.get("comment_count", 0),
                "author": author.get("user_name"),
                "description": article.get("brief_content"),
            })

        return items

    def _parse_bilibili_search(self, data: Any) -> List[Dict]:
        """解析B站热搜词"""
        items = []
        raw_items = data.get("list", [])

        for rank, item in enumerate(raw_items, start=1):
            title = item.get("keyword") or item.get("show_name")
            if not title:
                continue

            items.append({
                "title": title,
                "url": f"https://search.bilibili.com/all?keyword={title}",
                "position": rank,
                "platform": "bilibili",
                "hot_value": item.get("hot_id", 0),
            })

        return items
