# -*- coding: utf-8 -*-
"""
Rebang 热榜聚合器

基于 rebang.today 获取各平台热榜数据
网站: https://rebang.today
"""

from typing import Dict, List, Any
from loguru import logger

from .base import BaseAggregator, AggregatorResult


class RebangAggregator(BaseAggregator):
    """Rebang 热榜聚合器"""

    name = "rebang"
    display_name = "Rebang热榜"
    base_url = "https://rebang.today"

    # 支持的数据源映射
    SOURCE_MAP = {
        # 社交媒体
        "weibo": {"path": "weibo", "name": "微博热搜"},
        "zhihu": {"path": "zhihu", "name": "知乎热榜"},
        "douyin": {"path": "douyin", "name": "抖音热搜"},
        "bilibili": {"path": "bilibili", "name": "B站热搜"},
        "kuaishou": {"path": "kuaishou", "name": "快手热搜"},
        "xiaohongshu": {"path": "xiaohongshu", "name": "小红书热搜"},
        # 搜索引擎
        "baidu": {"path": "baidu", "name": "百度热搜"},
        "sogou": {"path": "sogou", "name": "搜狗热搜"},
        "so360": {"path": "so360", "name": "360热搜"},
        "toutiao": {"path": "toutiao", "name": "头条热搜"},
        "shenma": {"path": "shenma", "name": "神马热搜"},
        # 科技
        "36kr": {"path": "36kr", "name": "36氪"},
        "ithome": {"path": "ithome", "name": "IT之家"},
        "huxiu": {"path": "huxiu", "name": "虎嗅"},
        "juejin": {"path": "juejin", "name": "掘金"},
        "csdn": {"path": "csdn", "name": "CSDN"},
        # 社区
        "tieba": {"path": "tieba", "name": "百度贴吧"},
        "douban": {"path": "douban", "name": "豆瓣"},
        "hupu": {"path": "hupu", "name": "虎扑"},
        # 新闻
        "thepaper": {"path": "thepaper", "name": "澎湃新闻"},
        "guancha": {"path": "guancha", "name": "观察者网"},
        "ifeng": {"path": "ifeng", "name": "凤凰网"},
    }

    def get_supported_sources(self) -> List[str]:
        """获取支持的数据源列表"""
        return list(self.SOURCE_MAP.keys())

    def get_source_name(self, source: str) -> str:
        """获取数据源显示名称"""
        return self.SOURCE_MAP.get(source, {}).get("name", source)

    async def fetch(self, source: str, **kwargs: Any) -> AggregatorResult:
        """
        从 Rebang 获取数据

        Args:
            source: 数据源 ID

        Returns:
            AggregatorResult 结果对象
        """
        if source not in self.SOURCE_MAP:
            return self._make_error_result(source, f"不支持的数据源: {source}")

        source_info = self.SOURCE_MAP[source]
        url = f"{self.base_url}/api/{source_info['path']}"

        try:
            client = await self._get_client()
            response = await client.get(url)
            response.raise_for_status()

            data = response.json()
            items = self._parse_items(data, source)

            return self._make_success_result(source, items, raw_data=data)

        except Exception as e:
            logger.error(f"[Rebang] 获取 {source} 失败: {e}")
            return self._make_error_result(source, str(e))

    def _parse_items(self, data: Any, source: str) -> List[Dict]:
        """解析 API 返回数据"""
        items = []

        raw_items = []
        if isinstance(data, dict):
            raw_items = data.get("data", [])
            if not raw_items:
                raw_items = data.get("items", [])
                if not raw_items:
                    raw_items = data.get("list", [])
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

        title = item.get("title") or item.get("name") or item.get("keyword")
        if not title:
            return None

        url = item.get("url") or item.get("link") or ""

        result = {
            "title": str(title).strip(),
            "url": url,
            "position": rank,
            "platform": source,
        }

        # 热度值
        hot_value = (
            item.get("hot")
            or item.get("hotValue")
            or item.get("heat")
            or item.get("index")
        )
        if hot_value is not None:
            result["hot_value"] = self._parse_hot_value(hot_value)

        # 标签
        if item.get("tag") or item.get("label"):
            result["category"] = item.get("tag") or item.get("label")

        return result

    def _parse_hot_value(self, value: Any) -> int:
        """解析热度值"""
        try:
            hot_str = str(value)
            if "万" in hot_str:
                return int(float(hot_str.replace("万", "")) * 10000)
            elif "亿" in hot_str:
                return int(float(hot_str.replace("亿", "")) * 100000000)
            else:
                clean = "".join(c for c in hot_str if c.isdigit() or c == ".")
                return int(float(clean)) if clean else 0
        except (ValueError, TypeError):
            return 0
