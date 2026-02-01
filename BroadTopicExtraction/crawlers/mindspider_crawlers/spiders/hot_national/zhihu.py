# -*- coding: utf-8 -*-
"""
知乎热榜爬虫
"""

from typing import Generator
from scrapy.http import Response

from ..base import HotSearchSpider
from ...items import HotSearchItem


class ZhihuHotSpider(HotSearchSpider):
    """知乎热榜爬虫"""

    name = "zhihu_hot"
    source_name = "zhihu_hot"
    platform = "zhihu"
    allowed_domains = ["zhihu.com"]
    start_urls = ["https://www.zhihu.com/api/v3/feed/topstory/hot-lists/total?limit=50"]

    custom_settings = {
        "DOWNLOAD_DELAY": 1,
        "DEFAULT_REQUEST_HEADERS": {
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        },
    }

    def parse(self, response: Response) -> Generator:
        """解析知乎热榜 API 响应"""
        try:
            data = response.json()
            items = data.get("data", [])

            for rank, item in enumerate(items, start=1):
                target = item.get("target", {})
                title = target.get("title") or target.get("excerpt", "")
                if not title:
                    continue

                # 构建链接
                question_id = target.get("id")
                url = f"https://www.zhihu.com/question/{question_id}" if question_id else ""

                # 热度值
                detail_text = item.get("detail_text", "")
                hot_value = self._parse_hot_value(detail_text)

                yield self.make_hot_item(
                    title=title,
                    url=url,
                    position=rank,
                    hot_value=hot_value,
                    description=target.get("excerpt"),
                )

        except Exception as e:
            self.logger.error(f"解析知乎热榜失败: {e}")

    def _parse_hot_value(self, text: str) -> int:
        """解析热度文本，如 '1234 万热度'"""
        try:
            if "万" in text:
                num = float(text.replace("万热度", "").replace("万", "").strip())
                return int(num * 10000)
            elif "亿" in text:
                num = float(text.replace("亿热度", "").replace("亿", "").strip())
                return int(num * 100000000)
            else:
                clean = "".join(c for c in text if c.isdigit())
                return int(clean) if clean else 0
        except (ValueError, TypeError):
            return 0
