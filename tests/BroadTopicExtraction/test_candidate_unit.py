# -*- coding: utf-8 -*-
"""
候选话题管理 - 单元测试

基于 MongoDB 中实际信号数据结构编写，不依赖真实数据库连接。
使用 unittest.mock 模拟 MongoWriter。
"""

import time
from unittest.mock import MagicMock, patch

import pytest

from BroadTopicExtraction.analyzer.candidate_manager import (
    CandidateManager,
    COLLECTION,
    _ACTIVE_STATUSES,
    _is_declining,
)


# ==================== 真实信号数据 fixtures ====================


def _make_position_jump_signal(
    title="崩坏星穹铁道2.8前瞻",
    platform="bilibili",
    prev_pos=25,
    curr_pos=15,
    jump=10,
):
    """基于真实 position_jump 信号结构"""
    now = int(time.time())
    import hashlib

    title_hash = hashlib.md5(title.encode("utf-8")).hexdigest()[:12]
    return {
        "signal_id": f"position_jump_{title_hash}_{platform}",
        "consumed": False,
        "details": {
            "previous_position": prev_pos,
            "current_position": curr_pos,
            "jump": jump,
        },
        "detected_at": now,
        "first_seen_at": now - 3600,
        "hot_value_history": [],
        "last_seen_at": now,
        "layer": 1,
        "platform": platform,
        "platforms": [],
        "position_history": [
            {"ts": now - 1800, "val": prev_pos},
            {"ts": now, "val": curr_pos},
        ],
        "signal_type": "position_jump",
        "source_collection": "aggregator",
        "title": title,
        "updated_at": now,
    }


def _make_cross_platform_signal(
    title="谷歌推出Gemini 3.1 Pro模型",
    platform_items=None,
):
    """基于真实 cross_platform 信号结构"""
    now = int(time.time())
    import hashlib

    title_hash = hashlib.md5(title.encode("utf-8")).hexdigest()[:12]
    if platform_items is None:
        platform_items = {
            "tencent": {"title": title, "position": 2, "hot_value": 3967406,
                        "hot_value_history": [], "position_history": []},
            "sina": {"title": "谷歌发布Gemini 3.1 Pro", "position": 25, "hot_value": 1680000,
                     "hot_value_history": [], "position_history": []},
            "netease": {"title": "谷歌Gemini 3.1 Pro正式发布", "position": 5, "hot_value": 3834,
                        "hot_value_history": [], "position_history": []},
        }
    platforms = sorted(platform_items.keys())
    return {
        "signal_id": f"cross_platform_{title_hash}",
        "consumed": False,
        "details": {
            "platform_count": len(platforms),
            "platform_items": platform_items,
            "common_keywords": ["谷歌", "Gemini"],
        },
        "detected_at": now,
        "layer": 2,
        "platform": None,
        "platforms": platforms,
        "signal_type": "cross_platform",
        "source_collection": "cross",
        "title": title,
        "updated_at": now,
    }


def _make_new_entry_signal(title="突发：某地发生地震", position=3, hot_value=500000):
    """基于真实 new_entry 信号结构"""
    now = int(time.time())
    import hashlib

    title_hash = hashlib.md5(title.encode("utf-8")).hexdigest()[:12]
    return {
        "signal_id": f"new_entry_{title_hash}_baidu",
        "consumed": False,
        "details": {
            "position": position,
            "hot_value": hot_value,
            "age_seconds": 300,
        },
        "detected_at": now,
        "first_seen_at": now - 300,
        "hot_value_history": [{"ts": now, "val": hot_value}],
        "last_seen_at": now,
        "layer": 1,
        "platform": "baidu",
        "platforms": [],
        "position_history": [{"ts": now, "val": position}],
        "signal_type": "new_entry",
        "source_collection": "hot_national",
        "title": title,
        "updated_at": now,
    }


