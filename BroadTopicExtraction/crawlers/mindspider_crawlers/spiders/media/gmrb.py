# -*- coding: utf-8 -*-
"""
光明日报爬虫 (迁移自 spiders/gmrb)

爬取光明日报电子版文章
适配 2025+ 新版 URL 结构: /gmrb/html/layout/YYYYMM/DD/node_XX.html
"""

from datetime import date
from typing import Generator, Any
from urllib.parse import urljoin

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
        if year and month and day:
            self.target_date = date(int(year), int(month), int(day))
        else:
            self.target_date = date.today()

        self.year = f"{self.target_date.year:04d}"
        self.month = f"{self.target_date.month:02d}"
        self.day = f"{self.target_date.day:02d}"

    def start_requests(self) -> Generator:
        """生成起始请求 - 新版 URL 格式"""
        url = f"https://epaper.gmw.cn/gmrb/html/layout/{self.year}{self.month}/{self.day}/node_01.html"
        yield scrapy.Request(url, callback=self.parse_index)

    def parse_index(self, response: Response) -> Generator:
        """解析版面索引，提取所有版面链接"""
        soup = BeautifulSoup(response.body, "lxml")

        # 查找版面导航中的 node 链接
        node_links = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "node_" in href and href.endswith(".html"):
                node_links.add(href)

        for href in sorted(node_links):
            page_url = urljoin(response.url, href)
            yield scrapy.Request(page_url, callback=self.parse_page)

    def parse_page(self, response: Response) -> Generator:
        """解析单个版面的文章列表"""
        soup = BeautifulSoup(response.body, "lxml")

        # 新版结构: div.m-list-wrap > ul > li > a
        list_wrap = soup.find("div", class_="m-list-wrap")
        if not list_wrap:
            return

        for li in list_wrap.find_all("li"):
            a = li.find("a", href=True)
            if not a:
                continue

            # 标题在 <p> 标签中，可能有多个 <p>（主标题+副标题）
            paragraphs = a.find_all("p")
            title = paragraphs[0].text.strip() if paragraphs else a.text.strip()
            if not title:
                continue

            article_url = urljoin(response.url, a["href"])
            yield scrapy.Request(
                article_url,
                meta={"news_title": title},
                callback=self.parse,
            )

    def parse(self, response: Response) -> Generator:
        """解析文章详情"""
        soup = BeautifulSoup(response.body, "lxml")

        # 提取正文 - 尝试新旧两种结构
        content_div = soup.find("div", id="ozoom") or soup.find("div", class_="m-content")
        content = ""
        if content_div:
            content = content_div.text.strip()
            content = (
                content.replace("　　", "\n")
                .replace("\u2003", "")
                .replace("\xa0", " ")
            )

        publish_date = f"{self.year}-{self.month}-{self.day}"

        yield self.make_media_item(
            title=response.meta["news_title"],
            url=response.url,
            publish_date=publish_date,
            content=content,
        )
