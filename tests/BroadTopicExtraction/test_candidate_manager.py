# -*- coding: utf-8 -*-
"""
候选话题管理模块集成测试

连接真实 MongoDB (mindspider_signal) 验证 CandidateManager。
需要 MongoDB 可达且 signals collection 有数据（或先跑信号检测）。
"""

import hashlib
import time

import pytest

from BroadTopicExtraction.analyzer.candidate_manager import (
    CandidateManager,
    COLLECTION,
    TRANSITION_RULES,
    _ACTIVE_STATUSES,
    _is_declining,
)
from BroadTopicExtraction.analyzer.signal_detector import SignalDetector, _extract_keywords
from BroadTopicExtraction.analyzer.data_reader import DataReader
from BroadTopicExtraction.pipeline.mongo_writer import MongoWriter
from config import settings

LOOKBACK = 3600 * 6  # 6 小时


@pytest.fixture(scope="module")
def raw_mongo():
    """原始数据库连接"""
    mw = MongoWriter()
    mw.connect()
    yield mw
    mw.close()


@pytest.fixture(scope="module")
def signal_mongo():
    """信号数据库连接"""
    mw = MongoWriter(db_name=settings.MONGO_SIGNAL_DB_NAME)
    mw.connect()
    yield mw
    mw.close()


@pytest.fixture(scope="module")
def seeded_signals(raw_mongo, signal_mongo):
    """先跑一次信号检测，确保 signals collection 有数据"""
    reader = DataReader(mongo_writer=raw_mongo)
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
            "cross_platform_min_keywords": 2,
            "cross_platform_min_platforms": 2,
        },
    )
    since = int(time.time()) - LOOKBACK
    signals = detector.detect(since_ts=since)
    print(f"\n[seed] 检测到 {len(signals)} 个信号")
    return signals


@pytest.fixture(scope="module")
def manager(signal_mongo):
    """共享 CandidateManager"""
    return CandidateManager(signal_writer=signal_mongo)


@pytest.fixture(scope="module")
def cycle_result(seeded_signals, manager, signal_mongo):
    """运行一轮候选管理，共享结果"""
    stats = manager.run_cycle()
    print(f"\n[cycle] 统计: {stats}")
    yield stats

    # 清理: 删除本次测试创建的候选
    col = signal_mongo.get_collection(COLLECTION)
    result = col.delete_many({})
    print(f"\n[cleanup] 删除 {result.deleted_count} 个测试候选")


# ==================== 基础功能测试 ====================


class TestRunCycle:
    def test_returns_dict(self, cycle_result):
        assert isinstance(cycle_result, dict)

    def test_has_required_keys(self, cycle_result):
        required = {"signals_consumed", "candidates_created", "candidates_updated",
                     "transitions", "signals_deleted"}
        assert required <= set(cycle_result.keys())

    def test_signals_consumed(self, cycle_result, seeded_signals):
        """应消费了信号"""
        if seeded_signals:
            assert cycle_result["signals_consumed"] > 0

    def test_signals_deleted(self, cycle_result, signal_mongo):
        """signals collection 应被清空"""
        count = signal_mongo.count_documents("signals", {})
        assert count == 0, f"signals 应被清空，实际还有 {count} 条"

    def test_candidates_created(self, cycle_result):
        """应创建了候选"""
        if cycle_result["signals_consumed"] > 0:
            assert cycle_result["candidates_created"] > 0


# ==================== 候选文档结构测试 ====================


