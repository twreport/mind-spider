# -*- coding: utf-8 -*-
"""
IT之家爬虫
"""

from typing import Generator
from scrapy.http import Response

from ..base import VerticalHotSpider
from ...items import VerticalHotItem


class ITHomeSpider(VerticalHotSpider):
    """IT之家爬虫"""

    name = "ithome"
    source_name = "ithome"
    platform = "ithome"
    vertical = "tech"
    allowed_domains = ["ithome.com"]
    start_urls = ["https://www.ithome.com/"]

    custom_settings = {
        "DOWNLOAD_DELAY": 1,
    }

    def parse(self, response: Response) -> Generator:
        """解析IT之家首页"""
        try:
            # 热榜
            hot_items = response.css(".hot-list li, .rank-box li, .lst li")

            for rank, item in enumerate(hot_items[:50], start=1):
                title = item.css("a::text, .title::text").get()
                if not title:
                    continue

                url = item.css("a::attr(href)").get()
                if url and not url.startswith("http"):
                    url = f"https://www.ithome.com{url}"

                # 评论数
                comment = item.css(".comment::text, .comm::text").get()
                hot_value = self._parse_number(comment) if comment else 0

                yield self.make_vertical_item(
                    title=title.strip(),
                    url=url or "",
                    position=rank,
                    replies=hot_value,
                )

        except Exception as e:
            self.logger.error(f"解析IT之家失败: {e}")

    def _parse_number(self, text: str) -> int:
        """解析数字"""
        try:
            clean = "".join(c for c in text if c.isdigit())
            return int(clean) if clean else 0
        except (ValueError, TypeError):
            return 0
