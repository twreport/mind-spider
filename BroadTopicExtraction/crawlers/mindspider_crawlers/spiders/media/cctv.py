# -*- coding: utf-8 -*-
"""
央视新闻爬虫

首页列表 → 文章详情页 → 提取正文
"""

from typing import Generator

import scrapy
from scrapy.http import Response

from ..base import MediaSpider


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
        """解析央视新闻首页，提取文章链接后跟进详情页"""
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

                yield scrapy.Request(
                    url,
                    callback=self.parse_article,
                    meta={"title": title.strip(), "url": url},
                )

                if len(seen_urls) >= 50:
                    break

        except Exception as e:
            self.logger.error(f"解析央视新闻失败: {e}")

    def parse_article(self, response: Response) -> Generator:
        """解析文章详情页，提取正文"""
        title = response.meta["title"]
        url = response.meta["url"]

        try:
            # CCTV 文章正文容器（text_area 用于新闻文章，video_brief 用于视频页）
            content_parts = response.css(
                "div.text_area p::text, "
                "#content_body p::text, "
                "div.cnt_bd p::text, "
                "div.video_brief::text"
            ).getall()

            content = "\n".join(p.strip() for p in content_parts if p.strip())

            # 尝试提取发布时间
            publish_date = response.css(
                ".info-time::text, "
                "span.date::text, "
                ".newsTime::text"
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
            self.logger.warning(f"解析央视文章详情失败: {url} - {e}")
            yield self.make_media_item(title=title, url=url)
