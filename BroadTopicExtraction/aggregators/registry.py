# -*- coding: utf-8 -*-
"""
聚合器注册表

管理所有聚合器的注册和获取
"""

from typing import Dict, Optional, Type
from loguru import logger

from .base import BaseAggregator


class AggregatorRegistry:
    """聚合器注册表"""

    _instance: Optional["AggregatorRegistry"] = None
    _aggregators: Dict[str, Type[BaseAggregator]] = {}
    _instances: Dict[str, BaseAggregator] = {}

    def __new__(cls) -> "AggregatorRegistry":
        """单例模式"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def register(cls, aggregator_class: Type[BaseAggregator]) -> Type[BaseAggregator]:
        """
        注册聚合器类

        Args:
            aggregator_class: 聚合器类

        Returns:
            原聚合器类（用于装饰器模式）
        """
        name = aggregator_class.name
        cls._aggregators[name] = aggregator_class
        logger.debug(f"注册聚合器: {name} -> {aggregator_class.__name__}")
        return aggregator_class

    @classmethod
    def get(cls, name: str, **kwargs) -> Optional[BaseAggregator]:
        """
        获取聚合器实例

        Args:
            name: 聚合器名称
            **kwargs: 传递给聚合器构造函数的参数

        Returns:
            聚合器实例，不存在则返回 None
        """
        if name not in cls._aggregators:
            logger.warning(f"未知聚合器: {name}")
            return None

        # 如果没有额外参数，使用缓存的实例
        if not kwargs and name in cls._instances:
            return cls._instances[name]

        # 创建新实例
        aggregator_class = cls._aggregators[name]
        instance = aggregator_class(**kwargs)

        # 缓存无参数实例
        if not kwargs:
            cls._instances[name] = instance

        return instance

    @classmethod
    def list_aggregators(cls) -> Dict[str, str]:
        """
        列出所有已注册的聚合器

        Returns:
            {名称: 显示名称} 字典
        """
        return {
            name: agg_class.display_name
            for name, agg_class in cls._aggregators.items()
        }

    @classmethod
    def clear_instances(cls) -> None:
        """清除所有缓存的实例"""
        cls._instances.clear()


def get_aggregator(name: str, **kwargs) -> Optional[BaseAggregator]:
    """
    获取聚合器实例的便捷函数

    Args:
        name: 聚合器名称
        **kwargs: 传递给聚合器构造函数的参数

    Returns:
        聚合器实例
    """
    return AggregatorRegistry.get(name, **kwargs)


# 自动注册内置聚合器
def _register_builtin_aggregators() -> None:
    """注册内置聚合器"""
    from .newsnow import NewsNowAggregator
    from .tophub import TopHubAggregator
    from .rsshub import RSSHubAggregator
    from .mofish import MoFishAggregator
    from .anyknew import AnyKnewAggregator
    from .rebang import RebangAggregator
    from .jiucai import JiuCaiAggregator
    from .official import OfficialAPIAggregator

    # 推荐使用的聚合器
    AggregatorRegistry.register(TopHubAggregator)
    AggregatorRegistry.register(OfficialAPIAggregator)
    AggregatorRegistry.register(NewsNowAggregator)

    # 需自建实例
    AggregatorRegistry.register(RSSHubAggregator)

    # 已失效 (SPA 无公开 API，保留代码供参考)
    AggregatorRegistry.register(RebangAggregator)
    AggregatorRegistry.register(AnyKnewAggregator)
    AggregatorRegistry.register(JiuCaiAggregator)
    AggregatorRegistry.register(MoFishAggregator)


# 模块加载时自动注册
_register_builtin_aggregators()
