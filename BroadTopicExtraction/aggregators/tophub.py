# -*- coding: utf-8 -*-
"""
今日热榜 (tophub.today) 聚合器

基于今日热榜 HTML 页面提取各平台热搜数据
API 已下线，改为解析服务端渲染的 HTML 表格

测试状态: ✅ 可用
"""

import re
from typing import Dict, List, Any
from loguru import logger

from .base import BaseAggregator, AggregatorResult


class TopHubAggregator(BaseAggregator):
    """今日热榜聚合器 - HTML 解析模式"""

    name = "tophub"
    display_name = "今日热榜"
    base_url = "https://tophub.today"

    # 节点 ID 映射 (已验证可用)
    SOURCE_MAP = {
        # 社交媒体
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
    }

    def get_supported_sources(self) -> List[str]:
        """获取支持的数据源列表"""
        return list(self.SOURCE_MAP.keys())

    def get_source_name(self, source: str) -> str:
        """获取数据源显示名称"""
        return self.SOURCE_MAP.get(source, {}).get("name", source)

    async def fetch(self, source: str, **kwargs: Any) -> AggregatorResult:
        """
        从今日热榜 HTML 页面提取数据

        Args:
            source: 数据源 ID

        Returns:
            AggregatorResult 结果对象
        """
        if source not in self.SOURCE_MAP:
            return self._make_error_result(source, f"不支持的数据源: {source}")

        source_info = self.SOURCE_MAP[source]
        node_id = source_info["node_id"]

        # 使用 HTML 页面端点
        url = f"{self.base_url}/n/{node_id}"

        try:
            client = await self._get_client()
            response = await client.get(
                url,
                headers={"Accept": "text/html,application/xhtml+xml"},
            )
            response.raise_for_status()

            items = self._parse_html(response.text, source)

            return self._make_success_result(source, items)

        except Exception as e:
            logger.error(f"[TopHub] 获取 {source} 失败: {e}")
            return self._make_error_result(source, str(e))

    def _parse_html(self, html: str, source: str) -> List[Dict]:
        """解析 HTML 表格中的热搜数据"""
        items = []

        # 逐个提取 <tr> 块，再从中解析排名、链接、标题
        for tr_match in re.finditer(r'<tr[^>]*>(.*?)</tr>', html, re.S):
            tr_content = tr_match.group(1)

            # 提取排名: <td...>1.</td>
            rank_match = re.search(r'<td[^>]*>\s*(\d+)\.\s*</td>', tr_content)
            if not rank_match:
                continue
            rank = int(rank_match.group(1))

            # 提取链接和标题: <a href="..." ...>标题</a>
            # 跳过 tophub 内部链接，只要外部链接
            link_match = re.search(
                r'<a\s+href="(https?://[^"]*)"[^>]*>(.*?)</a>',
                tr_content, re.S,
            )
            if not link_match:
                continue

            url = link_match.group(1)
            title_html = link_match.group(2)

            # 清理标题中的 HTML 标签
            title = re.sub(r'<[^>]+>', '', title_html).strip()
            if not title:
                continue

            result = {
                "title": title,
                "url": url,
                "position": rank,
                "platform": source,
            }

            # 提取热度值: 通常在最后一个 <td> 中
            tds = re.findall(r'<td[^>]*>(.*?)</td>', tr_content, re.S)
            if len(tds) >= 3:
                hot_text = re.sub(r'<[^>]+>', '', tds[-1]).strip()
                if hot_text:
                    result["hot_value"] = self._parse_hot_value(hot_text)

            items.append(result)

        return items

    def _parse_hot_value(self, value: str) -> int:
        """解析热度值"""
        try:
            if "万" in value:
                return int(float(value.replace("万", "")) * 10000)
            elif "亿" in value:
                return int(float(value.replace("亿", "")) * 100000000)
            else:
                clean = "".join(c for c in value if c.isdigit() or c == ".")
                return int(float(clean)) if clean else 0
        except (ValueError, TypeError):
            return 0