def _make_velocity_signal(title="某热点事件持续发酵", prev_val=100000, curr_val=200000):
    """基于真实 velocity 信号结构"""
    now = int(time.time())
    import hashlib

    title_hash = hashlib.md5(title.encode("utf-8")).hexdigest()[:12]
    return {
        "signal_id": f"velocity_{title_hash}_baidu",
        "consumed": False,
        "details": {
            "previous_value": prev_val,
            "current_value": curr_val,
            "growth_rate": (curr_val - prev_val) / prev_val,
        },
        "detected_at": now,
        "first_seen_at": now - 7200,
        "hot_value_history": [
            {"ts": now - 1800, "val": prev_val},
            {"ts": now, "val": curr_val},
        ],
        "last_seen_at": now,
        "layer": 1,
        "platform": "baidu",
        "platforms": [],
        "position_history": [{"ts": now, "val": 5}],
        "signal_type": "velocity",
        "source_collection": "hot_national",
        "title": title,
        "updated_at": now,
    }


# ==================== Mock 工具 ====================


@pytest.fixture
def mock_mongo():
    """模拟 MongoWriter，不连真实数据库"""
    mongo = MagicMock()
    mongo.connect.return_value = None
    mongo.create_indexes.return_value = []
    mongo.find.return_value = []
    mongo.bulk_write.return_value = {"inserted": 0, "modified": 0, "upserted": 0}
    mongo.count_documents.return_value = 0
    col_mock = MagicMock()
    col_mock.delete_many.return_value = MagicMock(deleted_count=0)
    mongo.get_collection.return_value = col_mock
    return mongo


@pytest.fixture
def manager(mock_mongo):
    return CandidateManager(signal_writer=mock_mongo)


# ==================== score_pos 计算（真实数据结构） ====================


class TestScorePosRealData:
    """用真实信号结构验证 score_pos 计算"""

    def test_cross_platform_17_platforms(self, manager):
        """真实数据：17 个平台的 cross_platform 信号"""
        sig = _make_cross_platform_signal(
            title="谷歌推出Gemini 3.1 Pro模型",
            platform_items={
                "tencent": {"title": "t", "position": 2, "hot_value": 3967406},
                "sina": {"title": "t", "position": 25, "hot_value": 1680000},
                "netease": {"title": "t", "position": 5, "hot_value": 3834},
                "douyin": {"title": "t", "position": 35, "hot_value": 7736305},
                "baidu": {"title": "t", "position": 34, "hot_value": 4741681},
                "toutiao": {"title": "t", "position": 30, "hot_value": 1401021},
                "ithome": {"title": "t", "position": 31, "hot_value": None},
                "juejin": {"title": "t", "position": 2, "hot_value": 616},
                "36kr": {"title": "t", "position": 38, "hot_value": None},
                "cls": {"title": "t", "position": 19, "hot_value": None},
                "zhihu": {"title": "t", "position": 44, "hot_value": 652},
                "bilibili": {"title": "t", "position": 5, "hot_value": 652},
                "tieba": {"title": "t", "position": 25, "hot_value": None},
                "huxiu": {"title": "t", "position": 9, "hot_value": 652},
                "v2ex": {"title": "t", "position": 28, "hot_value": None},
                "weibo": {"title": "t", "position": 25, "hot_value": 652},
                "wallstreetcn": {"title": "t", "position": 26, "hot_value": None},
            },
        )
        score = manager._calc_score_pos(sig)
        expected = sum(
            int(10000 / p) for p in [2, 25, 5, 35, 34, 30, 31, 2, 38, 19, 44, 5, 25, 9, 28, 25, 26]
        )
        assert score == expected

    def test_cross_platform_3_platforms(self, manager):
        """真实数据：3 个平台"""
        sig = _make_cross_platform_signal(
            title="某事件",
            platform_items={
                "douyin": {"title": "t", "position": 24, "hot_value": 7763504},
                "baidu": {"title": "t", "position": 48, "hot_value": 3422789},
                "tieba": {"title": "t", "position": 26, "hot_value": None},
            },
        )
        score = manager._calc_score_pos(sig)
        assert score == int(10000 / 24) + int(10000 / 48) + int(10000 / 26)

    def test_cross_platform_sum_hot_skips_none(self, manager):
        """hot_value 为 None 的平台不计入 sum_hot"""
        sig = _make_cross_platform_signal(
            title="某事件",
            platform_items={
                "baidu": {"title": "t", "position": 10, "hot_value": 5000000},
                "tieba": {"title": "t", "position": 20, "hot_value": None},
                "bilibili": {"title": "t", "position": 5, "hot_value": 1000},
            },
        )
        hot = manager._calc_sum_hot(sig)
        assert hot == 5001000

    def test_position_jump_score(self, manager):
        """position_jump 信号：用 position_history 最后一条"""
        sig = _make_position_jump_signal(curr_pos=15)
        score = manager._calc_score_pos(sig)
        assert score == int(10000 / 15)

    def test_position_jump_score_top1(self, manager):
        sig = _make_position_jump_signal(curr_pos=1)
        score = manager._calc_score_pos(sig)
        assert score == 10000

    def test_velocity_score_from_position_history(self, manager):
        sig = _make_velocity_signal()
        score = manager._calc_score_pos(sig)
        assert score == int(10000 / 5)

    def test_new_entry_score_fallback_to_details(self, manager):
        sig = _make_new_entry_signal(position=3)
        score = manager._calc_score_pos(sig)
        assert score == int(10000 / 3)


