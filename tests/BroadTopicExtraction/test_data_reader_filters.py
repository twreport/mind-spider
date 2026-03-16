# -*- coding: utf-8 -*-
"""
DataReader 过滤规则 - 单元测试

验证 filters.yaml 中的黑名单和辟谣置顶过滤逻辑。
"""

from unittest.mock import MagicMock

import pytest

from BroadTopicExtraction.analyzer.data_reader import DataReader


@pytest.fixture
def reader():
    """构造 DataReader 实例，使用 mock MongoWriter"""
    mongo = MagicMock()
    mongo.connect.return_value = None
    r = DataReader(mongo_writer=mongo)
    return r


class TestDataReaderFilters:
    def test_hupu_board_name_filtered(self, reader):
        """虎扑板块名被过滤"""
        items = [
            {"source": "hupu_hot", "title": "步行街主干道", "position": 1},
            {"source": "hupu_hot", "title": "影视区", "position": 2},
            {"source": "hupu_hot", "title": "NBA季后赛勇士vs湖人", "position": 3},
        ]
        result = reader._apply_filters(items)
        assert len(result) == 1
        assert result[0]["title"] == "NBA季后赛勇士vs湖人"

    def test_xueqiu_short_stock_name_filtered(self, reader):
        """雪球短股票名被过滤"""
        items = [
            {"source": "newsnow_xueqiu", "title": "贵州茅台", "position": 1},
            {"source": "newsnow_xueqiu", "title": "比亚迪", "position": 2},
            {"source": "newsnow_xueqiu", "title": "中芯国际", "position": 3},
        ]
        result = reader._apply_filters(items)
        assert len(result) == 0

    def test_xueqiu_long_title_passes(self, reader):
        """雪球正常长标题不被过滤"""
        items = [
            {
                "source": "newsnow_xueqiu",
                "title": "贵州茅台股价创历史新高突破3000元",
                "position": 1,
            },
        ]
        result = reader._apply_filters(items)
        assert len(result) == 1

    def test_netease_debunk_filtered(self, reader):
        """网易辟谣置顶被过滤"""
        items = [
            {"source": "netease_hot", "title": "春节期间这些谣言你信了吗", "position": 5},
            {"source": "netease_hot", "title": "网传某地发生不实消息", "position": 5},
            {"source": "netease_hot", "title": "官方辟谣：该视频系摆拍", "position": 5},
        ]
        result = reader._apply_filters(items)
        assert len(result) == 0

    def test_netease_non_position5_passes(self, reader):
        """网易非 position=5 的含谣言标题不被过滤"""
        items = [
            {"source": "netease_hot", "title": "春节期间这些谣言你信了吗", "position": 3},
        ]
        result = reader._apply_filters(items)
        assert len(result) == 1

    def test_normal_items_pass_through(self, reader):
        """正常条目不受影响"""
        items = [
            {"source": "baidu_hot", "title": "某重大新闻事件", "position": 1},
            {"source": "sina_hot", "title": "明星官宣结婚", "position": 2},
            {"source": "tencent_hot", "title": "科技公司发布新产品", "position": 3},
        ]
        result = reader._apply_filters(items)
        assert len(result) == 3

    def test_empty_filters_passthrough(self, reader):
        """空过滤配置不影响数据"""
        reader._filters = {}
        items = [
            {"source": "hupu_hot", "title": "步行街主干道", "position": 1},
        ]
        result = reader._apply_filters(items)
        assert len(result) == 1
