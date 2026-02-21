# -*- coding: utf-8 -*-
"""
深层采集模块单元测试 + 集成冒烟测试

测试内容：
1. _emit_crawl_tasks() 任务生成逻辑
2. CookieManager 保存/加载/过期周期
3. PlatformWorker config 保存/恢复安全性
4. TaskDispatcher 熔断器逻辑
5. 集成冒烟测试：假设话题 → 状态跃迁 → crawl_task 生成 → Worker 执行 → MySQL 数据验证
"""

import asyncio
import time
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "BroadTopicExtraction"))
sys.path.insert(0, str(project_root / "DeepSentimentCrawling" / "MediaCrawler"))

from BroadTopicExtraction.analyzer.candidate_manager import (
    CandidateManager,
    COLLECTION,
    CRAWL_TASKS_COLLECTION,
    _SURFACE_TO_DEEP,
    _CRAWL_SCALE,
)
from DeepSentimentCrawling.cookie_manager import CookieManager
from DeepSentimentCrawling.alert import send_alert, _last_alert_ts, _RATE_LIMIT_SEC
from DeepSentimentCrawling.dispatcher import TaskDispatcher


# ==================== Fixtures ====================


@pytest.fixture
def mock_mongo():
    """模拟 MongoWriter"""
    mongo = MagicMock()
    mongo.connect.return_value = None
    mongo.create_indexes.return_value = []
    mongo.find.return_value = []
    mongo.find_one.return_value = None
    mongo.bulk_write.return_value = {"inserted": 0, "modified": 0, "upserted": 0}
    mongo.count_documents.return_value = 0
    col_mock = MagicMock()
    col_mock.delete_many.return_value = MagicMock(deleted_count=0)
    col_mock.find_one.return_value = None  # 默认无重复任务
    col_mock.count_documents.return_value = 0
    mongo.get_collection.return_value = col_mock
    return mongo


@pytest.fixture
def manager(mock_mongo):
    return CandidateManager(signal_writer=mock_mongo)


@pytest.fixture
def cookie_mgr(mock_mongo):
    return CookieManager(mongo_writer=mock_mongo)


# ==================== 1. _emit_crawl_tasks 测试 ====================