# ==================== 候选创建（真实数据结构） ====================


class TestCreateCandidateRealData:
    def test_from_cross_platform(self, manager):
        sig = _make_cross_platform_signal()
        now = int(time.time())
        cand = manager._create_candidate(sig, now)

        assert cand["candidate_id"].startswith("cand_")
        assert cand["canonical_title"] == "谷歌推出Gemini 3.1 Pro模型"
        assert cand["status"] == "emerging"
        assert set(cand["platforms"]) == {"netease", "sina", "tencent"}
        assert cand["platform_count"] == 3
        assert len(cand["snapshots"]) == 1
        assert cand["snapshots"][0]["score_pos"] > 0
        assert "谷歌推出Gemini 3.1 Pro模型" in cand["source_titles"]
        assert "谷歌发布Gemini 3.1 Pro" in cand["source_titles"]
        assert "谷歌Gemini 3.1 Pro正式发布" in cand["source_titles"]

    def test_from_position_jump(self, manager):
        sig = _make_position_jump_signal()
        now = int(time.time())
        cand = manager._create_candidate(sig, now)

        assert cand["status"] == "emerging"
        assert cand["platforms"] == ["bilibili"]
        assert len(cand["source_titles"]) == 1
        assert cand["status_history"][0]["reason"] == "position_jump signal"

    def test_from_new_entry(self, manager):
        sig = _make_new_entry_signal()
        now = int(time.time())
        cand = manager._create_candidate(sig, now)

        assert cand["platforms"] == ["baidu"]
        assert cand["status_history"][0]["reason"] == "new_entry signal"


# ==================== 候选更新（合并信号） ====================


class TestUpdateCandidateRealData:
    def test_merge_new_platform(self, manager):
        now = int(time.time())
        cand = manager._create_candidate(_make_cross_platform_signal(), now)
        sig2 = _make_position_jump_signal(
            title="谷歌推出Gemini 3.1 Pro最新消息",
            platform="bilibili",
        )
        manager._update_candidate(cand, sig2, now)

        assert "bilibili" in cand["platforms"]
        assert cand["platform_count"] == 4
        assert "谷歌推出Gemini 3.1 Pro最新消息" in cand["source_titles"]

    def test_merge_same_ts_accumulates_score(self, manager):
        now = int(time.time())
        cand = manager._create_candidate(
            _make_position_jump_signal(title="事件A", curr_pos=10), now
        )
        initial_score = cand["snapshots"][-1]["score_pos"]

        sig2 = _make_position_jump_signal(title="事件A后续", platform="weibo", curr_pos=5)
        manager._update_candidate(cand, sig2, now)

        assert cand["snapshots"][-1]["score_pos"] == initial_score + int(10000 / 5)


# ==================== 准入标准（真实信号类型） ====================


