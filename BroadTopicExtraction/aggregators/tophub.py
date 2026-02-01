# -*- coding: utf-8 -*-
"""
今日热榜 (tophub.today) 聚合器

基于今日热榜获取各平台热搜数据
这是目前最稳定的聚合源之一

测试状态: ✅ 可用
"""

from typing import Dict, List, Any
from loguru import logger

from .base import BaseAggregator, AggregatorResult


class TopHubAggregator(BaseAggregator):
    """今日热榜聚合器 - 推荐使用"""

    name = "tophub"
    display_name = "今日热榜"
    base_url = "https://tophub.today"

    # 节点 ID 映射 (已验证可用)
    SOURCE_MAP = {
        # 社交媒体 - 已验证
        "weibo": {"node_id": "KqndgxeLl9", "name": "微博热搜"},
        "zhihu": {"node_id": "mproPpoq6O", "name": "知乎热榜"},
        "baidu": {"node_id": "Jb0vmloB1G", "name": "百度热搜"},
        "toutiao": {"node_id": "x9ozB4KG8m", "name": "今日头条"},
        "douyin": {"node_id": "DpQvNABoNE", "name": "抖音热搜"},
        "bilibili": {"node_id": "74KvxwokxM", "name": "B站热搜"},
        # 科技
        "36kr": {"node_id": "Q1Vd5Ko85R", "name": "36氪"},
        "ithome": {"node_id": "Y2KeDGQdNP", "name": "IT之家"},
        "huxiu": {"node_id": "5VaobgvAj1", "name": "虎嗅"},
        # 其他
        "douban-movie": {"node_id": "NKGoRAzel6", "name": "豆瓣电影"},
        "github": {"node_id": "Wdz5zaLYo3", "name": "GitHub"},
    }

    def get_supported_sources(self) -> List[str]:
        """获取支持的数据源列表"""
        return list(self.SOURCE_MAP.keys())

    def get_source_name(self, source: str) -> str:
        """获取数据源显示名称"""
        return self.SOURCE_MAP.get(source, {}).get("name", source)

    async def fetch(self, source: str, **kwargs: Any) -> AggregatorResult:
        """
        从今日热榜 API 获取数据

        Args:
            source: 数据源 ID

        Returns:
            AggregatorResult 结果对象
        """
        if source not in self.SOURCE_MAP:
            return self._make_error_result(source, f"不支持的数据源: {source}")

        source_info = self.SOURCE_MAP[source]
        node_id = source_info["node_id"]

        # 今日热榜 API 端点
        url = f"{self.base_url}/api/nodes/{node_id}"

        try:
            client = await self._get_client()
            response = await client.get(url)
            response.raise_for_status()

            data = response.json()

            # 检查 API 返回状态
            if data.get("error"):
                return self._make_error_result(source, data.get("msg", "API error"))

            # 解析数据
            items = self._parse_items(data, source)

            return self._make_success_result(source, items, raw_data=data)

        except Exception as e:
            logger.error(f"[TopHub] 获取 {source} 失败: {e}")
            return self._make_error_result(source, str(e))

    def _parse_items(self, data: Any, source: str) -> List[Dict]:
        """解析 API 返回数据"""
        items = []

        # 今日热榜 API 返回格式
        raw_items = []
        if isinstance(data, dict):
            raw_items = data.get("data", {}).get("items", [])
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

        # 热度值
        hot_value = item.get("extra", {}).get("hot") or item.get("hot")
        if hot_value is not None:
            try:
                # 处理带单位的热度值 (如 "123万")
                hot_str = str(hot_value)
                if "万" in hot_str:
                    result["hot_value"] = int(float(hot_str.replace("万", "")) * 10000)
                elif "亿" in hot_str:
                    result["hot_value"] = int(float(hot_str.replace("亿", "")) * 100000000)
                else:
                    result["hot_value"] = int(float(hot_str))
            except (ValueError, TypeError):
                pass

        return result
