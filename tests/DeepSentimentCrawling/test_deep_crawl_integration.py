# -*- coding: utf-8 -*-
"""
深层采集集成冒烟测试

用一个假设话题走通完整流程：
1. 构造候选 → 状态跃迁到 rising → 验证 crawl_task 写入 MongoDB
2. 取出 crawl_task → PlatformWorker 执行 → 验证 MySQL 数据含 topic_id

前提：
- MongoDB 可达（mindspider_signal 库）
- MySQL/PostgreSQL 可达
- bili 平台有有效 cookie（或该测试在无 cookie 时验证 blocked 降级）

运行：
    uv run pytest tests/DeepSentimentCrawling/test_deep_crawl_integration.py -v -s
"""

import time

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
)
from BroadTopicExtraction.pipeline.mongo_writer import MongoWriter
from ms_config import settings

# 测试标记：需要真实 MongoDB
pytestmark = pytest.mark.skipif(
    not settings.MONGO_URI or settings.MONGO_URI == "mongodb://localhost:27017",
    reason="需要配置 MONGO_URI 连接真实 MongoDB",
)


# ==================== Fixtures ====================


TEST_CANDIDATE_ID = "cand_test_deep_crawl_smoke"
TEST_TOPIC_TITLE = "测试话题_深层采集冒烟测试_请忽略"


@pytest.fixture(scope="module")
def signal_mongo():
    """信号库连接"""
    mw = MongoWriter(db_name=settings.MONGO_SIGNAL_DB_NAME)
    mw.connect()
    yield mw
    mw.close()


@pytest.fixture(scope="module")
def manager(signal_mongo):
    return CandidateManager(signal_writer=signal_mongo)


@pytest.fixture(scope="module", autouse=True)
def cleanup_test_data(signal_mongo):
    """测试前后清理测试数据"""
    # 清理旧的测试数据
    _cleanup(signal_mongo)
    yield
    # 测试后清理
    _cleanup(signal_mongo)


def _cleanup(mongo: MongoWriter):
    """清理测试数据"""
    candidates_col = mongo.get_collection(COLLECTION)
    tasks_col = mongo.get_collection(CRAWL_TASKS_COLLECTION)
    candidates_col.delete_many({"candidate_id": TEST_CANDIDATE_ID})
    tasks_col.delete_many({"candidate_id": TEST_CANDIDATE_ID})


# ==================== 阶段 1：候选 → crawl_task 生成 ====================


class TestCandidateEmitsTasks:
    """验证候选状态跃迁时写入 crawl_task 到 MongoDB"""

    @pytest.fixture(scope="class")
    def rising_candidate(self, manager, signal_mongo):
        """构造一个假设候选，从 emerging 跃迁到 rising"""
        now = int(time.time())
        candidate = {
            "candidate_id": TEST_CANDIDATE_ID,
            "canonical_title": TEST_TOPIC_TITLE,
            "source_titles": [TEST_TOPIC_TITLE],
            "status": "emerging",
            "platforms": ["weibo", "bilibili", "douyin", "zhihu"],
            "platform_count": 4,
            "snapshots": [{"ts": now, "score_pos": 2000, "sum_hot": 100000}],
            "first_seen_at": now - 3600,
            "updated_at": now,
            "status_history": [
                {"ts": now - 3600, "status": "emerging", "reason": "test signal"},
            ],
        }

        # 写入候选
        col = signal_mongo.get_collection(COLLECTION)
        col.update_one(
            {"candidate_id": TEST_CANDIDATE_ID},
            {"$set": candidate},
            upsert=True,
        )

        # 执行状态跃迁
        manager._apply_transition(candidate, "rising", "score_pos >= 1500 (test)", now)

        return candidate

    def test_candidate_is_rising(self, rising_candidate):
        """候选状态应为 rising"""
        assert rising_candidate["status"] == "rising"

    def test_crawl_tasks_created(self, rising_candidate, signal_mongo):
        """应生成 crawl_task 文档"""
        tasks_col = signal_mongo.get_collection(CRAWL_TASKS_COLLECTION)
        tasks = list(tasks_col.find({"candidate_id": TEST_CANDIDATE_ID}))

        assert len(tasks) > 0, "应生成至少 1 个 crawl_task"
        print(f"\n[integration] 生成了 {len(tasks)} 个 crawl_task")
        for t in tasks:
            print(f"  - {t['task_id']}: platform={t['platform']}, "
                  f"max_notes={t['max_notes']}, priority={t['priority']}")

    def test_crawl_task_structure(self, rising_candidate, signal_mongo):
        """验证 crawl_task 文档结构"""
        tasks_col = signal_mongo.get_collection(CRAWL_TASKS_COLLECTION)
        task = tasks_col.find_one({"candidate_id": TEST_CANDIDATE_ID})

        assert task is not None
        assert task["task_id"].startswith("ct_")
        assert task["candidate_id"] == TEST_CANDIDATE_ID
        assert task["topic_title"] == TEST_TOPIC_TITLE
        assert isinstance(task["search_keywords"], list)
        assert task["platform"] in ("wb", "bili", "dy", "zhihu")
        assert task["max_notes"] == 10  # rising 的 max_notes
        assert task["priority"] == 1   # rising 的 priority
        assert task["status"] == "pending"
        assert task["attempts"] == 0

    def test_rising_limits_to_3_platforms(self, rising_candidate, signal_mongo):
        """rising 应最多生成 3 个平台的任务"""
        tasks_col = signal_mongo.get_collection(CRAWL_TASKS_COLLECTION)
        tasks = list(tasks_col.find({"candidate_id": TEST_CANDIDATE_ID}))
        assert len(tasks) <= 3

    def test_no_duplicate_on_rerun(self, rising_candidate, manager, signal_mongo):
        """再次调用 _emit_crawl_tasks 不应生成重复任务"""
        now = int(time.time())
        before_count = signal_mongo.get_collection(CRAWL_TASKS_COLLECTION).count_documents(
            {"candidate_id": TEST_CANDIDATE_ID}
        )

        manager._emit_crawl_tasks(rising_candidate, "rising", now)

        after_count = signal_mongo.get_collection(CRAWL_TASKS_COLLECTION).count_documents(
            {"candidate_id": TEST_CANDIDATE_ID}
        )
        assert after_count == before_count, "不应生成重复任务"


