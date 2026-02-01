# -*- coding: utf-8 -*-
"""
传统媒体爬虫包
"""

from .rmrb import RmrbSpider
from .xinhua import XinhuaSpider
from .thepaper import ThePaperSpider
from .cctv import CCTVSpider
from .gmrb import GmrbSpider
from .jjrb import JjrbSpider
from .mrdx import MrdxSpider
from .xwlb import XwlbSpider
from .xwzbj import XwzbjSpider
from .zgqnb import ZgqnbSpider

__all__ = [
    "RmrbSpider",
    "XinhuaSpider",
    "ThePaperSpider",
    "CCTVSpider",
    "GmrbSpider",
    "JjrbSpider",
    "MrdxSpider",
    "XwlbSpider",
    "XwzbjSpider",
    "ZgqnbSpider",
]
