# -*- coding: utf-8 -*-
"""
行业/垂直榜单爬虫包
"""

from .ithome import ITHomeSpider
from .huxiu import HuxiuSpider
from .kr36 import Kr36Spider
from .cls import CLSSpider
from .xueqiu import XueqiuSpider
from .juejin import JuejinSpider

__all__ = [
    "ITHomeSpider",
    "HuxiuSpider",
    "Kr36Spider",
    "CLSSpider",
    "XueqiuSpider",
    "JuejinSpider",
]
