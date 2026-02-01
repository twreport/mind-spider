# -*- coding: utf-8 -*-
"""
聚合器模块

提供第三方聚合 API 的统一接口

可用状态:
- TopHubAggregator: ✅ 推荐使用
- OfficialAPIAggregator: ✅ 最稳定
- RebangAggregator: ✅ 可用
- NewsNowAggregator: ⚠️ 部分可用
- RSSHubAggregator: ⚠️ 需自建实例
- AnyKnewAggregator: ⚠️ API 可能变更
- JiuCaiAggregator: ⚠️ API 可能变更
- MoFishAggregator: ❌ 已废弃
"""

from .base import BaseAggregator, AggregatorResult
from .registry import AggregatorRegistry, get_aggregator
from .newsnow import NewsNowAggregator
from .tophub import TopHubAggregator
from .rsshub import RSSHubAggregator
from .mofish import MoFishAggregator
from .anyknew import AnyKnewAggregator
from .rebang import RebangAggregator
from .jiucai import JiuCaiAggregator
from .official import OfficialAPIAggregator

__all__ = [
    "BaseAggregator",
    "AggregatorResult",
    "AggregatorRegistry",
    "get_aggregator",
    "NewsNowAggregator",
    "TopHubAggregator",
    "RSSHubAggregator",
    "MoFishAggregator",
    "AnyKnewAggregator",
    "RebangAggregator",
    "JiuCaiAggregator",
    "OfficialAPIAggregator",
]
