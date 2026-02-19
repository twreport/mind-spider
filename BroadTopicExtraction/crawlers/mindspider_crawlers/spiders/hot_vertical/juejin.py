# -*- coding: utf-8 -*-
"""
掘金爬虫
"""

from typing import Generator
from scrapy.http import Response, Request
import json

from ..base import VerticalHotSpider
from ...items import VerticalHotItem


class JuejinSpider(VerticalHotSpider):
    """掘金爬虫"""

    name = "juejin"
    source_name = "juejin"
    platform = "juejin"
    vertical = "tech"
    allowed_domains = ["juejin.cn"]

    custom_settings = {
        "DOWNLOAD_DELAY": 1,
    }

    def start_requests(self):
        """发起 API 请求"""
        url = "https://api.juejin.cn/recommend_api/v1/article/recommend_all_feed"
        payload = {
            "id_type": 2,
            "sort_type": 3,  # 热门
            "cursor": "0",
            "limit": 50,
        }
        yield Request(
            url=url,
            method="POST",
            body=json.dumps(payload),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            },
            callback=self.parse,
        )

    def parse(self, response: Response) -> Generator:
        """解析掘金 API 响应"""
        try:
            data = response.json()
            if data.get("err_no") != 0:
                self.logger.error(f"掘金 API 返回错误: {data.get('err_msg')}")
                return

            items = data.get("data", [])

            for rank, item in enumerate(items, start=1):
                # API 返回结构: data[i].item_info.article_info
                info = item.get("item_info", item)
                article = info.get("article_info", {})
                author = info.get("author_user_info", {})

                title = article.get("title", "")
                if not title:
                    continue

                article_id = article.get("article_id", "")
                url = f"https://juejin.cn/post/{article_id}" if article_id else ""

                yield self.make_vertical_item(
                    title=title,
                    url=url,
                    position=rank,
                    hot_value=article.get("view_count", 0),
                    likes=article.get("digg_count", 0),
                    replies=article.get("comment_count", 0),
                    author=author.get("user_name"),
                    description=article.get("brief_content"),
                )

        except Exception as e:
            self.logger.error(f"解析掘金失败: {e}")
