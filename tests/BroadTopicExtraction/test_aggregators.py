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

    def test_parse_html_simple(self):
        """测试解析简单 HTML 表格"""
        from BroadTopicExtraction.aggregators.tophub import TopHubAggregator

        aggregator = TopHubAggregator()
        html = '''
        <tr><td align="center">1.</td>
        <td><a href="https://example.com/1" target="_blank">测试标题</a></td>
        <td class="w">123万</td></tr>
        '''

        items = aggregator._parse_html(html, "weibo")

        assert len(items) == 1
        assert items[0]["title"] == "测试标题"
        assert items[0]["url"] == "https://example.com/1"
        assert items[0]["position"] == 1
        assert items[0]["platform"] == "weibo"
        assert items[0]["hot_value"] == 1230000

    def test_parse_html_with_image(self):
        """测试解析带图片的 HTML 表格"""
        from BroadTopicExtraction.aggregators.tophub import TopHubAggregator

        aggregator = TopHubAggregator()
        html = '''
        <tr><td align="center">1.</td>
        <td class="al"><img src="https://img.com/1.jpg">
        <a href="https://example.com/1" target="_blank">带图标题</a></td>
        <td>50.5万</td></tr>
        '''

        items = aggregator._parse_html(html, "zhihu")

        assert len(items) == 1
        assert items[0]["title"] == "带图标题"
        assert items[0]["hot_value"] == 505000

    def test_parse_html_multiple(self):
        """测试解析多条数据"""
        from BroadTopicExtraction.aggregators.tophub import TopHubAggregator

        aggregator = TopHubAggregator()
        html = '''
        <tr><td>1.</td><td><a href="https://example.com/1">热搜1</a></td><td>100</td></tr>
        <tr><td>2.</td><td><a href="https://example.com/2">热搜2</a></td><td>200</td></tr>
        '''

        items = aggregator._parse_html(html, "weibo")

        assert len(items) == 2
        assert items[0]["position"] == 1
        assert items[1]["position"] == 2

    def test_parse_hot_value(self):
        """测试解析热度值"""
        from BroadTopicExtraction.aggregators.tophub import TopHubAggregator

        aggregator = TopHubAggregator()

        assert aggregator._parse_hot_value("123万") == 1230000
        assert aggregator._parse_hot_value("1.5亿") == 150000000
        assert aggregator._parse_hot_value("12345") == 12345

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

        # Mock HTML 响应
        mock_response = MagicMock()
        mock_response.text = '''
        <tr><td>1.</td><td><a href="https://example.com" target="_blank">测试</a></td><td>100</td></tr>
        '''
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

    def test_get_aggregator_mofish(self):
        """测试获取鱼塘热榜聚合器"""
        from BroadTopicExtraction.aggregators import get_aggregator

        aggregator = get_aggregator("mofish")

        assert aggregator is not None
        assert aggregator.name == "mofish"

    def test_get_aggregator_anyknew(self):
        """测试获取 AnyKnew 聚合器"""
        from BroadTopicExtraction.aggregators import get_aggregator

        aggregator = get_aggregator("anyknew")

        assert aggregator is not None
        assert aggregator.name == "anyknew"

    def test_get_aggregator_rebang(self):
        """测试获取 Rebang 聚合器"""
        from BroadTopicExtraction.aggregators import get_aggregator

        aggregator = get_aggregator("rebang")

        assert aggregator is not None
        assert aggregator.name == "rebang"

    def test_get_aggregator_jiucai(self):
        """测试获取韭菜公社聚合器"""
        from BroadTopicExtraction.aggregators import get_aggregator

        aggregator = get_aggregator("jiucai")

        assert aggregator is not None
        assert aggregator.name == "jiucai"


class TestMoFishAggregator:
    """测试鱼塘热榜聚合器"""

    def test_get_supported_sources(self):
        """测试获取支持的数据源"""
        from BroadTopicExtraction.aggregators.mofish import MoFishAggregator

        aggregator = MoFishAggregator()
        sources = aggregator.get_supported_sources()

        assert "weibo" in sources
        assert "zhihu" in sources
        assert "douyin" in sources
        assert "36kr" in sources
        assert "xueqiu" in sources

    def test_get_source_name(self):
        """测试获取数据源显示名称"""
        from BroadTopicExtraction.aggregators.mofish import MoFishAggregator

        aggregator = MoFishAggregator()

        assert aggregator.get_source_name("weibo") == "微博热搜"
        assert aggregator.get_source_name("36kr") == "36氪"

    def test_parse_hot_value(self):
        """测试解析热度值"""
        from BroadTopicExtraction.aggregators.mofish import MoFishAggregator

        aggregator = MoFishAggregator()

        assert aggregator._parse_hot_value("123万") == 1230000
        assert aggregator._parse_hot_value("1.5亿") == 150000000
        assert aggregator._parse_hot_value("12345") == 12345
        assert aggregator._parse_hot_value("invalid") == 0

    @pytest.mark.asyncio
    async def test_fetch_unsupported_source(self):
        """测试获取不支持的数据源"""
        from BroadTopicExtraction.aggregators.mofish import MoFishAggregator

        aggregator = MoFishAggregator()
        result = await aggregator.fetch("unknown_source")

        assert result.success is False
        assert "不支持的数据源" in result.error


