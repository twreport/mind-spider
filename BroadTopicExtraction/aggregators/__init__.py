# -*- coding: utf-8 -*-
"""
聚合器模块

提供第三方聚合 API 的统一接口
"""

from .base import BaseAggregator, AggregatorResult
from .registry import AggregatorRegistry, get_aggregator
from .newsnow import NewsNowAggregator
from .tophub import TopHubAggregator
from .rsshub import RSSHubAggregator

__all__ = [
    "BaseAggregator",
    "AggregatorResult",
    "AggregatorRegistry",
    "get_aggregator",
    "NewsNowAggregator",
    "TopHubAggregator",
    "RSSHubAggregator",
]