class TestAdmissionRealData:
    def test_position_jump_always_admitted(self, manager):
        assert manager._check_admission(_make_position_jump_signal()) is True

    def test_cross_platform_always_admitted(self, manager):
        assert manager._check_admission(_make_cross_platform_signal()) is True

    def test_velocity_always_admitted(self, manager):
        assert manager._check_admission(_make_velocity_signal()) is True

    def test_new_entry_pos3_admitted(self, manager):
        assert manager._check_admission(_make_new_entry_signal(position=3)) is True

    def test_new_entry_pos10_admitted(self, manager):
        assert manager._check_admission(_make_new_entry_signal(position=10)) is True

    def test_new_entry_pos11_rejected(self, manager):
        assert manager._check_admission(_make_new_entry_signal(position=11)) is False

    def test_new_entry_pos50_rejected(self, manager):
        assert manager._check_admission(_make_new_entry_signal(position=50)) is False


# ==================== run_cycle 完整流程（mock） ====================


class TestRunCycleMocked:
    def test_no_signals_no_candidates(self, manager, mock_mongo):
        mock_mongo.find.return_value = []
        stats = manager.run_cycle()
        assert stats["signals_consumed"] == 0
        assert stats["candidates_created"] == 0

    def test_no_signals_with_active_candidates_decays(self, manager, mock_mongo):
        now = int(time.time())
        existing_cand = {
            "candidate_id": "cand_abc",
            "canonical_title": "测试",
            "source_titles": ["测试"],
            "status": "rising",
            "platforms": ["baidu"],
            "platform_count": 1,
            "snapshots": [{"ts": now - 1800, "score_pos": 2000, "sum_hot": 100000}],
            "first_seen_at": now - 3600,
            "updated_at": now - 1800,
            "status_history": [{"ts": now - 3600, "status": "emerging", "reason": "test"}],
        }

        def find_side_effect(collection, query, **kwargs):
            if collection == "signals":
                return []
            if collection == COLLECTION:
                return [existing_cand]
            return []

        mock_mongo.find.side_effect = find_side_effect
        stats = manager.run_cycle()

        assert stats["signals_consumed"] == 0
        mock_mongo.bulk_write.assert_called()

    def test_cross_platform_creates_candidate(self, manager, mock_mongo):
        sig = _make_cross_platform_signal()
        call_count = [0]

        def find_side_effect(collection, query, **kwargs):
            if collection == "signals":
                if call_count[0] == 0:
                    call_count[0] += 1
                    return [sig]
                return []
            return []

        mock_mongo.find.side_effect = find_side_effect
        col_mock = MagicMock()
        col_mock.delete_many.return_value = MagicMock(deleted_count=1)
        mock_mongo.get_collection.return_value = col_mock

        stats = manager.run_cycle()

        assert stats["signals_consumed"] == 1
        assert stats["candidates_created"] == 1
        assert stats["signals_deleted"] == 1

    def test_mixed_signals_flow(self, manager, mock_mongo):
        cross_sig = _make_cross_platform_signal(title="谷歌Gemini发布")
        jump_sig = _make_position_jump_signal(title="谷歌Gemini最新动态", curr_pos=3)
        call_count = [0]

        def find_side_effect(collection, query, **kwargs):
            if collection == "signals":
                if call_count[0] == 0:
                    call_count[0] += 1
                    return [cross_sig, jump_sig]
                return []
            return []

        mock_mongo.find.side_effect = find_side_effect
        col_mock = MagicMock()
        col_mock.delete_many.return_value = MagicMock(deleted_count=2)
        mock_mongo.get_collection.return_value = col_mock

        stats = manager.run_cycle()

        assert stats["signals_consumed"] == 2
        assert stats["candidates_created"] >= 1

    def test_unrelated_new_entry_low_pos_rejected(self, manager, mock_mongo):
        sig = _make_new_entry_signal(title="某冷门话题无人关注", position=50)
        call_count = [0]

        def find_side_effect(collection, query, **kwargs):
            if collection == "signals":
                if call_count[0] == 0:
                    call_count[0] += 1
                    return [sig]
                return []
            return []

        mock_mongo.find.side_effect = find_side_effect
        col_mock = MagicMock()
        col_mock.delete_many.return_value = MagicMock(deleted_count=1)
        mock_mongo.get_collection.return_value = col_mock

        stats = manager.run_cycle()

        assert stats["signals_consumed"] == 1
        assert stats["candidates_created"] == 0


