# -*- coding: utf-8 -*-
"""
36氪爬虫
"""

from typing import Generator
from scrapy.http import Response

from ..base import VerticalHotSpider
from ...items import VerticalHotItem


class Kr36Spider(VerticalHotSpider):
    """36氪爬虫"""

    name = "36kr"
    source_name = "36kr"
    platform = "36kr"
    vertical = "tech"
    allowed_domains = ["36kr.com"]
    start_urls = ["https://36kr.com/newsflashes"]

    custom_settings = {
        "DOWNLOAD_DELAY": 1,
    }

    def parse(self, response: Response) -> Generator:
        """解析36氪快讯页面"""
        try:
            # 快讯列表
            items = response.css(".newsflash-item, .flow-item")

            for rank, item in enumerate(items[:50], start=1):
                title = item.css(".item-title::text, a::text").get()
                if not title:
                    continue

                url = item.css("a::attr(href)").get()
                if url and not url.startswith("http"):
                    url = f"https://36kr.com{url}"

                # 时间
                time_text = item.css(".time::text, .item-time::text").get()

                yield self.make_vertical_item(
                    title=title.strip(),
                    url=url or "",
                    position=rank,
                    description=time_text,
                )

        except Exception as e:
            self.logger.error(f"解析36氪失败: {e}")
