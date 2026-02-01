# -*- coding: utf-8 -*-
"""
今日头条热搜爬虫 (迁移自 tang-news-spiders/toutiaotop)

使用头条 API 获取热搜数据
"""

import json
import time
from typing import Generator

from scrapy import Request
from scrapy.http import Response

from ..base import HotSearchSpider


class ToutiaoHotSpider(HotSearchSpider):
    """今日头条热搜爬虫"""

    name = "toutiao_hot"
    source_name = "toutiao_hot"
    platform = "toutiao"
    allowed_domains = ["toutiao.com"]

    custom_settings = {
        "DOWNLOAD_DELAY": 1,
        "DEFAULT_REQUEST_HEADERS": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://www.toutiao.com/",
        },
    }

    def start_requests(self) -> Generator:
        """构建带时间戳的请求"""
        url = f"https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc&_signature={int(time.time())}"
        yield Request(url=url, callback=self.parse)

    def parse(self, response: Response) -> Generator:
        """解析头条热搜 API 响应"""
        try:
            data = response.json()
        except json.JSONDecodeError:
            self.logger.error("JSON 解析失败")
            return

        res_data = data.get("data", [])
        if not res_data:
            self.logger.warning("头条热搜数据为空")
            return

        for rank, item in enumerate(res_data, start=1):
            title = item.get("Title")
            if not title:
                continue

            hot_value = item.get("HotValue", 0)
            url = item.get("Url", "")

            yield self.make_hot_item(
                title=title,
                url=url,
                position=rank,
                hot_value=int(hot_value) if hot_value else 0,
                category=item.get("Label", ""),
                image=item.get("Image", ""),
                extra={
                    "cluster_id": item.get("ClusterId"),
                    "cluster_type": item.get("ClusterType"),
                },
            )

        self.logger.info(f"获取 {len(res_data)} 条头条热搜")
