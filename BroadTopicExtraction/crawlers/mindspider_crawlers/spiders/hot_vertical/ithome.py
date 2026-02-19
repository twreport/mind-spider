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
        """解析IT之家首页热榜"""
        try:
            # 日榜: #rank > ul.bd.order li > a
            hot_items = response.css("#rank ul.bd li a")

            for rank, item in enumerate(hot_items[:50], start=1):
                title = item.css("::text").get()
                if not title:
                    continue

                url = item.attrib.get("href", "")
                if url and not url.startswith("http"):
                    url = f"https://www.ithome.com{url}"

                yield self.make_vertical_item(
                    title=title.strip(),
                    url=url,
                    position=rank,
                )

        except Exception as e:
            self.logger.error(f"解析IT之家失败: {e}")
