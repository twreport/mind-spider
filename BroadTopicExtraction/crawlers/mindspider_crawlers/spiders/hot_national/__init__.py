# -*- coding: utf-8 -*-
"""
全国热搜爬虫包
"""

from .weibo import WeiboHotSpider
from .douyin import DouyinHotSpider
from .baidu import BaiduHotSpider

__all__ = ["WeiboHotSpider", "DouyinHotSpider", "BaiduHotSpider"]
