# -*- coding: utf-8 -*-
"""
聚合器模块单元测试

测试 aggregators/ 下的聚合器类
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime


class TestAggregatorResult:
    """测试 AggregatorResult 数据类"""

    def test_success_result(self):
        """测试成功结果"""
        from BroadTopicExtraction.aggregators.base import AggregatorResult

        result = AggregatorResult(
            success=True,
            source="weibo",
            items=[{"title": "test1"}, {"title": "test2"}],
        )

        assert result.success is True
        assert result.source == "weibo"
        assert result.count == 2
        assert result.error is None

    def test_error_result(self):
        """测试错误结果"""
        from BroadTopicExtraction.aggregators.base import AggregatorResult

        result = AggregatorResult(
            success=False,
            source="weibo",
            error="连接超时",
        )

        assert result.success is False
        assert result.count == 0
        assert result.error == "连接超时"

    def test_to_dict(self):
        """测试转换为字典"""
        from BroadTopicExtraction.aggregators.base import AggregatorResult

        result = AggregatorResult(
            success=True,
            source="baidu",
            items=[{"title": "test"}],
        )
        d = result.to_dict()

        assert d["success"] is True
        assert d["source"] == "baidu"
        assert d["count"] == 1
        assert "timestamp" in d


class TestTopHubAggregator:
    """测试今日热榜聚合器"""

    def test_get_supported_sources(self):
        """测试获取支持的数据源"""
        from BroadTopicExtraction.aggregators.tophub import TopHubAggregator

        aggregator = TopHubAggregator()
        sources = aggregator.get_supported_sources()

        assert "weibo" in sources
        assert "zhihu" in sources
        assert "baidu" in sources
        assert "douyin" in sources

    def test_get_source_name(self):
        """测试获取数据源显示名称"""
        from BroadTopicExtraction.aggregators.tophub import TopHubAggregator

        aggregator = TopHubAggregator()

        assert aggregator.get_source_name("weibo") == "微博热搜"
        assert aggregator.get_source_name("unknown") == "unknown"

    def test_parse_single_item_valid(self):
        """测试解析有效数据项"""
        from BroadTopicExtraction.aggregators.tophub import TopHubAggregator

        aggregator = TopHubAggregator()
        item = {
            "title": "测试标题",
            "url": "https://example.com",
            "extra": {"hot": "123万"},
        }

        result = aggregator._parse_single_item(item, "weibo", 1)

        assert result["title"] == "测试标题"
        assert result["url"] == "https://example.com"
        assert result["position"] == 1
        assert result["platform"] == "weibo"
        assert result["hot_value"] == 1230000

    def test_parse_single_item_missing_title(self):
        """测试解析缺少标题的数据项"""
        from BroadTopicExtraction.aggregators.tophub import TopHubAggregator

        aggregator = TopHubAggregator()
        item = {"url": "https://example.com"}

        result = aggregator._parse_single_item(item, "weibo", 1)

        assert result is None

    def test_parse_hot_value_wan(self):
        """测试解析带'万'单位的热度值"""
        from BroadTopicExtraction.aggregators.tophub import TopHubAggregator

        aggregator = TopHubAggregator()
        item = {"title": "test", "extra": {"hot": "50.5万"}}

        result = aggregator._parse_single_item(item, "weibo", 1)

        assert result["hot_value"] == 505000

    def test_parse_hot_value_yi(self):
        """测试解析带'亿'单位的热度值"""
        from BroadTopicExtraction.aggregators.tophub import TopHubAggregator

        aggregator = TopHubAggregator()
        item = {"title": "test", "extra": {"hot": "1.5亿"}}

        result = aggregator._parse_single_item(item, "weibo", 1)

        assert result["hot_value"] == 150000000

    def test_parse_items_dict_format(self):
        """测试解析字典格式的 API 响应"""
        from BroadTopicExtraction.aggregators.tophub import TopHubAggregator

        aggregator = TopHubAggregator()
        data = {
            "data": {
                "items": [
                    {"title": "热搜1", "url": "url1"},
                    {"title": "热搜2", "url": "url2"},
                ]
            }
        }

        items = aggregator._parse_items(data, "weibo")

        assert len(items) == 2
        assert items[0]["position"] == 1
        assert items[1]["position"] == 2

    def test_parse_items_list_format(self):
        """测试解析列表格式的 API 响应"""
        from BroadTopicExtraction.aggregators.tophub import TopHubAggregator

        aggregator = TopHubAggregator()
        data = [
            {"title": "热搜1", "url": "url1"},
            {"title": "热搜2", "url": "url2"},
        ]

        items = aggregator._parse_items(data, "weibo")

        assert len(items) == 2

    @pytest.mark.asyncio
    async def test_fetch_unsupported_source(self):
        """测试获取不支持的数据源"""
        from BroadTopicExtraction.aggregators.tophub import TopHubAggregator

        aggregator = TopHubAggregator()
        result = await aggregator.fetch("unknown_source")

        assert result.success is False
        assert "不支持的数据源" in result.error

    @pytest.mark.asyncio
    async def test_fetch_success(self, mock_httpx_client):
        """测试成功获取数据"""
        from BroadTopicExtraction.aggregators.tophub import TopHubAggregator

        aggregator = TopHubAggregator()

        # Mock HTTP 响应
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "data": {"items": [{"title": "测试", "url": "http://test.com"}]}
        }
        mock_response.raise_for_status = MagicMock()
        mock_httpx_client.get = AsyncMock(return_value=mock_response)

        with patch.object(aggregator, "_get_client", return_value=mock_httpx_client):
            result = await aggregator.fetch("weibo")

        assert result.success is True
        assert result.count == 1

    @pytest.mark.asyncio
    async def test_fetch_network_error(self, mock_httpx_client):
        """测试网络错误处理"""
        from BroadTopicExtraction.aggregators.tophub import TopHubAggregator

        aggregator = TopHubAggregator()
        mock_httpx_client.get = AsyncMock(side_effect=Exception("网络错误"))

        with patch.object(aggregator, "_get_client", return_value=mock_httpx_client):
            result = await aggregator.fetch("weibo")

        assert result.success is False
        assert "网络错误" in result.error


class TestAggregatorRegistry:
    """测试聚合器注册表"""

    def test_get_aggregator_tophub(self):
        """测试获取 TopHub 聚合器"""
        from BroadTopicExtraction.aggregators import get_aggregator

        aggregator = get_aggregator("tophub")

        assert aggregator is not None
        assert aggregator.name == "tophub"

    def test_get_aggregator_unknown(self):
        """测试获取未知聚合器"""
        from BroadTopicExtraction.aggregators import get_aggregator

        aggregator = get_aggregator("unknown")

        assert aggregator is None

    def test_aggregator_registry(self):
        """测试聚合器注册表"""
        from BroadTopicExtraction.aggregators import AggregatorRegistry

        registry = AggregatorRegistry()

        # 检查已注册的聚合器
        assert registry is not None