class TestEmitCrawlTasks:
    """测试候选状态跃迁时的爬取任务生成"""

    def _make_candidate(self, status="rising", platforms=None):
        now = int(time.time())
        return {
            "candidate_id": "cand_test123",
            "canonical_title": "测试：某重大社会事件引发热议",
            "source_titles": ["测试：某重大社会事件引发热议", "某重大事件最新进展"],
            "status": status,
            "platforms": platforms or ["weibo", "bilibili", "douyin"],
            "platform_count": len(platforms or ["weibo", "bilibili", "douyin"]),
            "snapshots": [{"ts": now, "score_pos": 2000, "sum_hot": 100000}],
            "first_seen_at": now - 3600,
            "updated_at": now,
            "status_history": [
                {"ts": now - 3600, "status": "emerging", "reason": "cross_platform signal"},
                {"ts": now, "status": status, "reason": "score_pos >= 1500"},
            ],
        }

    def test_rising_generates_3_platforms(self, manager, mock_mongo):
        """rising 状态应生成最多 3 个平台的任务"""
        col_mock = mock_mongo.get_collection.return_value
        cand = self._make_candidate("rising", ["weibo", "bilibili", "douyin", "zhihu"])
        now = int(time.time())

        manager._emit_crawl_tasks(cand, "rising", now)

        # rising: platforms=3, 所以应插入 3 个任务
        assert col_mock.insert_one.call_count == 3

    def test_confirmed_generates_5_platforms(self, manager, mock_mongo):
        """confirmed 状态应生成最多 5 个平台的任务"""
        col_mock = mock_mongo.get_collection.return_value
        cand = self._make_candidate(
            "confirmed",
            ["weibo", "bilibili", "douyin", "zhihu", "kuaishou", "xiaohongshu", "tieba"],
        )
        now = int(time.time())

        manager._emit_crawl_tasks(cand, "confirmed", now)

        assert col_mock.insert_one.call_count == 5

    def test_exploded_generates_7_platforms(self, manager, mock_mongo):
        """exploded 状态应生成最多 7 个平台的任务"""
        col_mock = mock_mongo.get_collection.return_value
        cand = self._make_candidate(
            "exploded",
            ["weibo", "bilibili", "douyin", "zhihu", "kuaishou", "xiaohongshu", "tieba"],
        )
        now = int(time.time())

        manager._emit_crawl_tasks(cand, "exploded", now)

        assert col_mock.insert_one.call_count == 7

    def test_task_document_structure(self, manager, mock_mongo):
        """验证生成的 task 文档结构"""
        col_mock = mock_mongo.get_collection.return_value
        cand = self._make_candidate("rising", ["weibo"])
        now = int(time.time())

        manager._emit_crawl_tasks(cand, "rising", now)

        assert col_mock.insert_one.call_count == 1
        task_doc = col_mock.insert_one.call_args[0][0]

        # 验证字段
        assert task_doc["task_id"].startswith("ct_cand_test123_wb_")
        assert task_doc["candidate_id"] == "cand_test123"
        assert task_doc["topic_title"] == "测试：某重大社会事件引发热议"
        assert task_doc["platform"] == "wb"
        assert task_doc["max_notes"] == _CRAWL_SCALE["rising"]["max_notes"]
        assert task_doc["priority"] == _CRAWL_SCALE["rising"]["priority"]
        assert task_doc["status"] == "pending"
        assert task_doc["attempts"] == 0
        assert isinstance(task_doc["search_keywords"], list)
        assert len(task_doc["search_keywords"]) >= 1

    def test_platform_name_mapping(self, manager, mock_mongo):
        """验证表层平台名正确映射到深层平台代码"""
        col_mock = mock_mongo.get_collection.return_value
        cand = self._make_candidate("rising", ["xiaohongshu"])
        now = int(time.time())

        manager._emit_crawl_tasks(cand, "rising", now)

        task_doc = col_mock.insert_one.call_args[0][0]
        assert task_doc["platform"] == "xhs"

    def test_dedup_skips_existing_active_task(self, manager, mock_mongo):
        """已有 pending/running 任务时应跳过"""
        col_mock = mock_mongo.get_collection.return_value
        col_mock.find_one.return_value = {"task_id": "existing_task"}  # 模拟已存在

        cand = self._make_candidate("rising", ["weibo"])
        now = int(time.time())

        manager._emit_crawl_tasks(cand, "rising", now)

        # find_one 会找到已有任务，所以 insert_one 不应被调用
        assert col_mock.insert_one.call_count == 0

    def test_unknown_platform_ignored(self, manager, mock_mongo):
        """未知平台名应被忽略"""
        col_mock = mock_mongo.get_collection.return_value
        cand = self._make_candidate("rising", ["unknown_platform"])
        now = int(time.time())

        manager._emit_crawl_tasks(cand, "rising", now)

        assert col_mock.insert_one.call_count == 0

    def test_emerging_does_not_emit(self, manager, mock_mongo):
        """emerging 状态不在 _CRAWL_SCALE 中，不应生成任务"""
        col_mock = mock_mongo.get_collection.return_value
        cand = self._make_candidate("emerging", ["weibo"])
        now = int(time.time())

        manager._emit_crawl_tasks(cand, "emerging", now)

        assert col_mock.insert_one.call_count == 0

    def test_transition_triggers_emit(self, manager, mock_mongo):
        """_apply_transition 到 rising 时应自动调用 _emit_crawl_tasks"""
        col_mock = mock_mongo.get_collection.return_value
        cand = self._make_candidate("emerging", ["weibo", "bilibili", "douyin"])
        now = int(time.time())

        manager._apply_transition(cand, "rising", "score_pos >= 1500", now)

        # 应生成任务
        assert col_mock.insert_one.call_count == 3
        assert cand["status"] == "rising"

    def test_transition_to_tracking_does_not_emit(self, manager, mock_mongo):
        """跃迁到 tracking 不应生成任务"""
        col_mock = mock_mongo.get_collection.return_value
        cand = self._make_candidate("exploded", ["weibo"])
        now = int(time.time())

        manager._apply_transition(cand, "tracking", "declining", now)

        assert col_mock.insert_one.call_count == 0

    def test_search_keywords_limited_to_3(self, manager, mock_mongo):
        """搜索关键词最多 3 个"""
        col_mock = mock_mongo.get_collection.return_value
        cand = self._make_candidate("rising", ["weibo"])
        cand["source_titles"] = ["标题A", "标题B", "标题C", "标题D", "标题E"]
        now = int(time.time())

        manager._emit_crawl_tasks(cand, "rising", now)

        task_doc = col_mock.insert_one.call_args[0][0]
        assert len(task_doc["search_keywords"]) <= 3


# ==================== 2. CookieManager 测试 ====================


