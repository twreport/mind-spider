# -*- coding: utf-8 -*-
"""
虎扑热榜爬虫
"""

from typing import Generator
from scrapy.http import Response

from ..base import HotSearchSpider
from ...items import HotSearchItem


class HupuHotSpider(HotSearchSpider):
    """虎扑热榜爬虫"""

    name = "hupu_hot"
    source_name = "hupu_hot"
    platform = "hupu"
    allowed_domains = ["hupu.com"]
    start_urls = ["https://bbs.hupu.com/all-gambia"]

    custom_settings = {
        "DOWNLOAD_DELAY": 1,
    }

    def parse(self, response: Response) -> Generator:
        """解析虎扑热榜页面"""
        try:
            # 热帖列表
            posts = response.css(".list-item, .bbs-sl-web-post-layout")

            for rank, post in enumerate(posts[:50], start=1):
                # 标题
                title = post.css(".post-title::text, a.p-title::text").get()
                if not title:
                    title = post.css("a::text").get()
                if not title:
                    continue

                # 链接
                url = post.css("a::attr(href)").get()
                if url and not url.startswith("http"):
                    url = f"https://bbs.hupu.com{url}"

                # 回复数/热度
                replies = post.css(".post-reply::text, .reply-num::text").get()
                hot_value = self._parse_number(replies) if replies else 0

                yield self.make_hot_item(
                    title=title.strip(),
                    url=url or "",
                    position=rank,
                    hot_value=hot_value,
                    category="sports",
                )

        except Exception as e:
            self.logger.error(f"解析虎扑热榜失败: {e}")

    def _parse_number(self, text: str) -> int:
        """解析数字"""
        try:
            clean = "".join(c for c in text if c.isdigit())
            return int(clean) if clean else 0
        except (ValueError, TypeError):
            return 0
