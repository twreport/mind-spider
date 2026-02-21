# -*- coding: utf-8 -*-
"""
信号检测模块集成测试

连接真实 MongoDB (10.168.1.80:27018) 验证 DataReader 和 SignalDetector。
需要 MongoDB 可达且有数据才能通过。
"""

import time

import pytest

from BroadTopicExtraction.analyzer.data_reader import DataReader
from BroadTopicExtraction.analyzer.signal_detector import (
    SignalDetector,
    _extract_keywords,
    _normalize_platform,
)
from BroadTopicExtraction.pipeline.mongo_writer import MongoWriter
from ms_config import settings

# 用较长的回看窗口，确保能拿到数据
LOOKBACK = 3600 * 6  # 6 小时


@pytest.fixture(scope="module")
def mongo():
    """共享 MongoWriter 连接（原始数据库）"""
    mw = MongoWriter()
    mw.connect()
    yield mw
    mw.close()


@pytest.fixture(scope="module")
def signal_mongo():
    """共享 MongoWriter 连接（信号数据库）"""
    mw = MongoWriter(db_name=settings.MONGO_SIGNAL_DB_NAME)
    mw.connect()
    yield mw
    mw.close()


@pytest.fixture(scope="module")
def reader(mongo):
    """共享 DataReader"""
    return DataReader(mongo_writer=mongo)


# ==================== DataReader 测试 ====================


class TestDataReader:
    def test_get_hot_national(self, reader):
        since = int(time.time()) - LOOKBACK
        items = reader.get_hot_national(since)
        assert isinstance(items, list)
        assert len(items) > 0, "hot_national 应有数据"

        sample = items[0]
        assert "item_id" in sample
        assert "title" in sample
        assert "platform" in sample
        assert "_id" not in sample, "projection 应排除 _id"

    def test_get_hot_vertical(self, reader):
        since = int(time.time()) - LOOKBACK
        items = reader.get_hot_vertical(since)
        assert isinstance(items, list)
        assert len(items) > 0, "hot_vertical 应有数据"

        sample = items[0]
        assert "vertical" in sample, "hot_vertical 应包含 vertical 字段"

    def test_get_aggregator(self, reader):
        since = int(time.time()) - LOOKBACK
        items = reader.get_aggregator(since)
        assert isinstance(items, list)
        assert len(items) > 0, "aggregator 应有数据"

    def test_get_media(self, reader):
        since = int(time.time()) - LOOKBACK
        items = reader.get_media(since)
        assert isinstance(items, list)
        # media 可能较少，不强制 > 0

    def test_get_hot_local_returns_empty(self, reader):
        items = reader.get_hot_local()
        assert items == []

    def test_get_all_hot_items_dedup(self, reader):
        since = int(time.time()) - LOOKBACK
        national = reader.get_hot_national(since)
        vertical = reader.get_hot_vertical(since)
        aggregator = reader.get_aggregator(since)
        merged = reader.get_all_hot_items(since)

        # 去重后应 <= 总和
        total_raw = len(national) + len(vertical) + len(aggregator)
        assert len(merged) <= total_raw
        assert len(merged) > 0

        # title 应唯一
        titles = [it["title"] for it in merged]
        assert len(titles) == len(set(titles)), "去重后 title 应唯一"

    def test_all_hot_items_has_multiple_platforms(self, reader):
        since = int(time.time()) - LOOKBACK
        merged = reader.get_all_hot_items(since)
        platforms = set(it.get("platform") for it in merged)
        assert len(platforms) >= 2, f"应有多个平台，实际: {platforms}"

    def test_get_items_by_source(self, reader):
        since = int(time.time()) - LOOKBACK
        items = reader.get_items_by_source("hot_national", "baidu_hot", since)
        assert isinstance(items, list)
        assert len(items) > 0, "baidu_hot 应有数据"
        for it in items:
            assert it.get("source") == "baidu_hot"


# ==================== jieba 关键词提取测试 ====================


class TestExtractKeywords:
    def test_basic(self):
        kws = _extract_keywords("习近平会见法国总统马克龙")
        assert isinstance(kws, set)
        assert len(kws) > 0
        # 单字应被过滤
        for w in kws:
            assert len(w) >= 2

    def test_stopwords_filtered(self):
        kws = _extract_keywords("如何看待今天发布的最新通报")
        assert "如何看待" not in kws
        assert "今天" not in kws
        assert "最新" not in kws
        assert "发布" not in kws
        assert "通报" not in kws

    def test_empty(self):
        assert _extract_keywords("") == set()
        assert _extract_keywords(None) == set()


# ==================== 平台归一化测试 ====================


class TestNormalizePlatform:
    def test_alias(self):
        assert _normalize_platform("bilibili-hot-search") == "bilibili"
        assert _normalize_platform("cls-hot") == "cls"
        assert _normalize_platform("douban-movie") == "douban"
        assert _normalize_platform("github-trending-today") == "github"

    def test_passthrough(self):
        assert _normalize_platform("baidu") == "baidu"
        assert _normalize_platform("weibo") == "weibo"
        assert _normalize_platform("douyin") == "douyin"


