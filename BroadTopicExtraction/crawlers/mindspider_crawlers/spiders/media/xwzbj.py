# -*- coding: utf-8 -*-
"""
新闻直播间爬虫 (迁移自 spiders/xwzbj)

爬取央视新闻直播间节目
"""

import json
from datetime import date
from typing import Generator, Any

import scrapy
from scrapy.http import Response

from ..base import MediaSpider


class XwzbjSpider(MediaSpider):
    """新闻直播间爬虫"""

    name = "xwzbj"
    source_name = "xwzbj"
    platform = "xwzbj"
    media_type = "central"
    allowed_domains = ["cctv.com", "api.cntv.cn"]

    custom_settings = {
        "DOWNLOAD_DELAY": 1,
    }

    def __init__(self, year=None, month=None, day=None, *args: Any, **kwargs: Any):
        super().__init__(*args, **kwargs)
        # 支持指定日期，默认为今天
        if year and month and day:
            self.target_date = date(int(year), int(month), int(day))
        else:
            self.target_date = date.today()

        self.year = f"{self.target_date.year:04d}"
        self.month = f"{self.target_date.month:02d}"
        self.day = f"{self.target_date.day:02d}"
        self.date_str = f"{self.year}{self.month}{self.day}"

    def start_requests(self) -> Generator:
        """生成起始请求 - 分页获取"""
        # 新闻直播间栏目 ID
        column_id = "TOPC1451559129520755"

        for page in range(1, 20):
            url = (
                f"https://api.cntv.cn/NewVideo/getVideoListByColumn"
                f"?id={column_id}&n=100&sort=desc&p={page}"
                f"&bd={self.date_str}&mode=2&serviceId=tvcctv&cb=cb"
            )
            yield scrapy.Request(
                url,
                meta={"date": self.date_str, "page": page},
                callback=self.parse,
            )

    def parse(self, response: Response) -> Generator:
        """解析 API 响应"""
        try:
            # 去掉 JSONP 包装: cb({...})
            body = response.body.decode("utf-8")
            json_str = body[3:-1] if body.startswith("cb(") else body
            data = json.loads(json_str)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            self.logger.error(f"JSON 解析失败: {e}")
            return

        news_list = data.get("data", {}).get("list", [])
        if not news_list:
            return

        # 格式化日期
        date_str = response.meta["date"]
        publish_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"

        for news in news_list:
            title = news.get("title", "")
            if not title:
                continue

            # 跳过节目标题
            if "《新闻直播间》" in title:
                continue

            url = news.get("url", "")

            yield self.make_media_item(
                title=title,
                url=url,
                publish_date=publish_date,
                extra={
                    "video_id": news.get("id", ""),
                    "brief": news.get("brief", ""),
                    "image": news.get("image", ""),
                    "length": news.get("length", ""),
                },
            )
