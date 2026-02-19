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
            items = response.css(".telegraph-content-box")

            for rank, item in enumerate(items[:50], start=1):
                # 标题取 <strong> 标签
                title = item.css("strong::text").get()
                if not title:
                    # 无 strong 时取整段文字前50字
                    content = item.css("div::text").get()
                    if not content:
                        continue
                    title = content.strip()[:50]
                    if len(content.strip()) > 50:
                        title += "..."

                # 完整内容
                content = item.css("span.c-34304b div::text").get()

                # 时间
                time_text = item.css(".telegraph-time-box::text").get()

                yield self.make_vertical_item(
                    title=title.strip(),
                    url="",
                    position=rank,
                    description=content.strip() if content else None,
                    category="finance",
                )

        except Exception as e:
            self.logger.error(f"解析财联社失败: {e}")