# ==================== SignalDetector 测试 ====================


class TestSignalDetector:
    """使用真实数据测试信号检测

    注意: 信号数量取决于实时数据，测试只验证结构正确性和基本逻辑。
    """

    @pytest.fixture(scope="class")
    def detector_and_signals(self, mongo, signal_mongo):
        """运行一次检测，共享结果"""
        reader = DataReader(mongo_writer=mongo)
        # 信号写入独立的 signal 库
        detector = SignalDetector(
            data_reader=reader,
            signal_writer=signal_mongo,
            # 降低阈值，确保能检测到信号
            thresholds={
                "velocity_growth_rate": 0.05,
                "velocity_min_hot_value": 1000,
                "new_entry_max_age": LOOKBACK,
                "new_entry_min_hot_value": 10000,
                "new_entry_max_position": 20,
                "position_jump_min": 3,
                "cross_platform_min_keywords": 2,
                "cross_platform_min_platforms": 2,
            },
        )
        since = int(time.time()) - LOOKBACK
        signals = detector.detect(since_ts=since)
        yield detector, signals

        # 清理: 从 signal 库删除本次测试写入的信号
        collection = signal_mongo.get_collection("signals")
        if signals:
            signal_ids = [s["signal_id"] for s in signals]
            result = collection.delete_many({"signal_id": {"$in": signal_ids}})
            print(f"\n[cleanup] 删除 {result.deleted_count} 条测试信号")

    def test_detect_returns_list(self, detector_and_signals):
        _, signals = detector_and_signals
        assert isinstance(signals, list)

    def test_signals_found(self, detector_and_signals):
        _, signals = detector_and_signals
        assert len(signals) > 0, "降低阈值后应能检测到信号"

    def test_signal_structure(self, detector_and_signals):
        _, signals = detector_and_signals
        required_fields = {
            "signal_id", "signal_type", "layer", "title",
            "platform", "platforms", "source_collection", "details",
        }
        for s in signals:
            missing = required_fields - set(s.keys())
            assert not missing, f"信号缺少字段 {missing}: {s['signal_id']}"
            assert s["signal_type"] in ("velocity", "new_entry", "position_jump", "cross_platform")
            assert s["layer"] in (1, 2)

    def test_velocity_signals(self, detector_and_signals):
        _, signals = detector_and_signals
        velocity = [s for s in signals if s["signal_type"] == "velocity"]
        print(f"\n  velocity 信号: {len(velocity)} 条")
        for s in velocity[:3]:
            d = s["details"]
            print(f"    {s['title'][:25]} | {d['previous_value']}->{d['current_value']} rate={d['growth_rate']}")
            assert d["growth_rate"] >= 0.05
            assert d["current_value"] > d["previous_value"]

    def test_new_entry_signals(self, detector_and_signals):
        _, signals = detector_and_signals
        new_entry = [s for s in signals if s["signal_type"] == "new_entry"]
        print(f"\n  new_entry 信号: {len(new_entry)} 条")
        for s in new_entry[:3]:
            d = s["details"]
            print(f"    {s['title'][:25]} | hv={d['hot_value']} pos={d['position']} age={d['age_seconds']}s")

    def test_position_jump_signals(self, detector_and_signals):
        _, signals = detector_and_signals
        jumps = [s for s in signals if s["signal_type"] == "position_jump"]
        print(f"\n  position_jump 信号: {len(jumps)} 条")
        for s in jumps[:3]:
            d = s["details"]
            print(f"    {s['title'][:25]} | {d['previous_position']}->{d['current_position']} jump={d['jump']}")
            assert d["jump"] >= 3

    def test_cross_platform_signals(self, detector_and_signals):
        _, signals = detector_and_signals
        cross = [s for s in signals if s["signal_type"] == "cross_platform"]
        print(f"\n  cross_platform 信号: {len(cross)} 条")
        for s in cross[:3]:
            d = s["details"]
            print(f"    {s['title'][:25]} | platforms={s['platforms']} kw={d['common_keywords'][:5]}")
            assert len(s["platforms"]) >= 2
            assert s["platform"] is None
            assert s["source_collection"] == "cross"
            # platform_items 应包含每个平台的详细信息
            assert "platform_items" in d
            for plat, info in d["platform_items"].items():
                assert "title" in info

    def test_signal_type_distribution(self, detector_and_signals):
        _, signals = detector_and_signals
        from collections import Counter
        dist = Counter(s["signal_type"] for s in signals)
        print(f"\n  信号分布: {dict(dist)}")
        # 至少应有 2 种类型的信号
        assert len(dist) >= 2, f"信号类型过少: {dict(dist)}"

    def test_signals_written_to_mongo(self, detector_and_signals, signal_mongo):
        _, signals = detector_and_signals
        if not signals:
            pytest.skip("无信号可验证")
        # 验证第一条信号已写入 signal 库
        doc = signal_mongo.find_one("signals", {"signal_id": signals[0]["signal_id"]})
        assert doc is not None, "信号应已写入 mindspider_signal 库"
        assert doc["consumed"] is False
        assert "detected_at" in doc
        assert "updated_at" in doc

    def test_cross_platform_normalized(self, detector_and_signals):
        """验证跨平台信号的平台名已归一化"""
        _, signals = detector_and_signals
        cross = [s for s in signals if s["signal_type"] == "cross_platform"]
        alias_values = {"bilibili-hot-search", "cls-hot", "douban-movie", "github-trending-today"}
        for s in cross:
            for plat in s["platforms"]:
                assert plat not in alias_values, f"平台名未归一化: {plat}"

    def test_layer1_has_history(self, detector_and_signals):
        """Layer 1 信号应携带热度和排名历史"""
        _, signals = detector_and_signals
        layer1 = [s for s in signals if s["layer"] == 1]
        assert len(layer1) > 0
        for s in layer1:
            assert "hot_value_history" in s, f"缺少 hot_value_history: {s['signal_id']}"
            assert "position_history" in s, f"缺少 position_history: {s['signal_id']}"

    def test_upsert_idempotent(self, detector_and_signals, signal_mongo):
        """重复检测同一信号应 upsert 而非插入新记录"""
        detector, signals = detector_and_signals
        if not signals:
            pytest.skip("无信号可验证")

        # 记录当前数量
        count_before = signal_mongo.count_documents("signals", {})

        # 再跑一次检测（会 upsert 同样的信号）
        since = int(time.time()) - LOOKBACK
        detector.detect(since_ts=since)

        count_after = signal_mongo.count_documents("signals", {})
        # 数量不应显著增加（可能有少量新信号，但不会翻倍）
        assert count_after < count_before * 1.5, (
            f"upsert 失效？before={count_before}, after={count_after}"
        )