# ==================== 状态机（真实场景模拟） ====================


class TestStateMachineScenarios:
    def test_hot_topic_lifecycle(self, manager):
        """模拟热点话题完整生命周期"""
        now = int(time.time())
        cand = {
            "candidate_id": "cand_lifecycle",
            "canonical_title": "重大事件",
            "status": "emerging",
            "snapshots": [{"ts": now, "score_pos": 500, "sum_hot": 10000}],
            "status_history": [{"ts": now, "status": "emerging", "reason": "test"}],
            "updated_at": now,
        }

        # emerging (score < 1500) → 不变
        manager._evaluate_transitions([cand], now)
        assert cand["status"] == "emerging"

        # → rising
        cand["snapshots"].append({"ts": now + 1, "score_pos": 1500, "sum_hot": 50000})
        manager._evaluate_transitions([cand], now + 1)
        assert cand["status"] == "rising"

        # → confirmed
        cand["snapshots"].append({"ts": now + 2, "score_pos": 4000, "sum_hot": 200000})
        manager._evaluate_transitions([cand], now + 2)
        assert cand["status"] == "confirmed"

        # → exploded
        cand["snapshots"].append({"ts": now + 3, "score_pos": 10000, "sum_hot": 500000})
        manager._evaluate_transitions([cand], now + 3)
        assert cand["status"] == "exploded"

        # 连续 3 轮下降 → tracking
        cand["snapshots"].extend([
            {"ts": now + 4, "score_pos": 8000, "sum_hot": 400000},
            {"ts": now + 5, "score_pos": 6000, "sum_hot": 300000},
            {"ts": now + 6, "score_pos": 4000, "sum_hot": 200000},
        ])
        manager._evaluate_transitions([cand], now + 6)
        assert cand["status"] == "tracking"

        # score < 300 → closed
        cand["snapshots"].append({"ts": now + 7, "score_pos": 200, "sum_hot": 5000})
        manager._evaluate_transitions([cand], now + 7)
        assert cand["status"] == "closed"

    def test_flash_topic_fades(self, manager):
        now = int(time.time())
        cand = {
            "candidate_id": "cand_flash",
            "canonical_title": "一闪而过的话题",
            "status": "emerging",
            "snapshots": [{"ts": now, "score_pos": 80, "sum_hot": 1000}],
            "status_history": [{"ts": now, "status": "emerging", "reason": "test"}],
            "updated_at": now,
        }
        manager._evaluate_transitions([cand], now)
        assert cand["status"] == "faded"

    def test_closed_and_faded_are_terminal(self, manager):
        now = int(time.time())
        for status in ("closed", "faded"):
            cand = {
                "candidate_id": f"cand_{status}",
                "canonical_title": "已结束",
                "status": status,
                "snapshots": [{"ts": now, "score_pos": 50000, "sum_hot": 999999}],
                "status_history": [{"ts": now, "status": status, "reason": "test"}],
                "updated_at": now,
            }
            count = manager._evaluate_transitions([cand], now)
            assert count == 0
            assert cand["status"] == status


# ==================== 衰减（真实场景） ====================


class TestDecayScenarios:
    def test_multiple_rounds_decay(self, manager):
        now = int(time.time())
        cand = {
            "status": "rising",
            "snapshots": [{"ts": now, "score_pos": 1000, "sum_hot": 50000}],
            "updated_at": now,
        }

        for i in range(1, 4):
            manager._apply_decay([cand], now + i * 1800)

        assert len(cand["snapshots"]) == 4
        # 1000 → 800 → 640 → 512
        assert cand["snapshots"][-1]["score_pos"] == 512
        assert cand["snapshots"][-1]["sum_hot"] == int(50000 * 0.8 * 0.8 * 0.8)
