# -*- coding: utf-8 -*-
"""
新华每日电讯爬虫 (迁移自 spiders/mrdx)

爬取新华每日电讯电子版文章
"""

import re
from datetime import date
from typing import Generator, Any

import scrapy
from bs4 import BeautifulSoup
from scrapy.http import Response

from ..base import MediaSpider


class MrdxSpider(MediaSpider):
    """新华每日电讯爬虫"""

    name = "mrdx"
    source_name = "mrdx"
    platform = "mrdx"
    media_type = "central"
    allowed_domains = ["mrdx.cn"]

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
        base_url = f"http://mrdx.cn/content/{self.date_str}/"
        url = f"{base_url}Page01DK.htm"

        yield scrapy.Request(
            url,
            meta={"date": self.date_str, "base_url": base_url},
            callback=self.parse_index,
        )

    def parse_index(self, response: Response) -> Generator:
        """解析报纸版面索引"""
        soup = BeautifulSoup(response.body, "lxml")

        listdaohang = soup.find("div", class_="listdaohang")
        if not listdaohang:
            self.logger.error("未找到版面导航")
            return

        ul_list = listdaohang.find_all("ul")
        for ul in ul_list:
            lis = ul.find_all("li")
            for li in lis:
                a = li.find("a")
                if a and a.get("daoxiang"):
                    title = a.get_text().strip()
                    daoxiang = response.meta["base_url"] + a["daoxiang"]
                    yield scrapy.Request(
                        daoxiang,
                        meta={
                            "date": response.meta["date"],
                            "title": title,
                            "ext_url": a["daoxiang"],
                        },
                        callback=self.parse,
                    )

    def parse(self, response: Response) -> Generator:
        """解析文章详情"""
        soup = BeautifulSoup(response.body, "lxml")
        table = soup.find("table")

        full_title = response.meta.get("title", "")
        content = ""

        if table is not None:
            # 从 table 中提取标题
            trs = table.find_all("tr")
            title_parts = []
            for tr in trs:
                divs = tr.find_all("div")
                for div in divs:
                    text = div.get_text().replace(" ", "").replace("\u3000", "")
                    text = text.replace("\r", "").replace("\n", "")
                    if text:
                        title_parts.append(text)
            if title_parts:
                full_title = " ".join(title_parts)

            # 提取正文
            next_step = table.next_siblings
            for step in next_step:
                if step.name is None:
                    continue
                # 如果内容太少说明不是详情 div
                if len(step.get_text()) < 10:
                    continue
                children = step.contents
                for child in children:
                    if hasattr(child, "name") and child.name == "table":
                        continue
                    text = child.get_text() if hasattr(child, "get_text") else str(child)
                    if len(text) < 2:
                        continue
                    content += text.replace(" ", "").replace("\u3000", "") + "\n"
        else:
            # 备用解析方式
            contenttext = soup.find("div", id="contenttext")
            if contenttext:
                content = contenttext.get_text().replace(" ", "").replace("\u3000", "")

        if not content:
            return

        # 解析文章 ID
        article_id = self._parse_article_id(response.meta.get("ext_url", ""))

        # 格式化日期
        date_str = response.meta["date"]
        publish_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"

        yield self.make_media_item(
            title=full_title,
            url=response.url,
            publish_date=publish_date,
            content=content,
            extra={
                "article_id": article_id,
            },
        )

    def _parse_article_id(self, ext_url: str) -> str:
        """从 URL 解析文章 ID"""
        try:
            # 用 re 提取数字
            pattern = re.compile(r"\d+")
            xinhua_id_nums = pattern.findall(ext_url)
            if xinhua_id_nums:
                page = xinhua_id_nums[0][0:2]
                num_str = xinhua_id_nums[0][3:5] if len(xinhua_id_nums[0]) >= 5 else "01"
                # 新华每日电讯从 2 开始排每一版的新闻，因此需要减 1
                num = f"{max(int(num_str) - 1, 0):02d}"
                return f"{self.date_str}-{page}-{num}"
        except (IndexError, ValueError):
            pass
        return ""
