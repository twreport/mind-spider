# -*- coding: utf-8 -*-
"""
MindSpider Scrapy 中间件
"""

import random
from typing import Optional
from scrapy import signals
from scrapy.http import Request, Response
from scrapy.spiders import Spider
from scrapy.downloadermiddlewares.retry import RetryMiddleware as BaseRetryMiddleware


class RandomUserAgentMiddleware:
    """随机 User-Agent 中间件"""

    def __init__(self, user_agents: list):
        self.user_agents = user_agents

    @classmethod
    def from_crawler(cls, crawler):
        user_agents = crawler.settings.getlist("USER_AGENTS")
        if not user_agents:
            user_agents = [
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            ]
        return cls(user_agents)

    def process_request(self, request: Request, spider: Spider) -> None:
        """为每个请求设置随机 User-Agent"""
        request.headers["User-Agent"] = random.choice(self.user_agents)


class RetryMiddleware(BaseRetryMiddleware):
    """增强的重试中间件"""

    def process_response(
        self, request: Request, response: Response, spider: Spider
    ) -> Response | Request:
        """处理响应，检查是否需要重试"""
        # 检查是否被反爬
        if response.status == 403:
            spider.logger.warning(f"403 Forbidden: {request.url}")
            # 可以在这里添加代理切换逻辑

        return super().process_response(request, response, spider)


class ProxyMiddleware:
    """代理中间件 (可选)"""

    def __init__(self, proxy_list: list):
        self.proxy_list = proxy_list
        self.current_proxy: Optional[str] = None

    @classmethod
    def from_crawler(cls, crawler):
        proxy_list = crawler.settings.getlist("PROXY_LIST", [])
        return cls(proxy_list)

    def process_request(self, request: Request, spider: Spider) -> None:
        """为请求设置代理"""
        if self.proxy_list:
            proxy = random.choice(self.proxy_list)
            request.meta["proxy"] = proxy
            self.current_proxy = proxy

    def process_exception(self, request: Request, exception, spider: Spider):
        """处理代理异常"""
        if self.current_proxy and self.current_proxy in self.proxy_list:
            # 移除失败的代理
            spider.logger.warning(f"代理失败，移除: {self.current_proxy}")
            self.proxy_list.remove(self.current_proxy)