class TestCandidateStructure:
    @pytest.fixture(scope="class")
    def candidates(self, cycle_result, signal_mongo):
        return signal_mongo.find(COLLECTION, {})

    def test_has_candidates(self, candidates):
        assert len(candidates) > 0, "应有候选文档"

    def test_required_fields(self, candidates):
        required = {
            "candidate_id", "canonical_title", "source_titles", "status",
            "platforms", "platform_count", "snapshots",
            "first_seen_at", "updated_at", "status_history",
        }
        for cand in candidates:
            missing = required - set(cand.keys())
            assert not missing, f"候选缺少字段 {missing}: {cand['candidate_id']}"

    def test_candidate_id_format(self, candidates):
        for cand in candidates:
            assert cand["candidate_id"].startswith("cand_")

    def test_status_valid(self, candidates):
        valid = _ACTIVE_STATUSES | {"closed", "faded"}
        for cand in candidates:
            assert cand["status"] in valid, f"无效状态: {cand['status']}"

    def test_snapshots_structure(self, candidates):
        for cand in candidates:
            assert len(cand["snapshots"]) >= 1
            for snap in cand["snapshots"]:
                assert "ts" in snap
                assert "score_pos" in snap
                assert "sum_hot" in snap

    def test_status_history_structure(self, candidates):
        for cand in candidates:
            assert len(cand["status_history"]) >= 1
            first = cand["status_history"][0]
            assert first["status"] == "emerging"
            for entry in cand["status_history"]:
                assert "ts" in entry
                assert "status" in entry
                assert "reason" in entry

    def test_platforms_nonempty(self, candidates):
        for cand in candidates:
            assert len(cand["platforms"]) >= 1
            assert cand["platform_count"] == len(cand["platforms"])


# ==================== 聚类 + 匹配测试 ====================


class TestClustering:
    def test_keyword_overlap(self, manager):
        kw_a = {"习近平", "会见", "法国", "总统"}
        kw_b = {"习近平", "法国", "马克龙", "访问"}
        overlap = manager._compute_overlap(kw_a, kw_b)
        # 交集 = {习近平, 法国} = 2, min(4, 4) = 4, overlap = 0.5
        assert overlap == 0.5

    def test_keyword_overlap_high(self, manager):
        kw_a = {"地震", "四川", "成都"}
        kw_b = {"地震", "四川", "救援"}
        overlap = manager._compute_overlap(kw_a, kw_b)
        # 交集 = {地震, 四川} = 2, min(3, 3) = 3, overlap ≈ 0.667
        assert overlap >= 0.6

    def test_keyword_overlap_empty(self, manager):
        assert manager._compute_overlap(set(), {"a", "b"}) == 0.0
        assert manager._compute_overlap({"a"}, set()) == 0.0

    def test_match_candidate(self, manager):
        """测试候选匹配"""
        existing = [
            {
                "candidate_id": "cand_test1",
                "source_titles": ["四川成都发生5.0级地震", "成都地震最新消息"],
                "status": "emerging",
            },
        ]
        # 相似标题应匹配
        sig_kw = _extract_keywords("四川成都地震已致3人受伤")
        matched = manager._match_candidate(sig_kw, existing)
        assert matched is not None
        assert matched["candidate_id"] == "cand_test1"

    def test_match_candidate_no_match(self, manager):
        """不相关标题不应匹配"""
        existing = [
            {
                "candidate_id": "cand_test1",
                "source_titles": ["四川成都发生5.0级地震"],
                "status": "emerging",
            },
        ]
        sig_kw = _extract_keywords("苹果发布新款iPhone手机")
        matched = manager._match_candidate(sig_kw, existing)
        assert matched is None


# ==================== 准入标准测试 ====================


class TestAdmission:
    def test_cross_platform_admitted(self, manager):
        assert manager._check_admission({"signal_type": "cross_platform"}) is True

    def test_velocity_admitted(self, manager):
        assert manager._check_admission({"signal_type": "velocity"}) is True

    def test_position_jump_admitted(self, manager):
        assert manager._check_admission({"signal_type": "position_jump"}) is True

    def test_new_entry_high_position_admitted(self, manager):
        sig = {"signal_type": "new_entry", "details": {"position": 5}}
        assert manager._check_admission(sig) is True

    def test_new_entry_low_position_rejected(self, manager):
        sig = {"signal_type": "new_entry", "details": {"position": 15}}
        assert manager._check_admission(sig) is False

    def test_new_entry_boundary(self, manager):
        sig = {"signal_type": "new_entry", "details": {"position": 10}}
        assert manager._check_admission(sig) is True