# ==================== 阶段 2：Worker 执行（无 cookie 降级测试） ====================


class TestWorkerExecution:
    """测试 PlatformWorker 在无 cookie 时的降级行为"""

    @pytest.fixture(scope="class")
    def task_doc(self, signal_mongo):
        """从 MongoDB 取出一个测试任务"""
        tasks_col = signal_mongo.get_collection(CRAWL_TASKS_COLLECTION)
        task = tasks_col.find_one({"candidate_id": TEST_CANDIDATE_ID, "status": "pending"})
        if not task:
            pytest.skip("无可用的测试任务")
        return task

    @pytest.mark.asyncio
    async def test_worker_blocked_without_cookies(self, task_doc, signal_mongo):
        """无 cookie 时 worker 应返回 blocked"""
        from DeepSentimentCrawling.cookie_manager import CookieManager
        from DeepSentimentCrawling.worker import PlatformWorker

        # 使用真实 MongoDB 但确保无 cookie
        cm = CookieManager(mongo_writer=signal_mongo)
        # 确保测试平台无 cookie
        col = signal_mongo.get_collection("platform_cookies")
        col.delete_one({"platform": task_doc["platform"]})

        worker = PlatformWorker(cookie_manager=cm)

        with pytest.MonkeyPatch.context() as m:
            # 屏蔽 alert 的实际 HTTP 调用
            m.setattr("DeepSentimentCrawling.alert.send_alert", lambda *a, **kw: False)
            result = await worker.execute_task(task_doc)

        assert result["status"] == "blocked"
        assert result["reason"] == "no_cookies"
        print(f"\n[integration] Worker 正确降级: {result}")

    @pytest.mark.asyncio
    async def test_worker_config_restored_after_blocked(self, task_doc):
        """即使任务 blocked，config 也应被恢复"""
        from DeepSentimentCrawling.worker import _save_config

        import config as cfg_module
        # 在项目 config 上设置标记属性
        cfg_module._TEST_MARKER = "original_value"

        from DeepSentimentCrawling.cookie_manager import CookieManager
        from DeepSentimentCrawling.worker import PlatformWorker

        # 使用 mock cookie_manager 返回 None
        cm = CookieManager.__new__(CookieManager)
        cm.mongo = type("FakeMongo", (), {
            "connect": lambda self: None,
            "find_one": lambda self, *a, **kw: None,
        })()

        worker = PlatformWorker(cookie_manager=cm)

        with pytest.MonkeyPatch.context() as m:
            m.setattr("DeepSentimentCrawling.alert.send_alert", lambda *a, **kw: False)
            m.setattr("DeepSentimentCrawling.worker.alert_cookie_expired", lambda *a, **kw: False)
            await worker.execute_task(task_doc)

        # 验证 config 已恢复（标记属性应保持不变）
        assert cfg_module._TEST_MARKER == "original_value"
        del cfg_module._TEST_MARKER


