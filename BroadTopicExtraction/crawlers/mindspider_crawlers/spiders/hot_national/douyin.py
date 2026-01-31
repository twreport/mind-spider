# -*- coding: utf-8 -*-
"""
抖音热搜爬虫

爬取抖音热搜榜数据
"""

import json
from typing import Generator
from scrapy.http import Response

from ..base import HotSearchSpider


class DouyinHotSpider(HotSearchSpider):
    """抖音热搜爬虫"""

    name = "douyin_hot"
    source_name = "douyin_hot"
    platform = "douyin"
    allowed_domains = ["douyin.com", "www.douyin.com"]

    # 抖音热搜 API
    start_urls = [
        "https://www.douyin.com/aweme/v1/web/hot/search/list/"
    ]

    custom_settings = {
        "DEFAULT_REQUEST_HEADERS": {
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.douyin.com/",
        }
    }

    def parse(self, response: Response) -> Generator:
        """解析抖音热搜 API 响应"""
        try:
            data = json.loads(response.text)
        except json.JSONDecodeError:
            self.logger.error("JSON 解析失败")
            return

        # 检查响应状态
        if data.get("status_code") != 0:
            self.logger.error(f"API 返回错误: {data}")
            return

        # 解析热搜数据
        word_list = data.get("data", {}).get("word_list", [])

        for rank, item in enumerate(word_list, start=1):
            word = item.get("word")
            if not word:
                continue

            # 构建热搜 URL
            url = f"https://www.douyin.com/search/{word}"

            # 获取热度值
            hot_value = item.get("hot_value") or 0

            yield self.make_hot_item(
                title=word,
                url=url,
                position=rank,
                hot_value=int(hot_value) if hot_value else None,
                extra={
                    "sentence_id": item.get("sentence_id"),
                    "label": item.get("label"),
                    "word_type": item.get("word_type"),
                },
            )