# ==================== 状态机测试 ====================


class TestStateMachine:
    def test_emerging_to_rising(self, manager):
        cand = {
            "status": "emerging",
            "snapshots": [{"ts": 1, "score_pos": 1500, "sum_hot": 0}],
            "status_history": [{"ts": 1, "status": "emerging", "reason": "test"}],
            "canonical_title": "测试话题",
            "updated_at": 1,
        }
        count = manager._evaluate_transitions([cand], int(time.time()))
        assert count == 1
        assert cand["status"] == "rising"

    def test_rising_to_confirmed(self, manager):
        cand = {
            "status": "rising",
            "snapshots": [{"ts": 1, "score_pos": 4000, "sum_hot": 0}],
            "status_history": [{"ts": 1, "status": "rising", "reason": "test"}],
            "canonical_title": "测试话题",
            "updated_at": 1,
        }
        count = manager._evaluate_transitions([cand], int(time.time()))
        assert count == 1
        assert cand["status"] == "confirmed"

    def test_confirmed_to_exploded(self, manager):
        cand = {
            "status": "confirmed",
            "snapshots": [{"ts": 1, "score_pos": 10000, "sum_hot": 0}],
            "status_history": [{"ts": 1, "status": "confirmed", "reason": "test"}],
            "canonical_title": "测试话题",
            "updated_at": 1,
        }
        count = manager._evaluate_transitions([cand], int(time.time()))
        assert count == 1
        assert cand["status"] == "exploded"

    def test_faded(self, manager):
        cand = {
            "status": "emerging",
            "snapshots": [{"ts": 1, "score_pos": 50, "sum_hot": 0}],
            "status_history": [{"ts": 1, "status": "emerging", "reason": "test"}],
            "canonical_title": "测试话题",
            "updated_at": 1,
        }
        count = manager._evaluate_transitions([cand], int(time.time()))
        assert count == 1
        assert cand["status"] == "faded"

    def test_tracking_to_closed(self, manager):
        cand = {
            "status": "tracking",
            "snapshots": [{"ts": 1, "score_pos": 200, "sum_hot": 0}],
            "status_history": [{"ts": 1, "status": "tracking", "reason": "test"}],
            "canonical_title": "测试话题",
            "updated_at": 1,
        }
        count = manager._evaluate_transitions([cand], int(time.time()))
        assert count == 1
        assert cand["status"] == "closed"

    def test_declining_triggers_tracking(self, manager):
        """连续 3 轮下降 → tracking"""
        cand = {
            "status": "rising",
            "snapshots": [
                {"ts": 1, "score_pos": 3000, "sum_hot": 0},
                {"ts": 2, "score_pos": 2500, "sum_hot": 0},
                {"ts": 3, "score_pos": 2000, "sum_hot": 0},
                {"ts": 4, "score_pos": 1500, "sum_hot": 0},
            ],
            "status_history": [{"ts": 1, "status": "rising", "reason": "test"}],
            "canonical_title": "测试话题",
            "updated_at": 1,
        }
        count = manager._evaluate_transitions([cand], int(time.time()))
        assert count == 1
        assert cand["status"] == "tracking"

    def test_no_transition_when_stable(self, manager):
        """score_pos 在 emerging 范围内且不下降 → 不转换"""
        cand = {
            "status": "emerging",
            "snapshots": [
                {"ts": 1, "score_pos": 500, "sum_hot": 0},
                {"ts": 2, "score_pos": 600, "sum_hot": 0},
            ],
            "status_history": [{"ts": 1, "status": "emerging", "reason": "test"}],
            "canonical_title": "测试话题",
            "updated_at": 1,
        }
        count = manager._evaluate_transitions([cand], int(time.time()))
        assert count == 0
        assert cand["status"] == "emerging"


