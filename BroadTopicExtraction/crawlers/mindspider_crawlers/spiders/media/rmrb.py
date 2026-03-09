# -*- coding: utf-8 -*-
"""
人民日报爬虫 (迁移自 spiders/rmrb)

爬取人民日报电子版文章
已适配 2024.12 改版后的新 URL 格式
"""

import re
from datetime import date, timedelta
from typing import Generator, Any

import scrapy
from bs4 import BeautifulSoup
from scrapy.http import Response

from ..base import MediaSpider


class RmrbSpider(MediaSpider):
    """人民日报爬虫"""

    name = "rmrb"
    source_name = "rmrb"
    platform = "rmrb"
    media_type = "central"
    allowed_domains = ["people.com.cn", "paper.people.com.cn"]

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
        """生成起始请求"""
        date_int = int(self.date_str)

        # 2024.12.01 之后人民日报改版，URL 格式变化
        if date_int < 20241201:
            url = f"http://paper.people.com.cn/rmrb/html/{self.year}-{self.month}/{self.day}/nbs.D110000renmrb_01.htm"
        else:
            url = f"http://paper.people.com.cn/rmrb/pc/layout/{self.year}{self.month}/{self.day}/node_01.html"

        yield scrapy.Request(
            url,
            meta={"date": self.date_str},
            callback=self.parse_index,
        )

    def parse_index(self, response: Response) -> Generator:
        """解析报纸版面索引"""
        soup = BeautifulSoup(response.body, "lxml")

        # 查找版面导航
        swips = soup.find("div", class_="swiper-container")
        if not swips:
            self.logger.error("未找到版面导航")
            return

        swip_a = swips.find_all("a")
        for a in swip_a:
            href = a.get("href", "")
            if href:
                item_url = response.urljoin(href)
                yield scrapy.Request(
                    item_url,
                    meta={
                        "date": response.meta["date"],
                    },
                    callback=self.parse_page,
                )

    def parse_page(self, response: Response) -> Generator:
        """解析单个版面的文章列表"""
        soup = BeautifulSoup(response.body, "lxml")

        news_list_ul = soup.find("ul", class_="news-list")
        if not news_list_ul:
            return

        news_list_li = news_list_ul.find_all("li")
        for li in news_list_li:
            news_a = li.find("a")
            if not news_a:
                continue

            news_title = news_a.text.strip()
            # 跳过责编和广告
            if "本版责编" in news_title or "广告" in news_title:
                continue

            news_title = news_title.replace(" ", "")
            news_url = response.urljoin(news_a.get("href", ""))

            yield scrapy.Request(
                news_url,
                meta={
                    "date": response.meta["date"],
                    "news_title": news_title,
                },
                callback=self.parse,
            )

    def parse(self, response: Response) -> Generator:
        """解析文章详情"""
        soup = BeautifulSoup(response.body, "lxml")

        # 提取正文
        news_content_div = soup.find("div", id="ozoom")
        if not news_content_div:
            return

        news_text = news_content_div.text
        news_text = news_text.replace("　　", "\n").replace("\n\n", "")

        # 解析文章 ID
        article_id = self._parse_article_id(response.url, response.meta["date"])

        # 格式化日期
        date_str = response.meta["date"]
        publish_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"

        yield self.make_media_item(
            title=response.meta["news_title"],
            url=response.url,
            publish_date=publish_date,
            content=news_text,
            extra={
                "article_id": article_id,
            },
        )

    def _parse_article_id(self, url: str, date_str: str) -> str:
        """从 URL 解析文章 ID"""
        date_int = int(date_str)

        if date_int < 20241201:
            # 旧格式: nbs.D110000renmrb_01-01.htm
            last_words = url.split("/")[-1]
            words = last_words.split("_")
            if len(words) >= 3:
                w1 = words[1]
                w2_str_list = words[2].split("-")
                w2_str = w2_str_list[0]
                w2 = f"{int(w2_str):02d}"
                w3 = w2_str_list[1].split(".")[0] if len(w2_str_list) > 1 else "01"
                return f"{w1}-{w2}-{w3}"
        else:
            # 新格式: content_30057986.html
            last_words = url.split("/")[-1]
            words = last_words.split("_")
            if len(words) >= 2:
                return words[-1].split(".")[0]

        return ""