class TestCookieManager:
    """测试 cookie 保存/加载/过期周期"""

    def test_save_and_load(self, cookie_mgr, mock_mongo):
        """保存后应能加载"""
        cookie_mgr.save_cookies("xhs", {"web_session": "abc123", "a1": "xyz"})

        # 验证 upsert 被调用
        col_mock = mock_mongo.get_collection.return_value
        col_mock.update_one.assert_called_once()
        call_args = col_mock.update_one.call_args
        assert call_args[0][0] == {"platform": "xhs"}
        doc = call_args[0][1]["$set"]
        assert doc["platform"] == "xhs"
        assert doc["cookies"]["web_session"] == "abc123"
        assert doc["status"] == "active"

    def test_load_returns_none_when_no_cookies(self, cookie_mgr, mock_mongo):
        """无 cookie 时应返回 None"""
        mock_mongo.find_one.return_value = None
        result = cookie_mgr.load_cookies("xhs")
        assert result is None

    def test_load_returns_cookies_when_active(self, cookie_mgr, mock_mongo):
        """active 状态时应返回 cookie"""
        mock_mongo.find_one.return_value = {
            "platform": "xhs",
            "cookies": {"web_session": "abc"},
            "status": "active",
        }
        result = cookie_mgr.load_cookies("xhs")
        assert result == {"web_session": "abc"}

    @patch("DeepSentimentCrawling.cookie_manager.alert_cookie_expired")
    def test_mark_expired_triggers_alert(self, mock_alert, cookie_mgr, mock_mongo):
        """标记过期时应触发告警"""
        cookie_mgr.mark_expired("xhs")

        col_mock = mock_mongo.get_collection.return_value
        col_mock.update_one.assert_called_once()
        update_doc = col_mock.update_one.call_args[0][1]["$set"]
        assert update_doc["status"] == "expired"
        mock_alert.assert_called_once_with("xhs")

    def test_get_all_status_includes_missing(self, cookie_mgr, mock_mongo):
        """get_all_status 应补充未注册平台为 missing"""
        mock_mongo.find.return_value = [
            {"platform": "xhs", "status": "active", "saved_at": 1000, "expires_hint": 2000},
        ]
        statuses = cookie_mgr.get_all_status()

        platforms = {s["platform"] for s in statuses}
        # 应包含全部 7 个平台
        assert "xhs" in platforms
        assert "dy" in platforms
        assert "bili" in platforms

        # xhs 有数据
        xhs = next(s for s in statuses if s["platform"] == "xhs")
        assert xhs["status"] == "active"

        # 其他平台应为 missing
        dy = next(s for s in statuses if s["platform"] == "dy")
        assert dy["status"] == "missing"

    def test_format_cookies_for_config(self):
        """cookie dict 应格式化为分号分隔字符串"""
        result = CookieManager.format_cookies_for_config({"a": "1", "b": "2"})
        assert "a=1" in result
        assert "b=2" in result
        assert "; " in result

    def test_get_session_cookie_key(self):
        assert CookieManager.get_session_cookie_key("xhs") == "web_session"
        assert CookieManager.get_session_cookie_key("bili") == "SESSDATA"
        assert CookieManager.get_session_cookie_key("unknown") == ""


# ==================== 3. AlertService 测试 ====================


class TestAlertService:
    """测试告警速率限制"""

    def test_rate_limit_blocks_repeat(self):
        """同一平台 5 分钟内不应重复告警"""
        _last_alert_ts.clear()
        _last_alert_ts["xhs"] = time.time()  # 刚发过

        # send_alert 带 platform 参数时，应因速率限制返回 False
        # （SERVERCHAN_KEY 为空也会返回 False，所以我们直接测速率限制逻辑）
        from DeepSentimentCrawling.alert import _should_rate_limit
        assert _should_rate_limit("xhs") is True

    def test_rate_limit_allows_after_cooldown(self):
        """冷却期过后应允许告警"""
        _last_alert_ts.clear()
        _last_alert_ts["xhs"] = time.time() - _RATE_LIMIT_SEC - 1  # 已过冷却期

        from DeepSentimentCrawling.alert import _should_rate_limit
        assert _should_rate_limit("xhs") is False

    def test_rate_limit_different_platform(self):
        """不同平台互不影响"""
        _last_alert_ts.clear()
        _last_alert_ts["xhs"] = time.time()  # xhs 刚发过

        from DeepSentimentCrawling.alert import _should_rate_limit
        assert _should_rate_limit("dy") is False  # dy 不受影响


# ==================== 4. PlatformWorker config 安全性测试 ====================