# ==================== 衰减测试 ====================


class TestDecay:
    def test_decay_applied(self, manager):
        now = int(time.time())
        cand = {
            "status": "rising",
            "snapshots": [{"ts": now - 100, "score_pos": 1000, "sum_hot": 50000}],
            "updated_at": now - 100,
        }
        manager._apply_decay([cand], now)
        assert len(cand["snapshots"]) == 2
        assert cand["snapshots"][-1]["score_pos"] == 800  # 1000 * 0.8
        assert cand["snapshots"][-1]["sum_hot"] == 40000  # 50000 * 0.8

    def test_decay_skipped_for_signaled(self, manager):
        now = int(time.time())
        cand = {
            "status": "rising",
            "snapshots": [{"ts": now, "score_pos": 1000, "sum_hot": 50000}],
            "_has_signal": True,
            "updated_at": now,
        }
        manager._apply_decay([cand], now)
        assert len(cand["snapshots"]) == 1  # 不追加

    def test_decay_skipped_for_closed(self, manager):
        now = int(time.time())
        cand = {
            "status": "closed",
            "snapshots": [{"ts": now - 100, "score_pos": 100, "sum_hot": 1000}],
            "updated_at": now - 100,
        }
        manager._apply_decay([cand], now)
        assert len(cand["snapshots"]) == 1  # 不追加


# ==================== _is_declining 测试 ====================


class TestIsDeclining:
    def test_declining(self):
        ctx = {"snapshots": [
            {"ts": 1, "score_pos": 3000},
            {"ts": 2, "score_pos": 2500},
            {"ts": 3, "score_pos": 2000},
            {"ts": 4, "score_pos": 1500},
        ]}
        assert _is_declining(ctx, 3) is True

    def test_not_declining(self):
        ctx = {"snapshots": [
            {"ts": 1, "score_pos": 1000},
            {"ts": 2, "score_pos": 1500},
            {"ts": 3, "score_pos": 2000},
            {"ts": 4, "score_pos": 2500},
        ]}
        assert _is_declining(ctx, 3) is False

    def test_too_few_snapshots(self):
        ctx = {"snapshots": [
            {"ts": 1, "score_pos": 2000},
            {"ts": 2, "score_pos": 1500},
        ]}
        assert _is_declining(ctx, 3) is False


# ==================== score_pos 计算测试 ====================


class TestScorePos:
    def test_cross_platform_score(self, manager):
        sig = {
            "signal_type": "cross_platform",
            "details": {
                "platform_items": {
                    "weibo": {"position": 1, "hot_value": 100000},
                    "baidu": {"position": 2, "hot_value": 80000},
                    "toutiao": {"position": 5, "hot_value": 50000},
                },
            },
        }
        score = manager._calc_score_pos(sig)
        # 10000/1 + 10000/2 + 10000/5 = 10000 + 5000 + 2000 = 17000
        assert score == 17000

    def test_layer1_score(self, manager):
        sig = {
            "signal_type": "velocity",
            "position_history": [{"ts": 1, "val": 3}, {"ts": 2, "val": 2}],
        }
        score = manager._calc_score_pos(sig)
        # 10000/2 = 5000
        assert score == 5000

    def test_layer1_score_from_details(self, manager):
        sig = {
            "signal_type": "new_entry",
            "position_history": [],
            "details": {"position": 4},
        }
        score = manager._calc_score_pos(sig)
        # 10000/4 = 2500
        assert score == 2500


# ==================== 二次运行（衰减 + 无信号）测试 ====================


class TestSecondCycle:
    def test_second_cycle_no_signals(self, cycle_result, manager):
        """信号已清空，再跑一轮应只做衰减"""
        stats = manager.run_cycle()
        assert stats["signals_consumed"] == 0
        assert stats["candidates_created"] == 0
        print(f"\n[second cycle] {stats}")