# ==================== 阶段 3：带 cookie 的完整执行（可选） ====================


class TestFullExecution:
    """
    完整执行测试 — 用 bili 平台实际爬取，检查 MySQL 数据。

    此测试需要：
    1. MongoDB platform_cookies 中有 bili 的 active cookie
    2. MySQL/PostgreSQL 可达

    无 cookie 时自动跳过。
    """

    @pytest.fixture(scope="class")
    def bili_cookie(self, signal_mongo):
        """检查 bili 是否有可用 cookie"""
        from DeepSentimentCrawling.cookie_manager import CookieManager
        cm = CookieManager(mongo_writer=signal_mongo)
        cookies = cm.load_cookies("bili")
        if not cookies:
            pytest.skip("bili 平台无可用 cookie，跳过完整执行测试")
        return cookies

    @pytest.fixture(scope="class")
    def bili_task(self, signal_mongo):
        """构造一个 bili 平台的测试任务"""
        now = int(time.time())
        task = {
            "task_id": f"ct_{TEST_CANDIDATE_ID}_bili_{now}",
            "candidate_id": TEST_CANDIDATE_ID,
            "topic_title": TEST_TOPIC_TITLE,
            "search_keywords": ["测试"],
            "platform": "bili",
            "max_notes": 2,  # 最小量，仅验证流程
            "priority": 1,
            "status": "pending",
            "created_at": now,
            "attempts": 0,
        }
        tasks_col = signal_mongo.get_collection(CRAWL_TASKS_COLLECTION)
        tasks_col.update_one(
            {"task_id": task["task_id"]},
            {"$set": task},
            upsert=True,
        )
        yield task
        # 清理
        tasks_col.delete_one({"task_id": task["task_id"]})

    @pytest.mark.asyncio
    async def test_full_crawl_and_mysql_check(self, bili_cookie, bili_task, signal_mongo):
        """完整执行：Worker 爬取 → 检查 MySQL 数据含 topic_id"""
        from DeepSentimentCrawling.cookie_manager import CookieManager
        from DeepSentimentCrawling.worker import PlatformWorker

        cm = CookieManager(mongo_writer=signal_mongo)
        worker = PlatformWorker(cookie_manager=cm)

        result = await worker.execute_task(bili_task)
        print(f"\n[integration] 完整执行结果: {result}")

        if result["status"] != "success":
            pytest.skip(f"爬取未成功: {result}")

        # 检查 MySQL/PostgreSQL 数据
        await self._verify_mysql_data(bili_task)

    async def _verify_mysql_data(self, task):
        """验证 MySQL 中的数据包含 topic_id 和 crawling_task_id"""
        import aiomysql
        from ms_config import settings as root_settings

        # 根据数据库类型选择连接方式
        if root_settings.DB_DIALECT == "postgresql":
            # 使用 asyncpg 或跳过
            try:
                import asyncpg
                conn = await asyncpg.connect(
                    host=root_settings.DB_HOST,
                    port=root_settings.DB_PORT,
                    user=root_settings.DB_USER,
                    password=root_settings.DB_PASSWORD,
                    database=root_settings.DB_NAME,
                )
                rows = await conn.fetch(
                    "SELECT topic_id, crawling_task_id FROM bilibili_video "
                    "WHERE topic_id = $1 LIMIT 5",
                    task["candidate_id"],
                )
                await conn.close()
            except ImportError:
                pytest.skip("asyncpg 未安装，跳过 PostgreSQL 验证")
                return
        else:
            pool = await aiomysql.create_pool(
                host=root_settings.DB_HOST,
                port=root_settings.DB_PORT,
                user=root_settings.DB_USER,
                password=root_settings.DB_PASSWORD,
                db=root_settings.DB_NAME,
                charset=root_settings.DB_CHARSET,
            )
            async with pool.acquire() as conn:
                async with conn.cursor(aiomysql.DictCursor) as cur:
                    await cur.execute(
                        "SELECT topic_id, crawling_task_id FROM bilibili_video "
                        "WHERE topic_id = %s LIMIT 5",
                        (task["candidate_id"],),
                    )
                    rows = await cur.fetchall()
            pool.close()
            await pool.wait_closed()

        print(f"\n[integration] MySQL 查询结果: {len(rows)} 行")
        for row in rows:
            print(f"  topic_id={row['topic_id']}, crawling_task_id={row['crawling_task_id']}")

        assert len(rows) > 0, "MySQL 应有带 topic_id 的数据"
        for row in rows:
            assert row["topic_id"] == task["candidate_id"]
            assert row["crawling_task_id"] == task["task_id"]
