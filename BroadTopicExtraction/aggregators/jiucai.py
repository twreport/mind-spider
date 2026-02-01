# -*- coding: utf-8 -*-
"""
韭菜公社 / 韭圈儿 聚合器

基于韭菜公社获取财经热榜数据
网站: https://www.jiucaigongshe.com
"""

from typing import Dict, List, Any
from loguru import logger

from .base import BaseAggregator, AggregatorResult


class JiuCaiAggregator(BaseAggregator):
    """韭菜公社聚合器"""

    name = "jiucai"
    display_name = "韭菜公社"
    base_url = "https://www.jiucaigongshe.com"

    # 支持的数据源映射
    SOURCE_MAP = {
        # 财经热榜
        "xueqiu": {"id": "xueqiu", "name": "雪球热帖"},
        "eastmoney": {"id": "eastmoney", "name": "东方财富"},
        "cls": {"id": "cls", "name": "财联社电报"},
        "wallstreetcn": {"id": "wallstreetcn", "name": "华尔街见闻"},
        "jinse": {"id": "jinse", "name": "金色财经"},
        "gelonghui": {"id": "gelonghui", "name": "格隆汇"},
        # 股票相关
        "stock_hot": {"id": "stock_hot", "name": "股票热搜"},
        "fund_hot": {"id": "fund_hot", "name": "基金热搜"},
        # 加密货币
        "crypto": {"id": "crypto", "name": "加密货币"},
        "binance": {"id": "binance", "name": "币安"},
    }

    def get_supported_sources(self) -> List[str]:
        """获取支持的数据源列表"""
        return list(self.SOURCE_MAP.keys())

    def get_source_name(self, source: str) -> str:
        """获取数据源显示名称"""
        return self.SOURCE_MAP.get(source, {}).get("name", source)

    async def fetch(self, source: str, **kwargs: Any) -> AggregatorResult:
        """
        从韭菜公社获取数据

        Args:
            source: 数据源 ID

        Returns:
            AggregatorResult 结果对象
        """
        if source not in self.SOURCE_MAP:
            return self._make_error_result(source, f"不支持的数据源: {source}")

        source_info = self.SOURCE_MAP[source]
        url = f"{self.base_url}/api/hot/{source_info['id']}"

        try:
            client = await self._get_client()
            response = await client.get(url)
            response.raise_for_status()

            data = response.json()
            items = self._parse_items(data, source)

            return self._make_success_result(source, items, raw_data=data)

        except Exception as e:
            logger.error(f"[JiuCai] 获取 {source} 失败: {e}")
            return self._make_error_result(source, str(e))

    def _parse_items(self, data: Any, source: str) -> List[Dict]:
        """解析 API 返回数据"""
        items = []

        raw_items = []
        if isinstance(data, dict):
            raw_items = data.get("data", [])
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

        title = item.get("title") or item.get("content") or item.get("text")
        if not title:
            return None

        url = item.get("url") or item.get("link") or ""

        result = {
            "title": str(title).strip()[:200],  # 财经内容可能较长，截断
            "url": url,
            "position": rank,
            "platform": source,
            "category": "finance",
        }

        # 财经特有字段
        if item.get("stock_code"):
            result["extra"] = {"stock_code": item["stock_code"]}
        if item.get("change"):
            result["extra"] = result.get("extra", {})
            result["extra"]["change"] = item["change"]

        # 热度
        hot_value = item.get("hot") or item.get("read_count") or item.get("view")
        if hot_value:
            result["hot_value"] = self._parse_hot_value(hot_value)

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
