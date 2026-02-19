# -*- coding: utf-8 -*-
"""
澎湃新闻爬虫

从 Next.js __NEXT_DATA__ 中提取新闻数据
首页列表 → 文章详情页 → 提取正文
"""

import json
import re
from typing import Generator

import scrapy
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
            yield from self._parse_items(channel.get("contentList") or [], seen)
            yield from self._parse_items(channel.get("recommendList") or [], seen)

        self.logger.info(f"获取 {len(seen)} 条澎湃新闻")

    def _parse_items(self, items: list, seen: set) -> Generator:
        """解析新闻列表，跟进详情页提取正文"""
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

            publish_date = item.get("pubTime", "")

            # 外部链接不跟进详情页，直接 yield
            if "thepaper.cn" not in link:
                yield self.make_media_item(
                    title=title,
                    url=link,
                    publish_date=publish_date,
                )
                continue

            yield scrapy.Request(
                link,
                callback=self.parse_article,
                meta={
                    "title": title,
                    "url": link,
                    "publish_date": publish_date,
                },
            )

    def parse_article(self, response: Response) -> Generator:
        """解析文章详情页，提取正文"""
        title = response.meta["title"]
        url = response.meta["url"]
        publish_date = response.meta.get("publish_date", "")

        try:
            # 澎湃文章正文容器（CSS Modules 类名带哈希后缀）
            content_parts = response.css(
                'div[class*="cententWrap"] p::text, '
                'div[class*="news_txt"] p::text, '
                'div[class*="newsdetail_content"] p::text'
            ).getall()

            content = "\n".join(p.strip() for p in content_parts if p.strip())

            yield self.make_media_item(
                title=title,
                url=url,
                content=content or None,
                publish_date=publish_date,
            )

        except Exception as e:
            self.logger.warning(f"解析澎湃文章详情失败: {url} - {e}")
            yield self.make_media_item(
                title=title,
                url=url,
                publish_date=publish_date,
            )
