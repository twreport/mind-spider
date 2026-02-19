# -*- coding: utf-8 -*-
"""
新华网爬虫

首页列表 → 文章详情页 → 提取正文
"""

from typing import Generator

import scrapy
from scrapy.http import Response

from ..base import MediaSpider


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
        """解析新华网首页，提取文章链接后跟进详情页"""
        try:
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

                yield scrapy.Request(
                    url,
                    callback=self.parse_article,
                    meta={"title": title.strip(), "url": url},
                )

                if len(seen_urls) >= 50:
                    break

        except Exception as e:
            self.logger.error(f"解析新华网失败: {e}")

    def parse_article(self, response: Response) -> Generator:
        """解析文章详情页，提取正文"""
        title = response.meta["title"]
        url = response.meta["url"]

        try:
            # 新华网文章正文容器
            content_parts = response.css(
                "#detail .detail p::text, "
                "#detailContent p::text, "
                "div.main p::text, "
                ".article p::text"
            ).getall()

            content = "\n".join(p.strip() for p in content_parts if p.strip())

            # 尝试提取发布时间
            publish_date = response.css(
                ".header-time .date::text, "
                "span.time::text, "
                ".info .time::text"
            ).get()
            if publish_date:
                publish_date = publish_date.strip()

            yield self.make_media_item(
                title=title,
                url=url,
                content=content or None,
                publish_date=publish_date,
            )

        except Exception as e:
            self.logger.warning(f"解析新华网文章详情失败: {url} - {e}")
            yield self.make_media_item(title=title, url=url)
