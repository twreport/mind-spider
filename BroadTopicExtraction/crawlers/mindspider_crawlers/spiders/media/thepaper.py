# -*- coding: utf-8 -*-
"""
澎湃新闻爬虫
"""

from typing import Generator
from scrapy.http import Response

from ..base import MediaSpider
from ...items import MediaItem


class ThePaperSpider(MediaSpider):
    """澎湃新闻爬虫"""

    name = "thepaper"
    source_name = "thepaper"
    platform = "thepaper"
    media_type = "central"
    allowed_domains = ["thepaper.cn"]
    start_urls = ["https://www.thepaper.cn/"]

    custom_settings = {
        "DOWNLOAD_DELAY": 1,
    }

    def parse(self, response: Response) -> Generator:
        """解析澎湃新闻首页"""
        try:
            # 热门新闻
            news_items = response.css(".news_li, .newsbox, .index_hot li")

            for item in news_items[:50]:
                title = item.css("h2::text, a::text, .news_title::text").get()
                if not title:
                    continue

                url = item.css("a::attr(href)").get()
                if url and not url.startswith("http"):
                    url = f"https://www.thepaper.cn{url}"

                yield self.make_media_item(
                    title=title.strip(),
                    url=url or "",
                    media_type="central",
                )

        except Exception as e:
            self.logger.error(f"解析澎湃新闻失败: {e}")
