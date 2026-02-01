# -*- coding: utf-8 -*-
"""
B站热榜爬虫
"""

from typing import Generator
from scrapy.http import Response

from ..base import HotSearchSpider
from ...items import HotSearchItem


class BilibiliHotSpider(HotSearchSpider):
    """B站热榜爬虫"""

    name = "bilibili_hot"
    source_name = "bilibili_hot"
    platform = "bilibili"
    allowed_domains = ["bilibili.com"]
    start_urls = ["https://api.bilibili.com/x/web-interface/ranking/v2?rid=0&type=all"]

    custom_settings = {
        "DOWNLOAD_DELAY": 1,
        "DEFAULT_REQUEST_HEADERS": {
            "Accept": "application/json",
            "Referer": "https://www.bilibili.com/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        },
    }

    def parse(self, response: Response) -> Generator:
        """解析B站热榜 API 响应"""
        try:
            data = response.json()
            if data.get("code") != 0:
                self.logger.error(f"B站 API 返回错误: {data.get('message')}")
                return

            items = data.get("data", {}).get("list", [])

            for rank, item in enumerate(items, start=1):
                title = item.get("title", "")
                if not title:
                    continue

                bvid = item.get("bvid", "")
                url = f"https://www.bilibili.com/video/{bvid}" if bvid else ""

                # 播放量作为热度
                stat = item.get("stat", {})
                hot_value = stat.get("view", 0)

                yield self.make_hot_item(
                    title=title,
                    url=url,
                    position=rank,
                    hot_value=hot_value,
                    description=item.get("desc"),
                    extra={
                        "bvid": bvid,
                        "aid": item.get("aid"),
                        "danmaku": stat.get("danmaku", 0),
                        "like": stat.get("like", 0),
                        "coin": stat.get("coin", 0),
                        "author": item.get("owner", {}).get("name"),
                    },
                )

        except Exception as e:
            self.logger.error(f"解析B站热榜失败: {e}")
