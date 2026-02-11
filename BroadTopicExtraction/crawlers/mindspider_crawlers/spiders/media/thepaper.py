# -*- coding: utf-8 -*-
"""
澎湃新闻爬虫

从 Next.js __NEXT_DATA__ 中提取新闻数据
"""

import json
import re
from typing import Generator

from scrapy.http import Response

from ..base import MediaSpider


class ThePaperSpider(MediaSpider):
    """澎湃新闻爬虫"""

    name = "thepaper"
    source_name = "thepaper"
    platform = "thepaper"
    media_type = "central"
    allowed_domains = ["thepaper.cn"]
    start_urls = ["https://www.thepaper.cn/"]

    custom_settings = {
        "DOWNLOAD_DELAY": 1,
    }

    def parse(self, response: Response) -> Generator:
        """从 __NEXT_DATA__ 提取新闻"""
        match = re.search(
            r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
            response.text,
            re.S,
        )
        if not match:
            self.logger.error("未找到 __NEXT_DATA__")
            return

        try:
            next_data = json.loads(match.group(1))
            data = next_data["props"]["pageProps"]["data"]
        except (json.JSONDecodeError, KeyError) as e:
            self.logger.error(f"解析 __NEXT_DATA__ 失败: {e}")
            return

        seen = set()

        # recommendTxt: 文字推荐（多组）
        for group in data.get("recommendTxt", []):
            if isinstance(group, list):
                yield from self._parse_items(group, seen)

        # recommendImg: 图片推荐
        yield from self._parse_items(data.get("recommendImg", []), seen)

        # recommendChannels: 频道推荐
        for channel in data.get("recommendChannels", []):
            yield from self._parse_items(channel.get("contentList", []), seen)
            yield from self._parse_items(channel.get("recommendList", []), seen)

        self.logger.info(f"获取 {len(seen)} 条澎湃新闻")

    def _parse_items(self, items: list, seen: set) -> Generator:
        """解析新闻列表"""
        for item in items:
            if not isinstance(item, dict):
                continue

            cont_id = item.get("contId", "")
            if not cont_id or cont_id in seen:
                continue

            title = item.get("name", "")
            if not title:
                continue

            seen.add(cont_id)

            # 链接：外部链接或澎湃详情页
            link = item.get("link", "")
            if not link:
                link = f"https://www.thepaper.cn/newsDetail_forward_{cont_id}"

            yield self.make_media_item(
                title=title,
                url=link,
                publish_date=item.get("pubTime", ""),
            )
