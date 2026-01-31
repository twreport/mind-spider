# -*- coding: utf-8 -*-
"""
微博热搜爬虫

爬取微博热搜榜数据
"""

import json
from typing import Generator
from scrapy.http import Response

from ..base import HotSearchSpider


class WeiboHotSpider(HotSearchSpider):
    """微博热搜爬虫"""

    name = "weibo_hot"
    source_name = "weibo_hot"
    platform = "weibo"
    allowed_domains = ["weibo.com", "s.weibo.com"]

    # 微博热搜 API
    start_urls = [
        "https://weibo.com/ajax/side/hotSearch"
    ]

    custom_settings = {
        "DEFAULT_REQUEST_HEADERS": {
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://weibo.com/",
        }
    }

    def parse(self, response: Response) -> Generator:
        """解析微博热搜 API 响应"""
        try:
            data = json.loads(response.text)
        except json.JSONDecodeError:
            self.logger.error("JSON 解析失败")
            return

        # 检查响应状态
        if data.get("ok") != 1:
            self.logger.error(f"API 返回错误: {data}")
            return

        # 解析热搜数据
        realtime = data.get("data", {}).get("realtime", [])

        for rank, item in enumerate(realtime, start=1):
            word = item.get("word") or item.get("note")
            if not word:
                continue

            # 构建热搜 URL
            url = f"https://s.weibo.com/weibo?q=%23{word}%23"

            # 获取热度值
            hot_value = item.get("num") or item.get("raw_hot") or 0

            yield self.make_hot_item(
                title=word,
                url=url,
                position=rank,
                hot_value=int(hot_value) if hot_value else None,
                category=item.get("category"),
                extra={
                    "label_name": item.get("label_name"),
                    "icon_desc": item.get("icon_desc"),
                    "is_new": item.get("is_new"),
                },
            )
