# -*- coding: utf-8 -*-
"""
百度热搜爬虫

爬取百度热搜榜数据
"""

import json
import re
from typing import Any, Dict, Generator

from scrapy.http import Response

from ..base import HotSearchSpider


class BaiduHotSpider(HotSearchSpider):
    """百度热搜爬虫"""

    name = "baidu_hot"
    source_name = "baidu_hot"
    platform = "baidu"
    allowed_domains = ["baidu.com", "top.baidu.com"]

    # 百度热搜页面
    start_urls = ["https://top.baidu.com/board?tab=realtime"]

    custom_settings = {
        "DEFAULT_REQUEST_HEADERS": {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Referer": "https://www.baidu.com/",
        },
    }

    def parse(self, response: Response) -> Generator:
        """解析百度热搜数据"""
        try:
            # 从页面中提取 JSON 数据
            # 百度热搜数据嵌入在页面的 script 标签中
            pattern = r'<!--s-data:(.*?)-->'
            match = re.search(pattern, response.text, re.DOTALL)

            if not match:
                self.logger.error("未找到百度热搜数据")
                return

            data = json.loads(match.group(1))
            cards = data.get("data", {}).get("cards", [])

            if not cards:
                self.logger.error("百度热搜数据为空")
                return

            # 第一个 card 通常是实时热搜
            content = cards[0].get("content", [])

            for idx, item in enumerate(content):
                yield self._parse_item(item, idx + 1)

            self.logger.info(f"获取 {len(content)} 条热搜")

        except json.JSONDecodeError as e:
            self.logger.error(f"JSON 解析失败: {e}")
        except Exception as e:
            self.logger.error(f"解析失败: {e}")

    def _parse_item(self, item: Dict[str, Any], position: int):
        """解析单条热搜数据"""
        word = item.get("word", "") or item.get("query", "")
        hot_value = item.get("hotScore", 0)
        url = item.get("url", "") or item.get("rawUrl", "")

        # 如果没有 URL，构建搜索链接
        if not url and word:
            url = f"https://www.baidu.com/s?wd={word}"

        return self.make_hot_item(
            title=word,
            url=url,
            position=position,
            hot_value=int(hot_value) if hot_value else None,
            extra={
                "desc": item.get("desc", ""),
                "img": item.get("img", ""),
            },
        )
