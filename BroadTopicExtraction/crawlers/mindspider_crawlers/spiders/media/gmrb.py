# -*- coding: utf-8 -*-
"""
光明日报爬虫 (迁移自 spiders/gmrb)

爬取光明日报电子版文章
"""

from datetime import date
from typing import Generator, Any

import scrapy
from bs4 import BeautifulSoup
from scrapy.http import Response

from ..base import MediaSpider


class GmrbSpider(MediaSpider):
    """光明日报爬虫"""

    name = "gmrb"
    source_name = "gmrb"
    platform = "gmrb"
    media_type = "central"
    allowed_domains = ["gmw.cn", "epaper.gmw.cn"]

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
        base_url = f"https://epaper.gmw.cn/gmrb/html/{self.year}-{self.month}/{self.day}/"
        url = f"{base_url}nbs.D110000gmrb_01.htm"

        yield scrapy.Request(
            url,
            meta={"date": self.date_str, "base_url": base_url},
            callback=self.parse_index,
        )

    def parse_index(self, response: Response) -> Generator:
        """解析报纸版面索引"""
        soup = BeautifulSoup(response.body, "lxml")

        page_list = soup.find("div", id="pageList")
        if not page_list:
            self.logger.error("未找到版面列表")
            return

        # 查找所有带 id 属性的链接
        page_links = page_list.find_all(lambda tag: tag.name == "a" and tag.has_attr("id"))

        for a in page_links:
            href = a.get("href", "").replace("./", "")
            if href:
                item_url = response.meta["base_url"] + href + "?t=2"
                yield scrapy.Request(
                    item_url,
                    meta={
                        "date": response.meta["date"],
                        "base_url": response.meta["base_url"],
                    },
                    callback=self.parse_page,
                )

    def parse_page(self, response: Response) -> Generator:
        """解析单个版面的文章列表"""
        soup = BeautifulSoup(response.body, "lxml")

        title_list = soup.find("div", id="titleList")
        if not title_list:
            return

        news_list_ul = title_list.find("ul")
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
            news_url = response.meta["base_url"] + news_a.get("href", "")

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
        news_text = (
            news_text.replace("　　", "\n")
            .replace("\n\n", "")
            .replace("\u2003", "")
            .replace("\xa0", " ")
        )

        # 解析文章 ID
        article_id = self._parse_article_id(response.url)

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

    def _parse_article_id(self, url: str) -> str:
        """从 URL 解析文章 ID"""
        try:
            last_words = url.split("/")[-1]
            words = last_words.split("_")
            if len(words) >= 3:
                w1 = words[1]
                w2_str_list = words[2].split("-")
                w2_str = w2_str_list[0]
                w2 = f"{int(w2_str):02d}"
                w3 = w2_str_list[1].split(".")[0] if len(w2_str_list) > 1 else "01"
                return f"{w1}-{w2}-{w3}"
        except (IndexError, ValueError):
            pass
        return ""
