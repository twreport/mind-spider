# -*- coding: utf-8 -*-
"""
AnyKnew 聚合器

基于 AnyKnew 获取科技/互联网热榜数据
网站: https://www.anyknew.com
"""

from typing import Dict, List, Any
from loguru import logger

from .base import BaseAggregator, AggregatorResult


class AnyKnewAggregator(BaseAggregator):
    """AnyKnew 聚合器"""

    name = "anyknew"
    display_name = "AnyKnew"
    base_url = "https://www.anyknew.com"

    # 支持的数据源映射
    SOURCE_MAP = {
        # 科技媒体
        "36kr": {"id": "36kr", "name": "36氪"},
        "huxiu": {"id": "huxiu", "name": "虎嗅"},
        "geekpark": {"id": "geekpark", "name": "极客公园"},
        "pingwest": {"id": "pingwest", "name": "品玩"},
        "ifanr": {"id": "ifanr", "name": "爱范儿"},
        "ithome": {"id": "ithome", "name": "IT之家"},
        "oschina": {"id": "oschina", "name": "开源中国"},
        "cnbeta": {"id": "cnbeta", "name": "cnBeta"},
        # 开发者
        "github": {"id": "github", "name": "GitHub Trending"},
        "juejin": {"id": "juejin", "name": "掘金"},
        "segmentfault": {"id": "segmentfault", "name": "思否"},
        "v2ex": {"id": "v2ex", "name": "V2EX"},
        "csdn": {"id": "csdn", "name": "CSDN"},
        "infoq": {"id": "infoq", "name": "InfoQ"},
        # 产品设计
        "producthunt": {"id": "producthunt", "name": "Product Hunt"},
        "sspai": {"id": "sspai", "name": "少数派"},
        "uisdc": {"id": "uisdc", "name": "优设"},
        # 综合
        "zhihu": {"id": "zhihu", "name": "知乎热榜"},
        "weibo": {"id": "weibo", "name": "微博热搜"},
        "toutiao": {"id": "toutiao", "name": "今日头条"},
        "baidu": {"id": "baidu", "name": "百度热搜"},
    }

    def get_supported_sources(self) -> List[str]:
        """获取支持的数据源列表"""
        return list(self.SOURCE_MAP.keys())

    def get_source_name(self, source: str) -> str:
        """获取数据源显示名称"""
        return self.SOURCE_MAP.get(source, {}).get("name", source)

    async def fetch(self, source: str, **kwargs: Any) -> AggregatorResult:
        """
        从 AnyKnew 获取数据

        Args:
            source: 数据源 ID

        Returns:
            AggregatorResult 结果对象
        """
        if source not in self.SOURCE_MAP:
            return self._make_error_result(source, f"不支持的数据源: {source}")

        source_info = self.SOURCE_MAP[source]
        # AnyKnew API 端点
        url = f"{self.base_url}/api/v1/sites/{source_info['id']}/posts"

        try:
            client = await self._get_client()
            response = await client.get(url)
            response.raise_for_status()

            data = response.json()
            items = self._parse_items(data, source)

            return self._make_success_result(source, items, raw_data=data)

        except Exception as e:
            logger.error(f"[AnyKnew] 获取 {source} 失败: {e}")
            return self._make_error_result(source, str(e))

    def _parse_items(self, data: Any, source: str) -> List[Dict]:
        """解析 API 返回数据"""
        items = []

        raw_items = []
        if isinstance(data, dict):
            raw_items = data.get("data", [])
            if not raw_items:
                raw_items = data.get("posts", [])
                if not raw_items:
                    raw_items = data.get("items", [])
        elif isinstance(data, list):
            raw_items = data

        for rank, item in enumerate(raw_items, start=1):
            parsed = self._parse_single_item(item, source, rank)
            if parsed:
                items.append(parsed)

        return items

    def _parse_single_item(self, item: Any, source: str, rank: int) -> Dict | None:
        """解析单条数据"""
        if not isinstance(item, dict):
            return None

        title = item.get("title") or item.get("name")
        if not title:
            return None

        url = item.get("url") or item.get("link") or ""

        result = {
            "title": str(title).strip(),
            "url": url,
            "position": rank,
            "platform": source,
        }

        # 热度/评论数等
        if item.get("comments"):
            result["replies"] = int(item["comments"])
        if item.get("likes"):
            result["likes"] = int(item["likes"])
        if item.get("views"):
            result["hot_value"] = int(item["views"])

        # 发布时间
        if item.get("published_at") or item.get("created_at"):
            result["publish_time"] = item.get("published_at") or item.get("created_at")

        # 描述
        if item.get("summary") or item.get("description"):
            result["description"] = item.get("summary") or item.get("description")

        return result
