# -*- coding: utf-8 -*-
"""
全国热搜爬虫包
"""

from .weibo import WeiboHotSpider
from .douyin import DouyinHotSpider
from .baidu import BaiduHotSpider
from .zhihu import ZhihuHotSpider
from .bilibili import BilibiliHotSpider
from .hupu import HupuHotSpider
from .toutiao import ToutiaoHotSpider
from .tencent import TencentHotSpider
from .netease import NeteaseHotSpider
from .sina import SinaHotSpider

__all__ = [
    "WeiboHotSpider",
    "DouyinHotSpider",
    "BaiduHotSpider",
    "ZhihuHotSpider",
    "BilibiliHotSpider",
    "HupuHotSpider",
    "ToutiaoHotSpider",
    "TencentHotSpider",
    "NeteaseHotSpider",
    "SinaHotSpider",
]
