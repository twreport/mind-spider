# -*- coding: utf-8 -*-
"""
人民日报爬虫

爬取人民日报电子版文章
"""

from datetime import date
from typing import Generator
from scrapy.http import Response

from ..base import MediaSpider


class RmrbSpider(MediaSpider):
    """人民日报爬虫"""

    name = "rmrb"
    source_name = "rmrb"
    platform = "rmrb"
    media_type = "central"
    allowed_domains = ["people.com.cn", "paper.people.com.cn"]

    def start_requests(self):
        """生成起始请求 - 获取今日报纸"""
        today = date.today()
        # 人民日报电子版 URL 格式
        url = f"http://paper.people.com.cn/rmrb/html/{today.year}-{today.month:02d}/{today.day:02d}/nbs.D110000renmrb_01.htm"
        yield scrapy.Request(url, callback=self.parse_index)

    def parse_index(self, response: Response) -> Generator:
        """解析报纸版面索引"""
        # 获取所有版面链接
        page_links = response.css("div.swiper-slide a::attr(href)").getall()

        for page_link in page_links:
            page_url = response.urljoin(page_link)
            yield scrapy.Request(page_url, callback=self.parse_page)

    def parse_page(self, response: Response) -> Generator:
        """解析单个版面的文章列表"""
        # 获取版面名称
        page_name = response.css("div.list-header h2::text").get()

        # 获取文章链接
        article_links = response.css("div.news-list ul li a::attr(href)").getall()

        for link in article_links:
            article_url = response.urljoin(link)
            yield scrapy.Request(
                article_url,
                callback=self.parse,
                meta={"page_name": page_name},
            )

    def parse(self, response: Response) -> Generator:
        """解析文章详情"""
        # 提取标题
        title = response.css("div.article h1::text").get()
        if not title:
            title = response.css("h1::text").get()
        if not title:
            return

        # 提取正文
        content_parts = response.css("div.article p::text").getall()
        content = "\n".join(p.strip() for p in content_parts if p.strip())

        # 提取发布日期
        publish_date = response.css("div.article-info span::text").get()
        if not publish_date:
            # 从 URL 提取日期
            import re
            match = re.search(r"(\d{4})-(\d{2})/(\d{2})", response.url)
            if match:
                publish_date = f"{match.group(1)}-{match.group(2)}-{match.group(3)}"

        # 提取作者
        author = response.css("div.article-info .author::text").get()

        yield self.make_media_item(
            title=title.strip(),
            url=response.url,
            publish_date=publish_date,
            content=content,
            author=author,
            extra={
                "page_name": response.meta.get("page_name"),
            },
        )


# 需要导入 scrapy
import scrapy