class TestAnyKnewAggregator:
    """测试 AnyKnew 聚合器"""

    def test_get_supported_sources(self):
        """测试获取支持的数据源"""
        from BroadTopicExtraction.aggregators.anyknew import AnyKnewAggregator

        aggregator = AnyKnewAggregator()
        sources = aggregator.get_supported_sources()

        assert "36kr" in sources
        assert "huxiu" in sources
        assert "github" in sources
        assert "juejin" in sources

    def test_get_source_name(self):
        """测试获取数据源显示名称"""
        from BroadTopicExtraction.aggregators.anyknew import AnyKnewAggregator

        aggregator = AnyKnewAggregator()

        assert aggregator.get_source_name("36kr") == "36氪"
        assert aggregator.get_source_name("github") == "GitHub Trending"

    @pytest.mark.asyncio
    async def test_fetch_unsupported_source(self):
        """测试获取不支持的数据源"""
        from BroadTopicExtraction.aggregators.anyknew import AnyKnewAggregator

        aggregator = AnyKnewAggregator()
        result = await aggregator.fetch("unknown_source")

        assert result.success is False
        assert "不支持的数据源" in result.error


class TestRebangAggregator:
    """测试 Rebang 聚合器"""

    def test_get_supported_sources(self):
        """测试获取支持的数据源"""
        from BroadTopicExtraction.aggregators.rebang import RebangAggregator

        aggregator = RebangAggregator()
        sources = aggregator.get_supported_sources()

        assert "weibo" in sources
        assert "xiaohongshu" in sources
        assert "sogou" in sources
        assert "thepaper" in sources

    def test_parse_hot_value(self):
        """测试解析热度值"""
        from BroadTopicExtraction.aggregators.rebang import RebangAggregator

        aggregator = RebangAggregator()

        assert aggregator._parse_hot_value("50万") == 500000
        assert aggregator._parse_hot_value("2亿") == 200000000


class TestJiuCaiAggregator:
    """测试韭菜公社聚合器"""

    def test_get_supported_sources(self):
        """测试获取支持的数据源"""
        from BroadTopicExtraction.aggregators.jiucai import JiuCaiAggregator

        aggregator = JiuCaiAggregator()
        sources = aggregator.get_supported_sources()

        assert "xueqiu" in sources
        assert "eastmoney" in sources
        assert "cls" in sources
        assert "wallstreetcn" in sources

    def test_get_source_name(self):
        """测试获取数据源显示名称"""
        from BroadTopicExtraction.aggregators.jiucai import JiuCaiAggregator

        aggregator = JiuCaiAggregator()

        assert aggregator.get_source_name("xueqiu") == "雪球热帖"
        assert aggregator.get_source_name("cls") == "财联社电报"


class TestOfficialAPIAggregator:
    """测试官方 API 聚合器"""

    def test_get_supported_sources(self):
        """测试获取支持的数据源"""
        from BroadTopicExtraction.aggregators.official import OfficialAPIAggregator

        aggregator = OfficialAPIAggregator()
        sources = aggregator.get_supported_sources()

        assert "baidu" in sources
        assert "tieba" in sources
        assert "juejin" in sources
        assert "bilibili_search" in sources

    def test_get_source_name(self):
        """测试获取数据源显示名称"""
        from BroadTopicExtraction.aggregators.official import OfficialAPIAggregator

        aggregator = OfficialAPIAggregator()

        assert aggregator.get_source_name("baidu") == "百度热搜"
        assert aggregator.get_source_name("juejin") == "掘金热榜"

    @pytest.mark.asyncio
    async def test_fetch_unsupported_source(self):
        """测试获取不支持的数据源"""
        from BroadTopicExtraction.aggregators.official import OfficialAPIAggregator

        aggregator = OfficialAPIAggregator()
        result = await aggregator.fetch("unknown_source")

        assert result.success is False
        assert "不支持的数据源" in result.error

    def test_parse_baidu(self):
        """测试解析百度热搜数据"""
        from BroadTopicExtraction.aggregators.official import OfficialAPIAggregator

        aggregator = OfficialAPIAggregator()

        # 模拟百度 API 返回数据 (三层嵌套: cards[0].content[0].content)
        mock_data = {
            "data": {
                "cards": [
                    {
                        "content": [
                            {
                                "content": [
                                    {"word": "测试热搜1", "hotScore": 1000000},
                                    {"word": "测试热搜2", "hotScore": 500000},
                                ]
                            }
                        ]
                    }
                ]
            }
        }

        items = aggregator._parse_baidu(mock_data)

        assert len(items) == 2
        assert items[0]["title"] == "测试热搜1"
        assert items[0]["hot_value"] == 1000000
        assert items[0]["position"] == 1

    def test_parse_juejin(self):
        """测试解析掘金数据"""
        from BroadTopicExtraction.aggregators.official import OfficialAPIAggregator

        aggregator = OfficialAPIAggregator()

        # 模拟掘金 API 返回数据
        # 模拟掘金 API 返回数据 (data[].item_info.article_info)
        mock_data = {
            "data": [
                {
                    "item_type": 2,
                    "item_info": {
                        "article_id": "123",
                        "article_info": {
                            "article_id": "123",
                            "title": "测试文章",
                            "view_count": 1000,
                            "digg_count": 50,
                        },
                        "author_user_info": {"user_name": "测试作者"},
                    },
                }
            ]
        }

        items = aggregator._parse_juejin(mock_data)

        assert len(items) == 1
        assert items[0]["title"] == "测试文章"
        assert items[0]["hot_value"] == 1000
        assert items[0]["author"] == "测试作者"
