# -*- coding: utf-8 -*-
"""
财联社爬虫
"""

from typing import Generator
from scrapy.http import Response

from ..base import VerticalHotSpider
from ...items import VerticalHotItem


class CLSSpider(VerticalHotSpider):
    """财联社爬虫"""

    name = "cls"
    source_name = "cls"
    platform = "cls"
    vertical = "finance"
    allowed_domains = ["cls.cn"]
    start_urls = ["https://www.cls.cn/telegraph"]

    custom_settings = {
        "DOWNLOAD_DELAY": 1,
    }

    def parse(self, response: Response) -> Generator:
        """解析财联社电报页面"""
        try:
            # 电报列表
            items = response.css(".telegraph-item, .telegraph-content-box")

            for rank, item in enumerate(items[:50], start=1):
                # 电报内容
                content = item.css(".telegraph-content::text, .content::text").get()
                if not content:
                    continue

                # 标题取前50字
                title = content.strip()[:50]
                if len(content.strip()) > 50:
                    title += "..."

                # 时间
                time_text = item.css(".telegraph-time::text, .time::text").get()

                # 链接
                url = item.css("a::attr(href)").get()
                if url and not url.startswith("http"):
                    url = f"https://www.cls.cn{url}"

                yield self.make_vertical_item(
                    title=title,
                    url=url or "",
                    position=rank,
                    description=content.strip(),
                    category="finance",
                )

        except Exception as e:
            self.logger.error(f"解析财联社失败: {e}")
