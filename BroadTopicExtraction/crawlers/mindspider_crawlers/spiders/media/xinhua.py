# -*- coding: utf-8 -*-
"""
新华网爬虫
"""

from typing import Generator
from scrapy.http import Response

from ..base import MediaSpider
from ...items import MediaItem


class XinhuaSpider(MediaSpider):
    """新华网爬虫"""

    name = "xinhua"
    source_name = "xinhua"
    platform = "xinhua"
    media_type = "central"
    allowed_domains = ["xinhuanet.com", "news.cn"]
    start_urls = ["http://www.news.cn/"]

    custom_settings = {
        "DOWNLOAD_DELAY": 1,
    }

    def parse(self, response: Response) -> Generator:
        """解析新华网首页"""
        try:
            # 新闻列表
            news_items = response.css(".domPC li a, .tit a, .news-item a")

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
                    url = f"http://www.news.cn{url}"

                yield self.make_media_item(
                    title=title.strip(),
                    url=url,
                    media_type="central",
                )

                if len(seen_urls) >= 50:
                    break

        except Exception as e:
            self.logger.error(f"解析新华网失败: {e}")
