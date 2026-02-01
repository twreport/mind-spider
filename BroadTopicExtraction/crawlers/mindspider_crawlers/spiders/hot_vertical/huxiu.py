# -*- coding: utf-8 -*-
"""
虎嗅爬虫
"""

from typing import Generator
from scrapy.http import Response

from ..base import VerticalHotSpider
from ...items import VerticalHotItem


class HuxiuSpider(VerticalHotSpider):
    """虎嗅爬虫"""

    name = "huxiu"
    source_name = "huxiu"
    platform = "huxiu"
    vertical = "tech"
    allowed_domains = ["huxiu.com"]
    start_urls = ["https://www.huxiu.com/"]

    custom_settings = {
        "DOWNLOAD_DELAY": 1,
    }

    def parse(self, response: Response) -> Generator:
        """解析虎嗅首页"""
        try:
            # 文章列表
            articles = response.css(".article-item, .mob-ctt, .article-list li")

            for rank, article in enumerate(articles[:50], start=1):
                title = article.css("h2::text, .article-title::text, a::text").get()
                if not title:
                    continue

                url = article.css("a::attr(href)").get()
                if url and not url.startswith("http"):
                    url = f"https://www.huxiu.com{url}"

                # 摘要
                summary = article.css(".article-summary::text, .mob-sub::text").get()

                yield self.make_vertical_item(
                    title=title.strip(),
                    url=url or "",
                    position=rank,
                    description=summary.strip() if summary else None,
                )

        except Exception as e:
            self.logger.error(f"解析虎嗅失败: {e}")
