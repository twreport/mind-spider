# -*- coding: utf-8 -*-
"""
新闻联播爬虫 (迁移自 spiders/xwlb)

爬取央视新闻联播文字稿
"""

from datetime import date
from typing import Generator, Any

import scrapy
from bs4 import BeautifulSoup
from scrapy.http import Response

from ..base import MediaSpider


class XwlbSpider(MediaSpider):
    """新闻联播爬虫"""

    name = "xwlb"
    source_name = "xwlb"
    platform = "xwlb"
    media_type = "central"
    allowed_domains = ["cctv.com", "tv.cctv.com"]

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
        url = f"https://tv.cctv.com/lm/xwlb/day/{self.date_str}.shtml"

        yield scrapy.Request(
            url,
            meta={"date": self.date_str},
            callback=self.parse_index,
        )

    def parse_index(self, response: Response) -> Generator:
        """解析新闻联播节目列表"""
        soup = BeautifulSoup(response.body, "lxml")

        lis = soup.find_all("li")
        news_index_headline = 0

        for i, li in enumerate(lis):
            a = li.find("a")
            if not a:
                continue

            if "title" in a.attrs:
                title = a["title"]
            else:
                title = a.text.strip()

            # 跳过节目标题
            if "《新闻联播》" in title or "新闻联播" in title:
                if news_index_headline == 0:
                    news_index_headline = i + 1
                continue

            url = a.get("href", "")
            if not url:
                continue

            is_headline = i == news_index_headline
            is_domestic_fast_news = "国内联播快讯" in title
            is_international_fast_news = "国际联播快讯" in title

            yield scrapy.Request(
                url,
                meta={
                    "title": title,
                    "date": response.meta["date"],
                    "news_index": i,
                    "is_headline": is_headline,
                    "is_domestic_fast_news": is_domestic_fast_news,
                    "is_international_fast_news": is_international_fast_news,
                },
                callback=self.parse,
            )

    def parse(self, response: Response) -> Generator:
        """解析文章详情"""
        soup = BeautifulSoup(response.body, "lxml")

        # 查找正文容器
        div = soup.find("div", class_="content_area")
        if div is None:
            div = soup.find("div", class_="cnt_bd")
        if div is None:
            return

        ps = div.find_all("p")

        # 格式化日期
        date_str = response.meta["date"]
        publish_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"

        is_domestic = response.meta.get("is_domestic_fast_news", False)
        is_international = response.meta.get("is_international_fast_news", False)

        if is_domestic or is_international:
            # 联播快讯包含多条新闻，需要拆分
            current_title = ""
            for p in ps:
                text = p.get_text().strip()
                if "（新闻联播）" in text:
                    continue

                strong = p.find("strong")
                if strong is not None:
                    current_title = text
                elif current_title and text:
                    yield self.make_media_item(
                        title=current_title,
                        url=response.url,
                        publish_date=publish_date,
                        content=text,
                        extra={
                            "news_index": response.meta.get("news_index", 0),
                            "is_headline": response.meta.get("is_headline", False),
                            "is_fast_news": True,
                            "fast_news_type": "domestic" if is_domestic else "international",
                        },
                    )
        else:
            # 普通新闻
            p_text = ""
            for p in ps:
                p_str = p.get_text().strip()
                if p_str:
                    p_text += "\n" + p_str

            if p_text:
                yield self.make_media_item(
                    title=response.meta["title"],
                    url=response.url,
                    publish_date=publish_date,
                    content=p_text.strip(),
                    extra={
                        "news_index": response.meta.get("news_index", 0),
                        "is_headline": response.meta.get("is_headline", False),
                        "is_fast_news": False,
                    },
                )
