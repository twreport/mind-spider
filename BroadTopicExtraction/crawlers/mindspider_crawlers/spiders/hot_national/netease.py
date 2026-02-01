# -*- coding: utf-8 -*-
"""
网易新闻热搜爬虫 (迁移自 tang-news-spiders/neteasetop)

使用网易新闻 API 获取热搜数据
"""

import json
from typing import Generator

from scrapy import Request
from scrapy.http import Response

from ..base import HotSearchSpider


class NeteaseHotSpider(HotSearchSpider):
    """网易新闻热搜爬虫"""

    name = "netease_hot"
    source_name = "netease_hot"
    platform = "netease"
    allowed_domains = ["163.com", "m.163.com", "3g.163.com"]

    custom_settings = {
        "DOWNLOAD_DELAY": 1,
        "DEFAULT_REQUEST_HEADERS": {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://3g.163.com/",
        },
    }

    def start_requests(self) -> Generator:
        """发起 API 请求"""
        url = "https://gw.m.163.com/gentie-web/api/v2/products/a2869674571f77b5a0867c3d71db5856/rankDocs/all/list?ibc=newsapph5&limit=50"
        yield Request(url=url, callback=self.parse)

    def parse(self, response: Response) -> Generator:
        """解析网易新闻热搜 API 响应"""
        try:
            data = response.json()
        except json.JSONDecodeError:
            self.logger.error("JSON 解析失败")
            return

        news_list = data.get("data", {}).get("cmtDocs", [])
        if not news_list:
            self.logger.warning("网易新闻数据为空")
            return

        for rank, item in enumerate(news_list, start=1):
            title = item.get("doc_title")
            if not title:
                continue

            doc_id = item.get("docId", "")
            url = f"https://3g.163.com/news/article/{doc_id}.html" if doc_id else ""
            hot_value = item.get("hotScore", 0)

            yield self.make_hot_item(
                title=title,
                url=url,
                position=rank,
                hot_value=int(hot_value) if hot_value else 0,
                description=item.get("wondCmtContent", ""),
                image=item.get("doc_image", ""),
                extra={
                    "source": item.get("source", ""),
                    "doc_id": doc_id,
                },
            )

        self.logger.info(f"获取 {len(news_list)} 条网易热搜")
