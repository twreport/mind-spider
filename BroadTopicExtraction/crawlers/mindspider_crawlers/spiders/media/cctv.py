# -*- coding: utf-8 -*-
"""
央视新闻爬虫
"""

from typing import Generator
from scrapy.http import Response

from ..base import MediaSpider
from ...items import MediaItem


class CCTVSpider(MediaSpider):
    """央视新闻爬虫"""

    name = "cctv"
    source_name = "cctv"
    platform = "cctv"
    media_type = "central"
    allowed_domains = ["cctv.com"]
    start_urls = ["https://news.cctv.com/"]

    custom_settings = {
        "DOWNLOAD_DELAY": 1,
    }

    def parse(self, response: Response) -> Generator:
        """解析央视新闻首页"""
        try:
            news_items = response.css(".title a, .text a, .con a")

            seen_urls = set()
            for item in news_items:
                title = item.css("::text").get()
                if not title or len(title.strip()) < 5:
                    continue

                url = item.css("::attr(href)").get()
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)

                if not url.startswith("http"):
                    url = f"https://news.cctv.com{url}"

                yield self.make_media_item(
                    title=title.strip(),
                    url=url,
                    media_type="central",
                )

                if len(seen_urls) >= 50:
                    break

        except Exception as e:
            self.logger.error(f"解析央视新闻失败: {e}")