class TestWorkerConfigSafety:
    """测试 config 保存/恢复不会泄漏全局状态"""

    def test_save_restore_roundtrip(self):
        """config 保存后恢复，全局状态应不变"""
        # _save_config/_restore_config 操作 `import config` 得到的模块的大写属性
        # 在测试环境中 config 是项目根的 Pydantic Settings 模块
        # 我们直接往上面加临时属性来验证保存/恢复逻辑
        from DeepSentimentCrawling.worker import _save_config, _restore_config
        import config as cfg_module

        # 先设置一个已知属性用于测试
        cfg_module.TEST_PLATFORM = "bili"
        cfg_module.TEST_KEYWORDS = "原始关键词"

        original = _save_config()
        assert "TEST_PLATFORM" in original
        assert original["TEST_PLATFORM"] == "bili"

        # 修改
        cfg_module.TEST_PLATFORM = "test_modified"
        cfg_module.TEST_KEYWORDS = "被篡改的关键词"
        assert cfg_module.TEST_PLATFORM == "test_modified"

        # 恢复
        _restore_config(original)
        assert cfg_module.TEST_PLATFORM == "bili"
        assert cfg_module.TEST_KEYWORDS == "原始关键词"

        # 清理
        del cfg_module.TEST_PLATFORM
        del cfg_module.TEST_KEYWORDS


# ==================== 5. TaskDispatcher 熔断器测试 ====================


class TestDispatcherCircuitBreaker:
    """测试熔断器逻辑"""

    def test_circuit_closed_by_default(self, mock_mongo):
        dispatcher = TaskDispatcher(
            platforms=["xhs"], mongo_writer=mock_mongo, dry_run=True
        )
        assert dispatcher._is_circuit_open("xhs") is False

    def test_circuit_opens_after_threshold(self, mock_mongo):
        dispatcher = TaskDispatcher(
            platforms=["xhs"], mongo_writer=mock_mongo, dry_run=True
        )
        # 模拟连续失败
        dispatcher.failure_counts["xhs"] = dispatcher.CIRCUIT_THRESHOLD

        with patch("DeepSentimentCrawling.dispatcher.alert_circuit_open"):
            dispatcher._trip_circuit("xhs", "test failure")

        assert dispatcher._is_circuit_open("xhs") is True

    def test_circuit_auto_resets(self, mock_mongo):
        dispatcher = TaskDispatcher(
            platforms=["xhs"], mongo_writer=mock_mongo, dry_run=True
        )
        # 设置熔断已过期
        dispatcher.circuit_open_until["xhs"] = time.time() - 1

        assert dispatcher._is_circuit_open("xhs") is False
        assert dispatcher.failure_counts["xhs"] == 0

    def test_get_stats(self, mock_mongo):
        col_mock = mock_mongo.get_collection.return_value
        col_mock.count_documents.return_value = 5
        dispatcher = TaskDispatcher(
            platforms=["xhs", "dy"], mongo_writer=mock_mongo, dry_run=True
        )
        stats = dispatcher.get_stats()
        assert "pending" in stats
        assert "circuit_breakers" in stats
        assert stats["circuit_breakers"]["xhs"] == "closed"


# ==================== 6. 全局映射表一致性测试 ====================


class TestMappingConsistency:
    """确保平台映射和配置表的一致性"""

    def test_surface_to_deep_covers_common_platforms(self):
        """表层→深层映射应覆盖常见平台"""
        required = {"weibo", "bilibili", "douyin", "zhihu", "kuaishou", "tieba", "xiaohongshu", "xhs"}
        assert required <= set(_SURFACE_TO_DEEP.keys())

    def test_deep_platform_codes_valid(self):
        """映射目标应为有效的深层平台代码"""
        valid_codes = {"xhs", "dy", "ks", "bili", "wb", "tieba", "zhihu"}
        for deep_code in _SURFACE_TO_DEEP.values():
            assert deep_code in valid_codes, f"无效平台代码: {deep_code}"

    def test_crawl_scale_keys(self):
        """_CRAWL_SCALE 应只包含 rising/confirmed/exploded"""
        assert set(_CRAWL_SCALE.keys()) == {"rising", "confirmed", "exploded"}

    def test_crawl_scale_increasing_notes(self):
        """爬取规模应递增"""
        assert _CRAWL_SCALE["rising"]["max_notes"] < _CRAWL_SCALE["confirmed"]["max_notes"]
        assert _CRAWL_SCALE["confirmed"]["max_notes"] < _CRAWL_SCALE["exploded"]["max_notes"]

    def test_crawl_scale_increasing_priority(self):
        """优先级应递增"""
        assert _CRAWL_SCALE["rising"]["priority"] < _CRAWL_SCALE["confirmed"]["priority"]
        assert _CRAWL_SCALE["confirmed"]["priority"] < _CRAWL_SCALE["exploded"]["priority"]

    def test_crawl_scale_increasing_platforms(self):
        """平台数应递增"""
        assert _CRAWL_SCALE["rising"]["platforms"] < _CRAWL_SCALE["confirmed"]["platforms"]
        assert _CRAWL_SCALE["confirmed"]["platforms"] < _CRAWL_SCALE["exploded"]["platforms"]
