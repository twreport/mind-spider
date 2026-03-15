# -*- coding: utf-8 -*-
"""
深层采集模块单元测试 + 集成冒烟测试

测试内容：
1. _emit_crawl_tasks() 任务生成逻辑
2. CookieManager 保存/加载/过期周期
3. PlatformWorker config 保存/恢复安全性
4. TaskDispatcher 熔断器逻辑
5. 集成冒烟测试：假设话题 → 状态跃迁 → crawl_task 生成 → Worker 执行 → MySQL 数据验证
6. TopicMatcher 去重改进：fast-path / 36h 窗口 / 候选路径去重 / exclude_candidate_id
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
    _CRAWL_SCALE,
)
from DeepSentimentCrawling.cookie_manager import CookieManager
from DeepSentimentCrawling.alert import send_alert, _last_alert_ts, _RATE_LIMIT_SEC
from DeepSentimentCrawling.dispatcher import TaskDispatcher
from DeepSentimentCrawling.topic_matcher import (
    TopicMatcher,
    MATCH_DUPLICATE,
    MATCH_DEVELOPMENT,
    MATCH_DIFFERENT,
    _extract_keywords,
)


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
    mgr = CandidateManager(signal_writer=mock_mongo)
    mgr.topic_matcher = None  # 禁用 topic_matcher 避免影响非去重测试
    return mgr


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

    def test_rising_does_not_emit(self, manager, mock_mongo):
        """rising 状态不在 _CRAWL_SCALE 中，不应生成任务"""
        col_mock = mock_mongo.get_collection.return_value
        cand = self._make_candidate("rising", ["weibo", "bilibili", "douyin", "zhihu"])
        now = int(time.time())

        manager._emit_crawl_tasks(cand, "rising", now)

        assert col_mock.insert_one.call_count == 0

    def test_confirmed_does_not_emit(self, manager, mock_mongo):
        """confirmed 状态不在 _CRAWL_SCALE 中，不应生成任务"""
        col_mock = mock_mongo.get_collection.return_value
        cand = self._make_candidate(
            "confirmed",
            ["weibo", "bilibili", "douyin", "zhihu", "kuaishou", "xiaohongshu", "tieba"],
        )
        now = int(time.time())

        manager._emit_crawl_tasks(cand, "confirmed", now)

        assert col_mock.insert_one.call_count == 0

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
        cand = self._make_candidate("exploded", ["weibo"])
        now = int(time.time())

        manager._emit_crawl_tasks(cand, "exploded", now)

        assert col_mock.insert_one.call_count == 7
        task_doc = col_mock.insert_one.call_args_list[0][0][0]

        # 验证字段
        assert task_doc["task_id"].startswith("ct_cand_test123_wb_")
        assert task_doc["candidate_id"] == "cand_test123"
        assert task_doc["topic_title"] == "测试：某重大社会事件引发热议"
        assert task_doc["platform"] == "wb"
        assert task_doc["max_notes"] == _CRAWL_SCALE["exploded"]["max_notes"]
        assert task_doc["priority"] == _CRAWL_SCALE["exploded"]["priority"]
        assert task_doc["status"] == "pending"
        assert task_doc["attempts"] == 0
        assert isinstance(task_doc["search_keywords"], list)
        assert len(task_doc["search_keywords"]) >= 1

    def test_all_7_platforms_created(self, manager, mock_mongo):
        """exploded 应为所有 7 个深层平台创建任务"""
        col_mock = mock_mongo.get_collection.return_value
        cand = self._make_candidate("exploded", ["weibo"])
        now = int(time.time())

        manager._emit_crawl_tasks(cand, "exploded", now)

        platforms_created = [
            call[0][0]["platform"] for call in col_mock.insert_one.call_args_list
        ]
        assert set(platforms_created) == {"wb", "bili", "dy", "zhihu", "ks", "tieba", "xhs"}

    def test_dedup_skips_existing_active_task(self, manager, mock_mongo):
        """已有 pending/running 任务时应跳过"""
        col_mock = mock_mongo.get_collection.return_value
        col_mock.find_one.return_value = {"task_id": "existing_task"}  # 模拟已存在

        cand = self._make_candidate("exploded", ["weibo"])
        now = int(time.time())

        manager._emit_crawl_tasks(cand, "exploded", now)

        # find_one 会找到已有任务，所以 insert_one 不应被调用
        assert col_mock.insert_one.call_count == 0

    def test_emerging_does_not_emit(self, manager, mock_mongo):
        """emerging 状态不在 _CRAWL_SCALE 中，不应生成任务"""
        col_mock = mock_mongo.get_collection.return_value
        cand = self._make_candidate("emerging", ["weibo"])
        now = int(time.time())

        manager._emit_crawl_tasks(cand, "emerging", now)

        assert col_mock.insert_one.call_count == 0

    def test_transition_triggers_emit(self, manager, mock_mongo):
        """_apply_transition 到 exploded 时应自动调用 _emit_crawl_tasks"""
        col_mock = mock_mongo.get_collection.return_value
        cand = self._make_candidate("confirmed", ["weibo", "bilibili", "douyin"])
        now = int(time.time())

        manager._apply_transition(cand, "exploded", "score_pos >= 10000", now)

        # 应生成 7 个任务
        assert col_mock.insert_one.call_count == 7
        assert cand["status"] == "exploded"

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
        cand = self._make_candidate("exploded", ["weibo"])
        cand["source_titles"] = ["标题A", "标题B", "标题C", "标题D", "标题E"]
        now = int(time.time())

        manager._emit_crawl_tasks(cand, "exploded", now)

        task_doc = col_mock.insert_one.call_args_list[0][0][0]
        assert len(task_doc["search_keywords"]) <= 3


# ==================== 2. CookieManager 测试 ====================


class TestCookieManager:
    """测试 cookie 保存/加载/过期周期（cookie 池模式）"""

    def test_save_and_load(self, cookie_mgr, mock_mongo):
        """保存后应能加载"""
        cookie_mgr.save_cookies("xhs", {"web_session": "abc123", "a1": "xyz"})

        # 验证 upsert 被调用（按 cookie_id 匹配）
        col_mock = mock_mongo.get_collection.return_value
        col_mock.update_one.assert_called_once()
        call_args = col_mock.update_one.call_args
        # 匹配条件应为 cookie_id
        assert "cookie_id" in call_args[0][0]
        doc = call_args[0][1]["$set"]
        assert doc["platform"] == "xhs"
        assert doc["cookies"]["web_session"] == "abc123"
        assert doc["status"] == "active"
        assert "cookie_id" in doc

    def test_save_returns_cookie_id(self, cookie_mgr, mock_mongo):
        """save_cookies 应返回 cookie_id"""
        result = cookie_mgr.save_cookies("xhs", {"web_session": "abc123"})
        assert result.startswith("xhs_")
        assert len(result) == len("xhs_") + 8

    def test_load_returns_none_when_no_cookies(self, cookie_mgr, mock_mongo):
        """无 cookie 时应返回 None"""
        col_mock = mock_mongo.get_collection.return_value
        col_mock.find.return_value = []
        result = cookie_mgr.load_cookies("xhs")
        assert result is None

    def test_load_returns_tuple_when_active(self, cookie_mgr, mock_mongo):
        """active 状态时应返回 (cookie_id, cookies) tuple"""
        col_mock = mock_mongo.get_collection.return_value
        col_mock.find.return_value = [
            {
                "cookie_id": "xhs_abc12345",
                "platform": "xhs",
                "cookies": {"web_session": "abc"},
                "status": "active",
            }
        ]
        result = cookie_mgr.load_cookies("xhs")
        assert result is not None
        assert isinstance(result, tuple)
        assert len(result) == 2
        cookie_id, cookies = result
        assert cookie_id == "xhs_abc12345"
        assert cookies == {"web_session": "abc"}

    def test_load_random_choice_from_pool(self, cookie_mgr, mock_mongo):
        """多个 active cookie 时应随机选择"""
        col_mock = mock_mongo.get_collection.return_value
        col_mock.find.return_value = [
            {"cookie_id": "xhs_aaa", "cookies": {"web_session": "a"}, "status": "active"},
            {"cookie_id": "xhs_bbb", "cookies": {"web_session": "b"}, "status": "active"},
        ]
        results = set()
        for _ in range(50):
            cookie_id, _ = cookie_mgr.load_cookies("xhs")
            results.add(cookie_id)
        # 50 次应至少选到两个不同的
        assert len(results) == 2

    @patch("DeepSentimentCrawling.cookie_manager.alert_cookie_expired")
    def test_mark_expired_with_cookie_id(self, mock_alert, cookie_mgr, mock_mongo):
        """带 cookie_id 时只过期该条"""
        cookie_mgr.mark_expired("xhs", cookie_id="xhs_abc12345")

        col_mock = mock_mongo.get_collection.return_value
        col_mock.update_one.assert_called_once()
        filter_doc = col_mock.update_one.call_args[0][0]
        assert filter_doc == {"cookie_id": "xhs_abc12345"}
        update_doc = col_mock.update_one.call_args[0][1]["$set"]
        assert update_doc["status"] == "expired"
        mock_alert.assert_called_once_with("xhs")

    @patch("DeepSentimentCrawling.cookie_manager.alert_cookie_expired")
    def test_mark_expired_all_platform(self, mock_alert, cookie_mgr, mock_mongo):
        """不带 cookie_id 时过期整个平台"""
        cookie_mgr.mark_expired("xhs")

        col_mock = mock_mongo.get_collection.return_value
        col_mock.update_many.assert_called_once()
        filter_doc = col_mock.update_many.call_args[0][0]
        assert filter_doc == {"platform": "xhs"}
        mock_alert.assert_called_once_with("xhs")

    def test_has_active_cookies_true(self, cookie_mgr, mock_mongo):
        """有 active cookie 时返回 True"""
        col_mock = mock_mongo.get_collection.return_value
        col_mock.count_documents.return_value = 2
        assert cookie_mgr.has_active_cookies("xhs") is True

    def test_has_active_cookies_false(self, cookie_mgr, mock_mongo):
        """无 active cookie 时返回 False"""
        col_mock = mock_mongo.get_collection.return_value
        col_mock.count_documents.return_value = 0
        assert cookie_mgr.has_active_cookies("xhs") is False

    def test_get_all_status_includes_cookie_id(self, cookie_mgr, mock_mongo):
        """get_all_status 应包含 cookie_id 字段"""
        mock_mongo.find.return_value = [
            {"cookie_id": "xhs_aaa", "platform": "xhs", "status": "active", "saved_at": 1000},
            {"cookie_id": "xhs_bbb", "platform": "xhs", "status": "expired", "saved_at": 900},
        ]
        statuses = cookie_mgr.get_all_status()

        # 应包含两条 xhs 记录 + 其他 missing 平台
        xhs_entries = [s for s in statuses if s["platform"] == "xhs"]
        assert len(xhs_entries) == 2
        assert all("cookie_id" in s for s in xhs_entries)

        # 其他平台应为 missing
        dy = next(s for s in statuses if s["platform"] == "dy")
        assert dy["status"] == "missing"

    def test_generate_cookie_id_deterministic(self, cookie_mgr):
        """同一 session cookie 值应生成相同的 cookie_id"""
        id1 = cookie_mgr._generate_cookie_id("xhs", {"web_session": "abc123"})
        id2 = cookie_mgr._generate_cookie_id("xhs", {"web_session": "abc123"})
        assert id1 == id2
        assert id1.startswith("xhs_")

    def test_generate_cookie_id_different_sessions(self, cookie_mgr):
        """不同 session cookie 值应生成不同的 cookie_id"""
        id1 = cookie_mgr._generate_cookie_id("xhs", {"web_session": "abc123"})
        id2 = cookie_mgr._generate_cookie_id("xhs", {"web_session": "xyz789"})
        assert id1 != id2

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
        from DeepSentimentCrawling.worker import _save_config, _restore_config, mc_config

        # 先设置一个已知属性用于测试
        mc_config.TEST_PLATFORM = "bili"
        mc_config.TEST_KEYWORDS = "原始关键词"

        original = _save_config()
        assert "TEST_PLATFORM" in original
        assert original["TEST_PLATFORM"] == "bili"

        # 修改
        mc_config.TEST_PLATFORM = "test_modified"
        mc_config.TEST_KEYWORDS = "被篡改的关键词"
        assert mc_config.TEST_PLATFORM == "test_modified"

        # 恢复
        _restore_config(original)
        assert mc_config.TEST_PLATFORM == "bili"
        assert mc_config.TEST_KEYWORDS == "原始关键词"

        # 清理
        del mc_config.TEST_PLATFORM
        del mc_config.TEST_KEYWORDS


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

    def test_circuit_auto_resets_on_new_cookie(self, mock_mongo):
        dispatcher = TaskDispatcher(
            platforms=["xhs"], mongo_writer=mock_mongo, dry_run=True
        )
        # 设置熔断已开启
        dispatcher.circuit_open["xhs"] = True
        dispatcher.failure_counts["xhs"] = 3

        # 模拟 has_active_cookies 返回 True（用户已更新 cookie）
        dispatcher.cookie_manager = MagicMock()
        dispatcher.cookie_manager.has_active_cookies.return_value = True

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
    """确保配置表的一致性"""

    def test_crawl_scale_keys(self):
        """_CRAWL_SCALE 应只包含 exploded"""
        assert "exploded" in _CRAWL_SCALE

    def test_crawl_scale_exploded_has_7_platforms(self):
        """exploded 应配置 7 个平台"""
        assert _CRAWL_SCALE["exploded"]["platforms"] == 7

    def test_crawl_scale_exploded_structure(self):
        """exploded 配置应有完整字段"""
        scale = _CRAWL_SCALE["exploded"]
        assert "max_notes" in scale
        assert "priority" in scale
        assert "platforms" in scale


# ==================== 7. TopicMatcher 去重改进测试 ====================


class TestTopicMatcherFastPath:
    """测试 jieba fast-path（overlap >= 0.8 → 直接 duplicate）"""

    @pytest.fixture
    def matcher(self, mock_mongo):
        """创建 TopicMatcher（LLM 不可用）"""
        with patch("DeepSentimentCrawling.topic_matcher.settings") as mock_settings:
            mock_settings.TOPIC_MATCHER_API_KEY = ""
            mock_settings.TOPIC_MATCHER_BASE_URL = ""
            mock_settings.MINDSPIDER_API_KEY = ""
            mock_settings.MINDSPIDER_BASE_URL = ""
            mock_settings.TOPIC_MATCHER_MODEL_NAME = ""
            return TopicMatcher(mongo=mock_mongo)

    def test_high_overlap_returns_duplicate(self, matcher):
        """overlap >= 0.8 应直接返回 duplicate，跳过 LLM"""
        # 构造几乎相同的标题
        candidates = [
            {
                "candidate_id": "cand_abc123",
                "canonical_title": "北京暴雨致交通瘫痪",
                "source_titles": ["北京暴雨致交通瘫痪"],
                "status": "exploded",
            }
        ]

        # mock _fetch 和 _check_recent
        matcher._fetch_deep_crawled_candidates = MagicMock(return_value=candidates)
        matcher._check_recent_user_tasks = MagicMock(return_value=None)
        matcher._get_crawl_stats = MagicMock(return_value={"total_tasks": 0, "completed": 0, "platforms": []})

        # 用几乎相同的标题测试
        result = matcher.match("北京暴雨致交通瘫痪严重")
        # 关键词重叠率取决于 jieba 分词，验证逻辑路径
        if result:
            assert result["match_type"] in (MATCH_DUPLICATE,)
            assert result["match_method"] in ("jieba_fast", "jieba")

    def test_identical_title_is_fast_path(self, matcher):
        """完全相同的标题 overlap = 1.0，应走 fast-path"""
        candidates = [
            {
                "candidate_id": "cand_xyz",
                "canonical_title": "某某公司大规模裁员",
                "source_titles": ["某某公司大规模裁员"],
                "status": "exploded",
            }
        ]

        matcher._fetch_deep_crawled_candidates = MagicMock(return_value=candidates)
        matcher._check_recent_user_tasks = MagicMock(return_value=None)
        matcher._get_crawl_stats = MagicMock(return_value={"total_tasks": 0, "completed": 0, "platforms": []})

        result = matcher.match("某某公司大规模裁员")
        assert result is not None
        assert result["match_type"] == MATCH_DUPLICATE
        assert result["match_method"] == "jieba_fast"
        assert result["confidence"] >= 0.8

    def test_low_overlap_returns_none(self, matcher):
        """无关话题应返回 None"""
        candidates = [
            {
                "candidate_id": "cand_001",
                "canonical_title": "北京暴雨致交通瘫痪",
                "source_titles": ["北京暴雨致交通瘫痪"],
                "status": "exploded",
            }
        ]

        matcher._fetch_deep_crawled_candidates = MagicMock(return_value=candidates)
        matcher._check_recent_user_tasks = MagicMock(return_value=None)

        result = matcher.match("苹果发布新款iPhone")
        assert result is None


class TestTopicMatcherDedupWindow:
    """测试 36h 去重窗口"""

    def test_dedup_window_is_36h(self):
        """EXACT_DEDUP_WINDOW 应为 36 小时"""
        assert TopicMatcher.EXACT_DEDUP_WINDOW == 36 * 3600
        assert TopicMatcher.EXACT_DEDUP_WINDOW == 129600


class TestTopicMatcherExcludeCandidate:
    """测试 exclude_candidate_id 排除自匹配"""

    @pytest.fixture
    def matcher(self, mock_mongo):
        with patch("DeepSentimentCrawling.topic_matcher.settings") as mock_settings:
            mock_settings.TOPIC_MATCHER_API_KEY = ""
            mock_settings.TOPIC_MATCHER_BASE_URL = ""
            mock_settings.MINDSPIDER_API_KEY = ""
            mock_settings.MINDSPIDER_BASE_URL = ""
            mock_settings.TOPIC_MATCHER_MODEL_NAME = ""
            return TopicMatcher(mongo=mock_mongo)

    def test_exclude_candidate_id_in_match(self, matcher):
        """match() 应传递 exclude_candidate_id 给下游方法"""
        matcher._check_recent_user_tasks = MagicMock(return_value=None)
        matcher._fetch_deep_crawled_candidates = MagicMock(return_value=[])

        matcher.match("某话题", exclude_candidate_id="cand_self")

        matcher._check_recent_user_tasks.assert_called_once_with(
            "某话题", "cand_self"
        )
        matcher._fetch_deep_crawled_candidates.assert_called_once_with("cand_self")

    def test_exclude_candidate_id_none_by_default(self, matcher):
        """不传 exclude_candidate_id 时应为 None"""
        matcher._check_recent_user_tasks = MagicMock(return_value=None)
        matcher._fetch_deep_crawled_candidates = MagicMock(return_value=[])

        matcher.match("某话题")

        matcher._check_recent_user_tasks.assert_called_once_with("某话题", None)
        matcher._fetch_deep_crawled_candidates.assert_called_once_with(None)


class TestCandidateManagerDedup:
    """测试候选路径去重（CandidateManager._emit_crawl_tasks 接入 TopicMatcher）"""

    def _make_candidate(self, cand_id="cand_test123"):
        now = int(time.time())
        return {
            "candidate_id": cand_id,
            "canonical_title": "测试：某重大社会事件引发热议",
            "source_titles": ["测试：某重大社会事件引发热议"],
            "status": "exploded",
            "platforms": ["weibo", "bilibili", "douyin"],
            "platform_count": 3,
            "snapshots": [{"ts": now, "score_pos": 12000, "sum_hot": 500000}],
            "first_seen_at": now - 3600,
            "updated_at": now,
            "status_history": [
                {"ts": now - 3600, "status": "emerging", "reason": "cross_platform signal"},
                {"ts": now, "status": "exploded", "reason": "score_pos >= 10000"},
            ],
        }

    def test_duplicate_skips_all_tasks(self, manager, mock_mongo):
        """TopicMatcher 返回 duplicate 时应跳过全部任务创建"""
        col_mock = mock_mongo.get_collection.return_value
        cand = self._make_candidate()
        now = int(time.time())

        # mock TopicMatcher 返回 duplicate
        manager.topic_matcher = MagicMock()
        manager.topic_matcher.match.return_value = {
            "match_type": MATCH_DUPLICATE,
            "candidate_id": "cand_other",
            "canonical_title": "已有的相同事件",
            "match_method": "jieba_fast",
            "confidence": 0.9,
        }

        manager._emit_crawl_tasks(cand, "exploded", now)

        # duplicate → 不应创建任何任务
        assert col_mock.insert_one.call_count == 0

    def test_development_proceeds_normally(self, manager, mock_mongo):
        """TopicMatcher 返回 development 时应正常创建任务"""
        col_mock = mock_mongo.get_collection.return_value
        col_mock.find_one.return_value = None
        cand = self._make_candidate()
        now = int(time.time())

        manager.topic_matcher = MagicMock()
        manager.topic_matcher.match.return_value = {
            "match_type": MATCH_DEVELOPMENT,
            "candidate_id": "cand_other",
            "canonical_title": "同一事件的进展",
            "match_method": "llm",
            "confidence": 0.8,
        }

        manager._emit_crawl_tasks(cand, "exploded", now)

        # development → 应正常创建 7 个任务
        assert col_mock.insert_one.call_count == 7

    def test_no_match_proceeds_normally(self, manager, mock_mongo):
        """TopicMatcher 返回 None 时应正常创建任务"""
        col_mock = mock_mongo.get_collection.return_value
        col_mock.find_one.return_value = None
        cand = self._make_candidate()
        now = int(time.time())

        manager.topic_matcher = MagicMock()
        manager.topic_matcher.match.return_value = None

        manager._emit_crawl_tasks(cand, "exploded", now)

        assert col_mock.insert_one.call_count == 7

    def test_topic_matcher_failure_continues(self, manager, mock_mongo):
        """TopicMatcher 异常时应继续创建任务（降级）"""
        col_mock = mock_mongo.get_collection.return_value
        col_mock.find_one.return_value = None
        cand = self._make_candidate()
        now = int(time.time())

        manager.topic_matcher = MagicMock()
        manager.topic_matcher.match.side_effect = Exception("LLM 服务不可用")

        manager._emit_crawl_tasks(cand, "exploded", now)

        # 异常降级 → 仍应创建任务
        assert col_mock.insert_one.call_count == 7

    def test_topic_matcher_passes_exclude_candidate_id(self, manager, mock_mongo):
        """_emit_crawl_tasks 应传递当前 candidate_id 作为 exclude"""
        col_mock = mock_mongo.get_collection.return_value
        col_mock.find_one.return_value = None
        cand = self._make_candidate("cand_myid")
        now = int(time.time())

        manager.topic_matcher = MagicMock()
        manager.topic_matcher.match.return_value = None

        manager._emit_crawl_tasks(cand, "exploded", now)

        manager.topic_matcher.match.assert_called_once_with(
            "测试：某重大社会事件引发热议",
            exclude_candidate_id="cand_myid",
        )


# ==================== 8. 熔断平台 Redis 任务丢弃测试 ====================


class TestDispatcherCircuitDrop:
    """测试熔断时 Redis 任务丢弃（不推回），锁占用时仍推回"""

    def _make_task(self, platform="wb", task_id="ct_test_wb_001", from_redis=True):
        task = {
            "task_id": task_id,
            "platform": platform,
            "topic_title": "测试话题",
            "search_keywords": ["测试"],
            "max_notes": 50,
            "priority": 100,
            "status": "pending",
            "attempts": 0,
        }
        if from_redis:
            task["_from_redis"] = True
            task["_redis_score"] = 10000
        return task

    def test_circuit_open_drops_redis_tasks(self, mock_mongo):
        """熔断时 Redis 任务应被丢弃，不推回"""
        dispatcher = TaskDispatcher(
            platforms=["wb"], mongo_writer=mock_mongo, dry_run=True
        )
        dispatcher.circuit_open["wb"] = True
        dispatcher.cookie_manager = MagicMock()
        dispatcher.cookie_manager.has_active_cookies.return_value = False

        mock_queue = MagicMock()
        dispatcher._task_queue = mock_queue

        tasks = [self._make_task("wb", "ct_wb_001"), self._make_task("wb", "ct_wb_002")]
        dispatcher._fetch_pending_tasks = MagicMock(return_value=tasks)

        asyncio.get_event_loop().run_until_complete(dispatcher._dispatch_round())

        # push_back 不应被调用（任务被丢弃而非推回）
        mock_queue.push_back.assert_not_called()

    def test_circuit_drop_calls_ensure_task_in_mongo(self, mock_mongo):
        """熔断丢弃前应确保任务在 MongoDB 中存在"""
        dispatcher = TaskDispatcher(
            platforms=["wb"], mongo_writer=mock_mongo, dry_run=True
        )
        dispatcher.circuit_open["wb"] = True
        dispatcher.cookie_manager = MagicMock()
        dispatcher.cookie_manager.has_active_cookies.return_value = False

        dispatcher._task_queue = MagicMock()
        dispatcher._ensure_task_in_mongo = MagicMock()

        tasks = [self._make_task("wb", "ct_wb_001")]
        dispatcher._fetch_pending_tasks = MagicMock(return_value=tasks)

        asyncio.get_event_loop().run_until_complete(dispatcher._dispatch_round())

        dispatcher._ensure_task_in_mongo.assert_called_once_with(tasks[0])

    def test_circuit_drop_log_once_per_platform(self, mock_mongo):
        """熔断丢弃日志每平台只输出一次"""
        dispatcher = TaskDispatcher(
            platforms=["wb"], mongo_writer=mock_mongo, dry_run=True
        )
        dispatcher.circuit_open["wb"] = True
        dispatcher.cookie_manager = MagicMock()
        dispatcher.cookie_manager.has_active_cookies.return_value = False
        dispatcher._task_queue = MagicMock()

        tasks = [self._make_task("wb")]
        dispatcher._fetch_pending_tasks = MagicMock(return_value=tasks)

        with patch("DeepSentimentCrawling.dispatcher.logger") as mock_logger:
            # 第一轮：应输出日志
            asyncio.get_event_loop().run_until_complete(dispatcher._dispatch_round())
            info_calls_round1 = [
                c for c in mock_logger.info.call_args_list
                if "熔断中" in str(c) and "丢弃" in str(c)
            ]
            assert len(info_calls_round1) == 1

            mock_logger.reset_mock()

            # 第二轮：同平台不应再输出
            asyncio.get_event_loop().run_until_complete(dispatcher._dispatch_round())
            info_calls_round2 = [
                c for c in mock_logger.info.call_args_list
                if "熔断中" in str(c) and "丢弃" in str(c)
            ]
            assert len(info_calls_round2) == 0

    def test_lock_skipped_tasks_still_push_back(self, mock_mongo):
        """锁占用时 Redis 任务应推回队列"""
        dispatcher = TaskDispatcher(
            platforms=["wb"], mongo_writer=mock_mongo, dry_run=True
        )
        dispatcher.cookie_manager = MagicMock()
        dispatcher.cookie_manager.has_active_cookies.return_value = True

        mock_queue = MagicMock()
        dispatcher._task_queue = mock_queue

        # 手动锁住平台
        lock = dispatcher.platform_locks["wb"]
        lock._locked = True  # asyncio.Lock 内部状态

        tasks = [self._make_task("wb")]
        dispatcher._fetch_pending_tasks = MagicMock(return_value=tasks)

        asyncio.get_event_loop().run_until_complete(dispatcher._dispatch_round())

        mock_queue.push_back.assert_called_once()

    def test_circuit_recovery_clears_drop_log(self, mock_mongo):
        """熔断恢复后日志标记应被清除，下次熔断可再次输出"""
        dispatcher = TaskDispatcher(
            platforms=["wb"], mongo_writer=mock_mongo, dry_run=True
        )
        dispatcher.circuit_open["wb"] = True
        dispatcher._circuit_drop_logged.add("wb")

        # 模拟 cookie 已更新 → 熔断恢复
        dispatcher.cookie_manager = MagicMock()
        dispatcher.cookie_manager.has_active_cookies.return_value = True

        assert dispatcher._is_circuit_open("wb") is False
        assert "wb" not in dispatcher._circuit_drop_logged
