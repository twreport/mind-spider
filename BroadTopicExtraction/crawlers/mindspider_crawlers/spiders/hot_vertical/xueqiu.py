# -*- coding: utf-8 -*-
"""
雪球爬虫
"""

from typing import Generator
from scrapy.http import Response

from ..base import VerticalHotSpider
from ...items import VerticalHotItem


class XueqiuSpider(VerticalHotSpider):
    """雪球爬虫"""

    name = "xueqiu"
    source_name = "xueqiu"
    platform = "xueqiu"
    vertical = "finance"
    allowed_domains = ["xueqiu.com"]
    start_urls = ["https://xueqiu.com/statuses/hot/listV2.json?since_id=-1&max_id=-1&size=50"]

    custom_settings = {
        "DOWNLOAD_DELAY": 1,
        "DEFAULT_REQUEST_HEADERS": {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://xueqiu.com/",
        },
    }

    def parse(self, response: Response) -> Generator:
        """解析雪球热帖 API 响应"""
        try:
            data = response.json()
            items = data.get("data", [])

            for rank, item in enumerate(items, start=1):
                original = item.get("original_status", {}) or item

                title = original.get("title") or original.get("description", "")[:50]
                if not title:
                    continue

                # 构建链接
                status_id = original.get("id")
                user_id = original.get("user_id")
                url = f"https://xueqiu.com/{user_id}/{status_id}" if status_id and user_id else ""

                yield self.make_vertical_item(
                    title=title.strip(),
                    url=url,
                    position=rank,
                    hot_value=original.get("view_count", 0),
                    likes=original.get("like_count", 0),
                    replies=original.get("reply_count", 0),
                    author=original.get("user", {}).get("screen_name"),
                    category="finance",
                )

        except Exception as e:
            self.logger.error(f"解析雪球失败: {e}")
