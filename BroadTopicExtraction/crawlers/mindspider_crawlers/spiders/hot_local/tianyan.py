# -*- coding: utf-8 -*-
"""
天眼新闻热搜爬虫 (迁移自 tang-news-spiders/tianyanhot)

爬取贵州天眼新闻热搜数据（地方热搜）
"""

import json
from typing import Generator

from scrapy import Request
from scrapy.http import Response

from ..base import LocalHotSpider


class TianyanHotSpider(LocalHotSpider):
    """天眼新闻热搜爬虫（贵州地方热搜）"""

    name = "tianyan_hot"
    source_name = "tianyan_hot"
    platform = "tianyan"
    region = "guizhou"
    allowed_domains = ["todayguizhou.com", "jgz.app.todayguizhou.com"]

    custom_settings = {
        "DOWNLOAD_DELAY": 1,
        "DEFAULT_REQUEST_HEADERS": {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15",
            "Accept": "application/json, text/plain, */*",
        },
    }

    def start_requests(self) -> Generator:
        """发起 API 请求"""
        url = "https://jgz.app.todayguizhou.com/appAPI/index.php?act=special_column&op=index&special_column_id=11515115722490"
        yield Request(url=url, callback=self.parse)

    def parse(self, response: Response) -> Generator:
        """解析天眼新闻 API 响应"""
        try:
            data = response.json()
        except json.JSONDecodeError:
            self.logger.error("JSON 解析失败")
            return

        news_list = data.get("data", {}).get("list", [])
        if not news_list:
            self.logger.warning("天眼新闻数据为空")
            return

        for rank, item in enumerate(news_list, start=1):
            title = item.get("news_title")
            if not title:
                continue

            news_id = item.get("news_id", "")
            url = f"http://jgz.app.todayguizhou.com//news/news-news_detail-news_id-{news_id}.html"
            views = item.get("news_views", 0)

            yield self.make_local_item(
                title=title,
                url=url,
                position=rank,
                hot_value=int(views) if views else 0,
                image=item.get("news_thumb", ""),
                extra={
                    "news_id": news_id,
                    "news_source": item.get("news_source", ""),
                },
            )

        self.logger.info(f"获取 {len(news_list)} 条天眼新闻热搜")
