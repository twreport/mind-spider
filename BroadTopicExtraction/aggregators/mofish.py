# -*- coding: utf-8 -*-
"""
鱼塘热榜 (mo.fish) 聚合器

[已废弃] 该服务 DNS 已失效，无法访问
保留代码供参考，但默认禁用
"""

from typing import Dict, List, Any
from loguru import logger

from .base import BaseAggregator, AggregatorResult


class MoFishAggregator(BaseAggregator):
    """鱼塘热榜聚合器 [已废弃]"""

    name = "mofish"
    display_name = "鱼塘热榜(已废弃)"
    base_url = "https://mo.fish"
    deprecated = True  # 标记为已废弃

    # 支持的数据源映射
    SOURCE_MAP = {
        # 综合热榜
        "weibo": {"path": "/hot/weibo", "name": "微博热搜"},
        "zhihu": {"path": "/hot/zhihu", "name": "知乎热榜"},
        "baidu": {"path": "/hot/baidu", "name": "百度热搜"},
        "toutiao": {"path": "/hot/toutiao", "name": "今日头条"},
        "douyin": {"path": "/hot/douyin", "name": "抖音热搜"},
        "bilibili": {"path": "/hot/bilibili", "name": "B站热搜"},
        "kuaishou": {"path": "/hot/kuaishou", "name": "快手热搜"},
        # 科技
        "36kr": {"path": "/hot/36kr", "name": "36氪"},
        "ithome": {"path": "/hot/ithome", "name": "IT之家"},
        "huxiu": {"path": "/hot/huxiu", "name": "虎嗅"},
        "juejin": {"path": "/hot/juejin", "name": "掘金"},
        "v2ex": {"path": "/hot/v2ex", "name": "V2EX"},
        "sspai": {"path": "/hot/sspai", "name": "少数派"},
        # 财经
        "wallstreetcn": {"path": "/hot/wallstreetcn", "name": "华尔街见闻"},
        "cls": {"path": "/hot/cls", "name": "财联社"},
        "eastmoney": {"path": "/hot/eastmoney", "name": "东方财富"},
        "xueqiu": {"path": "/hot/xueqiu", "name": "雪球"},
        # 社区
        "douban": {"path": "/hot/douban", "name": "豆瓣"},
        "tieba": {"path": "/hot/tieba", "name": "贴吧"},
        "hupu": {"path": "/hot/hupu", "name": "虎扑"},
        "nga": {"path": "/hot/nga", "name": "NGA"},
        # 新闻
        "thepaper": {"path": "/hot/thepaper", "name": "澎湃新闻"},
        "sina": {"path": "/hot/sina", "name": "新浪新闻"},
        "163": {"path": "/hot/163", "name": "网易新闻"},
        "qq": {"path": "/hot/qq", "name": "腾讯新闻"},
    }

    def get_supported_sources(self) -> List[str]:
        """获取支持的数据源列表"""
        return list(self.SOURCE_MAP.keys())

    def get_source_name(self, source: str) -> str:
        """获取数据源显示名称"""
        return self.SOURCE_MAP.get(source, {}).get("name", source)

    async def fetch(self, source: str, **kwargs: Any) -> AggregatorResult:
        """
        从鱼塘热榜获取数据

        Args:
            source: 数据源 ID

        Returns:
            AggregatorResult 结果对象
        """
        if source not in self.SOURCE_MAP:
            return self._make_error_result(source, f"不支持的数据源: {source}")

        source_info = self.SOURCE_MAP[source]
        # 使用 API 接口
        url = f"{self.base_url}/api{source_info['path']}"

        try:
            client = await self._get_client()
            response = await client.get(url)
            response.raise_for_status()

            data = response.json()
            items = self._parse_items(data, source)

            return self._make_success_result(source, items, raw_data=data)

        except Exception as e:
            logger.error(f"[MoFish] 获取 {source} 失败: {e}")
            return self._make_error_result(source, str(e))

    def _parse_items(self, data: Any, source: str) -> List[Dict]:
        """解析 API 返回数据"""
        items = []

        # 鱼塘热榜 API 返回格式
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

        title = item.get("title") or item.get("name") or item.get("content")
        if not title:
            return None

        url = item.get("url") or item.get("link") or item.get("href") or ""

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
            or item.get("score")
        )
        if hot_value is not None:
            result["hot_value"] = self._parse_hot_value(hot_value)

        # 额外字段
        if item.get("desc") or item.get("description"):
            result["description"] = item.get("desc") or item.get("description")

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
                # 移除非数字字符
                clean = "".join(c for c in hot_str if c.isdigit() or c == ".")
                return int(float(clean)) if clean else 0
        except (ValueError, TypeError):
            return 0
