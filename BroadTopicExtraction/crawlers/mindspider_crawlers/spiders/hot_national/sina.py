# -*- coding: utf-8 -*-
"""
新浪热搜爬虫 (迁移自 tang-news-spiders/sinahot)

使用新浪 API 获取热搜数据
注意：此爬虫需要有效的 API 参数，可能需要定期更新
"""

import json
from typing import Generator

from scrapy import Request
from scrapy.http import Response

from ..base import HotSearchSpider


class SinaHotSpider(HotSearchSpider):
    """新浪热搜爬虫"""

    name = "sina_hot"
    source_name = "sina_hot"
    platform = "sina"
    allowed_domains = ["sina.cn", "sina.com.cn"]

    custom_settings = {
        "DOWNLOAD_DELAY": 1,
        "DEFAULT_REQUEST_HEADERS": {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15",
            "Accept": "application/json, text/plain, */*",
        },
    }

    # 备用 API 地址（公开的热搜接口）
    API_URL = "https://newsapp.sina.cn/api/hotlist?newsId=HB-1-snhs%2Ftop_news_list-all"

    def start_requests(self) -> Generator:
        """发起 API 请求"""
        yield Request(url=self.API_URL, callback=self.parse)

    def parse(self, response: Response) -> Generator:
        """解析新浪热搜 API 响应"""
        try:
            data = response.json()
        except json.JSONDecodeError:
            self.logger.error("JSON 解析失败")
            return

        # 尝试不同的数据结构
        news_list = data.get("data", {}).get("result", [])
        if not news_list:
            # 备用结构
            news_list = data.get("data", {}).get("list", [])

        if not news_list:
            self.logger.warning("新浪热搜数据为空")
            return

        for rank, item in enumerate(news_list, start=1):
            # 支持不同的字段名
            title = item.get("text") or item.get("title") or item.get("name", "")
            if not title:
                continue

            url = item.get("link") or item.get("url", "")

            # 处理热度值（可能带"万"）
            hot_value_str = str(item.get("hotValue", 0) or item.get("hot", 0))
            hot_value_str = hot_value_str.replace("万", "0000")
            try:
                hot_value = int(float(hot_value_str))
            except ValueError:
                hot_value = 0

            yield self.make_hot_item(
                title=title,
                url=url,
                position=rank,
                hot_value=hot_value,
                extra={
                    "category": item.get("category", ""),
                },
            )

        self.logger.info(f"获取 {len(news_list)} 条新浪热搜")
