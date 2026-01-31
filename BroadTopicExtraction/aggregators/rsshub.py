# -*- coding: utf-8 -*-
"""
RSSHub 聚合器

基于 RSSHub 获取 RSS 订阅数据
RSSHub 文档: https://docs.rsshub.app/
"""

from typing import Dict, List, Any, Optional
from datetime import datetime
import xml.etree.ElementTree as ET
from loguru import logger

from .base import BaseAggregator, AggregatorResult


class RSSHubAggregator(BaseAggregator):
    """RSSHub 聚合器"""

    name = "rsshub"
    display_name = "RSSHub"
    base_url = "https://rsshub.app"  # 默认公共实例

    # 常用 RSS 路由
    SOURCE_MAP = {
        # 传统媒体
        "people/paper/rmrb": {"name": "人民日报"},
        "xinhuanet/news": {"name": "新华社"},
        "cctv/news": {"name": "央视新闻"},
        "gmw/news": {"name": "光明日报"},
        # 财经媒体
        "caixin/latest": {"name": "财新网"},
        "yicai/news": {"name": "第一财经"},
        "wallstreetcn/news": {"name": "华尔街见闻"},
        "cls/telegraph": {"name": "财联社电报"},
        # 科技媒体
        "36kr/newsflashes": {"name": "36氪快讯"},
        "huxiu/article": {"name": "虎嗅"},
        "sspai/matrix": {"name": "少数派"},
        "ithome/news": {"name": "IT之家"},
        # 社区
        "zhihu/hot": {"name": "知乎热榜"},
        "weibo/search/hot": {"name": "微博热搜"},
        "bilibili/ranking/0/3": {"name": "B站全站榜"},
        "douban/movie/playing": {"name": "豆瓣正在热映"},
        # GitHub
        "github/trending/daily": {"name": "GitHub Trending"},
    }

    def __init__(
        self,
        base_url: Optional[str] = None,
        access_key: Optional[str] = None,
        timeout: float = 30.0,
    ):
        """
        初始化 RSSHub 聚合器

        Args:
            base_url: RSSHub 实例地址，默认使用公共实例
            access_key: 访问密钥 (如果实例需要)
            timeout: 请求超时时间
        """
        super().__init__(timeout)
        if base_url:
            self.base_url = base_url.rstrip("/")
        self.access_key = access_key

    def get_supported_sources(self) -> List[str]:
        """获取支持的数据源列表"""
        return list(self.SOURCE_MAP.keys())

    def get_source_name(self, source: str) -> str:
        """获取数据源显示名称"""
        return self.SOURCE_MAP.get(source, {}).get("name", source)

    async def fetch(self, source: str, **kwargs: Any) -> AggregatorResult:
        """
        从 RSSHub 获取 RSS 数据

        Args:
            source: RSS 路由路径 (如 "people/paper/rmrb")

        Returns:
            AggregatorResult 结果对象
        """
        # 构建 URL
        url = f"{self.base_url}/{source}"
        if self.access_key:
            url = f"{url}?key={self.access_key}"

        try:
            client = await self._get_client()

            # RSS 请求需要接受 XML
            headers = {"Accept": "application/rss+xml, application/xml, text/xml"}
            response = await client.get(url, headers=headers)
            response.raise_for_status()

            # 解析 RSS XML
            items = self._parse_rss(response.text, source)

            return self._make_success_result(source, items, raw_data=response.text)

        except Exception as e:
            logger.error(f"[RSSHub] 获取 {source} 失败: {e}")
            return self._make_error_result(source, str(e))

    def _parse_rss(self, xml_content: str, source: str) -> List[Dict]:
        """
        解析 RSS XML 内容

        Args:
            xml_content: RSS XML 字符串
            source: 数据源路径

        Returns:
            标准化的数据项列表
        """
        items = []

        try:
            root = ET.fromstring(xml_content)

            # 查找所有 item 元素 (RSS 2.0)
            channel = root.find("channel")
            if channel is not None:
                item_elements = channel.findall("item")
            else:
                # Atom 格式
                item_elements = root.findall(
                    ".//{http://www.w3.org/2005/Atom}entry"
                )

            for rank, item_elem in enumerate(item_elements, start=1):
                parsed = self._parse_rss_item(item_elem, source, rank)
                if parsed:
                    items.append(parsed)

        except ET.ParseError as e:
            logger.error(f"[RSSHub] XML 解析失败: {e}")

        return items

    def _parse_rss_item(
        self, item_elem: ET.Element, source: str, rank: int
    ) -> Dict | None:
        """
        解析单个 RSS item

        Args:
            item_elem: XML item 元素
            source: 数据源路径
            rank: 排名

        Returns:
            标准化的数据项
        """
        # RSS 2.0 格式
        title_elem = item_elem.find("title")
        link_elem = item_elem.find("link")
        desc_elem = item_elem.find("description")
        pub_date_elem = item_elem.find("pubDate")
        author_elem = item_elem.find("author") or item_elem.find("dc:creator")

        # Atom 格式备选
        if title_elem is None:
            title_elem = item_elem.find("{http://www.w3.org/2005/Atom}title")
        if link_elem is None:
            link_elem = item_elem.find("{http://www.w3.org/2005/Atom}link")
            if link_elem is not None:
                link_elem = type("obj", (object,), {"text": link_elem.get("href")})()

        title = title_elem.text if title_elem is not None else None
        if not title:
            return None

        url = link_elem.text if link_elem is not None else ""
        description = desc_elem.text if desc_elem is not None else ""

        result = {
            "title": str(title).strip(),
            "url": url or "",
            "position": rank,
            "platform": source.split("/")[0],  # 取路径第一段作为平台
        }

        # 发布日期
        if pub_date_elem is not None and pub_date_elem.text:
            try:
                # 尝试解析 RFC 2822 格式
                from email.utils import parsedate_to_datetime

                pub_date = parsedate_to_datetime(pub_date_elem.text)
                result["publish_date"] = pub_date.strftime("%Y-%m-%d")
                result["publish_time"] = pub_date.isoformat()
            except Exception:
                result["publish_date"] = pub_date_elem.text

        # 描述/摘要
        if description:
            # 去除 HTML 标签
            import re

            clean_desc = re.sub(r"<[^>]+>", "", description)
            result["description"] = clean_desc[:500]  # 限制长度

        # 作者
        if author_elem is not None and author_elem.text:
            result["author"] = author_elem.text

        return result

    async def fetch_custom(self, route: str, **kwargs: Any) -> AggregatorResult:
        """
        获取自定义 RSS 路由

        Args:
            route: 完整的 RSS 路由路径

        Returns:
            AggregatorResult 结果对象
        """
        return await self.fetch(route, **kwargs)