# ==================== detect_for_source 测试 ====================


class TestDetectForSource:
    """测试单信源 Layer 1 检测"""

    @pytest.fixture(scope="class")
    def source_result(self, mongo, signal_mongo):
        reader = DataReader(mongo_writer=mongo)
        detector = SignalDetector(
            data_reader=reader,
            signal_writer=signal_mongo,
            thresholds={
                "velocity_growth_rate": 0.05,
                "velocity_min_hot_value": 1000,
                "new_entry_max_age": LOOKBACK,
                "new_entry_min_hot_value": 10000,
                "new_entry_max_position": 20,
                "position_jump_min": 3,
            },
        )
        since = int(time.time()) - LOOKBACK
        signals = detector.detect_for_source("baidu_hot", "hot_national", since_ts=since)
        yield signals

        # 清理
        if signals:
            col = signal_mongo.get_collection("signals")
            col.delete_many({"signal_id": {"$in": [s["signal_id"] for s in signals]}})
            print(f"\n[cleanup] 删除 {len(signals)} 条 detect_for_source 测试信号")

    def test_returns_list(self, source_result):
        assert isinstance(source_result, list)

    def test_only_layer1(self, source_result):
        """detect_for_source 不应产生 cross_platform 信号"""
        for s in source_result:
            assert s["signal_type"] in ("velocity", "new_entry", "position_jump"), (
                f"不应有 {s['signal_type']} 信号"
            )
            assert s["layer"] == 1

    def test_signals_from_correct_source(self, source_result, mongo):
        """信号应来自指定信源的数据"""
        # baidu_hot 的 platform 是 baidu
        for s in source_result:
            assert s["platform"] == "baidu", f"平台应为 baidu，实际: {s['platform']}"

    def test_has_history(self, source_result):
        for s in source_result:
            assert "hot_value_history" in s
            assert "position_history" in s


# ==================== detect_cross_platform 测试 ====================


class TestDetectCrossPlatform:
    """测试独立跨平台检测"""

    @pytest.fixture(scope="class")
    def cross_result(self, mongo, signal_mongo):
        reader = DataReader(mongo_writer=mongo)
        detector = SignalDetector(
            data_reader=reader,
            signal_writer=signal_mongo,
            thresholds={
                "cross_platform_min_keywords": 2,
                "cross_platform_min_platforms": 2,
            },
        )
        since = int(time.time()) - LOOKBACK
        signals = detector.detect_cross_platform(since_ts=since)
        yield signals

        # 清理
        if signals:
            col = signal_mongo.get_collection("signals")
            col.delete_many({"signal_id": {"$in": [s["signal_id"] for s in signals]}})
            print(f"\n[cleanup] 删除 {len(signals)} 条 cross_platform 测试信号")

    def test_returns_list(self, cross_result):
        assert isinstance(cross_result, list)

    def test_only_cross_platform(self, cross_result):
        """detect_cross_platform 只应产生 cross_platform 信号"""
        assert len(cross_result) > 0, "应有跨平台信号"
        for s in cross_result:
            assert s["signal_type"] == "cross_platform"
            assert s["layer"] == 2

    def test_multiple_platforms(self, cross_result):
        for s in cross_result:
            assert len(s["platforms"]) >= 2
            assert s["platform"] is None

    def test_has_platform_items(self, cross_result):
        for s in cross_result:
            items = s["details"].get("platform_items", {})
            assert len(items) >= 2
            for plat, info in items.items():
                assert "title" in info
                assert "hot_value_history" in info
