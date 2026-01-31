# -*- coding: utf-8 -*-
"""
聚合器基类

定义所有第三方聚合 API 的统一接口
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any
import httpx
from loguru import logger


@dataclass
class AggregatorResult:
    """聚合器返回结果"""

    success: bool
    source: str
    items: List[Dict] = field(default_factory=list)
    error: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    raw_data: Optional[Any] = None

    @property
    def count(self) -> int:
        return len(self.items)

    def to_dict(self) -> Dict:
        return {
            "success": self.success,
            "source": self.source,
            "items": self.items,
            "count": self.count,
            "error": self.error,
            "timestamp": self.timestamp,
        }


class BaseAggregator(ABC):
    """聚合器基类"""

    # 子类需要定义的属性
    name: str = "base"
    display_name: str = "Base Aggregator"
    base_url: str = ""

    def __init__(self, timeout: float = 30.0):
        """
        初始化聚合器

        Args:
            timeout: HTTP 请求超时时间（秒）
        """
        self.timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None

    @property
    def default_headers(self) -> Dict[str, str]:
        """默认请求头"""
        return {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Connection": "keep-alive",
        }

    async def _get_client(self) -> httpx.AsyncClient:
        """获取 HTTP 客户端"""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.timeout,
                follow_redirects=True,
                headers=self.default_headers,
            )
        return self._client

    async def close(self) -> None:
        """关闭 HTTP 客户端"""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "BaseAggregator":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    @abstractmethod
    def get_supported_sources(self) -> List[str]:
        """
        获取支持的数据源列表

        Returns:
            数据源 ID 列表
        """
        pass

    @abstractmethod
    async def fetch(self, source: str, **kwargs: Any) -> AggregatorResult:
        """
        从指定数据源获取数据

        Args:
            source: 数据源 ID
            **kwargs: 额外参数

        Returns:
            AggregatorResult 结果对象
        """
        pass

    async def fetch_all(
        self, sources: Optional[List[str]] = None, delay: float = 0.5
    ) -> List[AggregatorResult]:
        """
        获取多个数据源的数据

        Args:
            sources: 数据源列表，None 表示所有支持的源
            delay: 请求间隔（秒）

        Returns:
            结果列表
        """
        import asyncio

        if sources is None:
            sources = self.get_supported_sources()

        results = []
        for source in sources:
            logger.info(f"[{self.name}] 正在获取: {source}")
            result = await self.fetch(source)
            results.append(result)

            if result.success:
                logger.info(f"[{self.name}] {source}: 获取成功，共 {result.count} 条")
            else:
                logger.warning(f"[{self.name}] {source}: {result.error}")

            if delay > 0:
                await asyncio.sleep(delay)

        return results

    def _make_error_result(self, source: str, error: str) -> AggregatorResult:
        """创建错误结果"""
        return AggregatorResult(
            success=False,
            source=source,
            error=error,
        )

    def _make_success_result(
        self, source: str, items: List[Dict], raw_data: Any = None
    ) -> AggregatorResult:
        """创建成功结果"""
        return AggregatorResult(
            success=True,
            source=source,
            items=items,
            raw_data=raw_data,
        )
