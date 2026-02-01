# -*- coding: utf-8 -*-
"""
腾讯新闻热搜爬虫 (迁移自 tang-news-spiders/tencenttop)

使用腾讯新闻 API 获取热搜数据
"""

import json
from typing import Generator

from scrapy import Request
from scrapy.http import Response

from ..base import HotSearchSpider


class TencentHotSpider(HotSearchSpider):
    """腾讯新闻热搜爬虫"""

    name = "tencent_hot"
    source_name = "tencent_hot"
    platform = "tencent"
    allowed_domains = ["qq.com", "inews.qq.com"]

    custom_settings = {
        "DOWNLOAD_DELAY": 1,
        "DEFAULT_REQUEST_HEADERS": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://news.qq.com/",
        },
    }

    def start_requests(self) -> Generator:
        """发起 API 请求"""
        url = "https://r.inews.qq.com/gw/event/hot_ranking_list"
        yield Request(url=url, callback=self.parse)

    def parse(self, response: Response) -> Generator:
        """解析腾讯新闻热搜 API 响应"""
        try:
            data = response.json()
        except json.JSONDecodeError:
            self.logger.error("JSON 解析失败")
            return

        try:
            news_list = data.get("idlist", [{}])[0].get("newslist", [])
            # 去掉第一个提示 item
            if news_list and len(news_list) > 1:
                news_list = news_list[1:]
        except (IndexError, KeyError):
            self.logger.error("腾讯新闻数据格式异常")
            return

        for rank, item in enumerate(news_list, start=1):
            title = item.get("title")
            if not title:
                continue

            url = item.get("url", "")
            hot_value = item.get("readCount", 0)

            yield self.make_hot_item(
                title=title,
                url=url,
                position=rank,
                hot_value=int(hot_value) if hot_value else 0,
                description=item.get("abstract", ""),
                image=item.get("miniProShareImage", ""),
                extra={
                    "source": item.get("source", ""),
                },
            )

        self.logger.info(f"获取 {len(news_list)} 条腾讯热搜")
