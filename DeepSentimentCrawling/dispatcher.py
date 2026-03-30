# -*- coding: utf-8 -*-
"""
TaskDispatcher — 异步任务调度器

从 Redis 任务队列 + MongoDB 获取待执行任务，按优先级调度到 PlatformWorker。
每个平台同时只运行一个任务（平台锁），连续失败触发熔断器。

任务来源优先级：
  1. Redis 队列（user 任务 > candidate 任务）
  2. MongoDB 轮询（重试任务、Redis 之前遗留的任务）
"""

import asyncio
import json
import time
from typing import Optional
from datetime import date
from urllib.parse import quote_plus

from loguru import logger
from sqlalchemy import create_engine, text

import sys
from pathlib import Path

_PROJECT_ROOT = str(Path(__file__).parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
from ms_config import settings

from BroadTopicExtraction.pipeline.mongo_writer import MongoWriter
from DeepSentimentCrawling.worker import PlatformWorker
from DeepSentimentCrawling.cookie_manager import CookieManager
from DeepSentimentCrawling.alert import alert_circuit_open

CRAWL_TASKS_COLLECTION = "crawl_tasks"
TASK_STATUS_COLLECTION = "task_status"

# 所有支持的平台
ALL_PLATFORMS = ["xhs", "dy", "bili", "wb", "ks", "tieba", "zhihu"]


class TaskDispatcher:
    """异步爬取任务调度器"""

    POLL_INTERVAL = 10  # 轮询间隔（秒）
    CIRCUIT_THRESHOLD = 3  # 连续失败次数触发熔断
    MAX_ATTEMPTS = 3  # 单任务最大重试次数
    RETRY_BACKOFF = [120, 240, 480]  # 重试退避（秒）
    ZOMBIE_TIMEOUT = 3600  # running 超过 60 分钟视为僵尸（秒）
    STALE_PENDING_TIMEOUT = 1800  # pending 超过 30 分钟视为过期（秒）

    def __init__(
        self,
        platforms: Optional[list[str]] = None,
        cookie_manager: Optional[CookieManager] = None,
        mongo_writer: Optional[MongoWriter] = None,
        dry_run: bool = False,
    ):
        self.platforms = platforms or ALL_PLATFORMS
        self.cookie_manager = cookie_manager or CookieManager()
        self.mongo = mongo_writer or MongoWriter(db_name=settings.MONGO_SIGNAL_DB_NAME)
        self.dry_run = dry_run
        self._mysql_engine = None
        self._task_queue = None  # TaskQueue (lazy init)

        self.workers: dict[str, PlatformWorker] = {}
        self.platform_locks: dict[str, asyncio.Lock] = {}
        self.failure_counts: dict[str, int] = {}
        self.circuit_open: dict[str, bool] = {}

        self._running = False
        self._running_tasks: set[asyncio.Task] = set()  # fire-and-forget 任务跟踪
        self._circuit_drop_logged: set[str] = set()  # 熔断丢弃日志去重

        for plat in self.platforms:
            self.workers[plat] = PlatformWorker(cookie_manager=self.cookie_manager)
            self.platform_locks[plat] = asyncio.Lock()
            self.failure_counts[plat] = 0
            self.circuit_open[plat] = False

    # ==================== 基础设施 ====================

    def _get_mysql_engine(self):
        """懒初始化 MySQL 引擎"""
        if self._mysql_engine is None:
            dialect = (settings.DB_DIALECT or "mysql").lower()
            if dialect in ("postgresql", "postgres"):
                url = (
                    f"postgresql+psycopg://{settings.DB_USER}:"
                    f"{quote_plus(settings.DB_PASSWORD)}@"
                    f"{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}"
                )
            else:
                url = (
                    f"mysql+pymysql://{settings.DB_USER}:"
                    f"{quote_plus(settings.DB_PASSWORD)}@"
                    f"{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}"
                    f"?charset={settings.DB_CHARSET}"
                )
            self._mysql_engine = create_engine(
                url, future=True, pool_recycle=3600, pool_pre_ping=True
            )
        return self._mysql_engine

    def _get_task_queue(self):
        """懒初始化 Redis TaskQueue"""
        if self._task_queue is None:
            try:
                from DeepSentimentCrawling.task_queue import get_task_queue

                self._task_queue = get_task_queue()
            except Exception as e:
                logger.warning(f"[Dispatcher] Redis 不可用: {e}")
        return self._task_queue

    def ensure_indexes(self):
        """创建 crawl_tasks 索引"""
        self.mongo.connect()
        self.mongo.create_indexes(
            CRAWL_TASKS_COLLECTION,
            [
                {"keys": [("task_id", 1)], "options": {"unique": True}},
                {"keys": [("status", 1), ("priority", -1), ("created_at", 1)]},
                {"keys": [("candidate_id", 1), ("platform", 1)]},
                {"keys": [("status", 1), ("pending_since", 1)]},
            ],
        )
        self.mongo.create_indexes(
            TASK_STATUS_COLLECTION,
            [
                {"keys": [("task_id", 1)]},
                {"keys": [("status", 1)]},
                {"keys": [("updated_at", -1)]},
            ],
        )

    # ==================== 熔断器 ====================

    def _log_circuit_event(self, platform: str, event: str, reason: str = ""):
        """记录熔断器事件到 MongoDB"""
        try:
            col = self.mongo.get_collection("circuit_events")
            col.insert_one(
                {
                    "platform": platform,
                    "event": event,  # "open" | "closed"
                    "reason": reason,
                    "timestamp": int(time.time()),
                }
            )
        except Exception as e:
            logger.warning(f"[Dispatcher] circuit_events 写入失败: {e}")

    def _is_circuit_open(self, platform: str) -> bool:
        if not self.circuit_open.get(platform, False):
            return False
        # 检查 cookie 池是否有可用 cookie（用户可能已更新）
        if self.cookie_manager.has_active_cookies(platform):
            self.circuit_open[platform] = False
            self.failure_counts[platform] = 0
            self._circuit_drop_logged.discard(platform)
            logger.info(f"[Dispatcher] {platform} cookie 已更新，熔断器恢复")
            self._log_circuit_event(platform, "closed", "cookie 已更新")
            return False
        return True

    def _trip_circuit(self, platform: str, reason: str):
        self.circuit_open[platform] = True
        logger.warning(f"[Dispatcher] {platform} 熔断器触发: {reason}")
        # cookie 池模式下，单个 cookie 过期由 worker 负责标记，
        # 熔断器不再批量过期所有 cookie
        alert_circuit_open(platform, reason)
        self._log_circuit_event(platform, "open", reason)

    # ==================== 僵尸任务回收 ====================

    def _reap_zombie_tasks(self) -> int:
        """
        将超时的 running 任务重置为 pending（未耗尽重试）或 failed（已耗尽）。

        返回回收的任务数量。
        """
        self.mongo.connect()
        col = self.mongo.get_collection(CRAWL_TASKS_COLLECTION)
        cutoff = int(time.time()) - self.ZOMBIE_TIMEOUT

        zombies = list(
            col.find(
                {
                    "status": "running",
                    "started_at": {"$lt": cutoff},
                }
            )
        )

        if not zombies:
            return 0

        reaped = 0
        for task in zombies:
            task_id = task["task_id"]
            attempts = task.get("attempts", 0) + 1
            elapsed_h = (time.time() - task.get("started_at", 0)) / 3600

            if attempts >= self.MAX_ATTEMPTS:
                self._update_task_status(
                    task_id,
                    {
                        "status": "failed",
                        "attempts": attempts,
                        "error": f"zombie_reaped: running 超时 {elapsed_h:.1f}h",
                    },
                )
                logger.warning(
                    f"[Dispatcher] 僵尸回收: {task_id} ({task.get('platform')}) "
                    f"running {elapsed_h:.1f}h, 重试耗尽 → failed"
                )
            else:
                backoff = self.RETRY_BACKOFF[min(attempts - 1, len(self.RETRY_BACKOFF) - 1)]
                self._update_task_status(
                    task_id,
                    {
                        "status": "pending",
                        "attempts": attempts,
                        "next_retry_at": int(time.time()) + backoff,
                        "last_error": f"zombie_reaped: running 超时 {elapsed_h:.1f}h",
                        "pending_since": int(time.time()),
                    },
                )
                logger.warning(
                    f"[Dispatcher] 僵尸回收: {task_id} ({task.get('platform')}) "
                    f"running {elapsed_h:.1f}h, 第 {attempts} 次 → pending (退避 {backoff}s)"
                )
            reaped += 1

        logger.info(f"[Dispatcher] 本轮僵尸回收: {reaped} 个任务")
        return reaped

    def _reap_stale_pending_tasks(self) -> int:
        """将超时的 pending 任务标记为 failed，避免过期任务堆积干扰后续流程。"""
        self.mongo.connect()
        col = self.mongo.get_collection(CRAWL_TASKS_COLLECTION)
        cutoff = int(time.time()) - self.STALE_PENDING_TIMEOUT

        # 兼容旧任务（无 pending_since 字段的用 created_at 兜底）
        stale = list(
            col.find(
                {
                    "status": "pending",
                    "$or": [
                        {"pending_since": {"$lt": cutoff}},
                        {"pending_since": {"$exists": False}, "created_at": {"$lt": cutoff}},
                    ],
                }
            )
        )

        if not stale:
            return 0

        reaped = 0
        for task in stale:
            task_id = task["task_id"]
            platform = task.get("platform", "unknown")
            age_min = (time.time() - task.get("pending_since", task.get("created_at", 0))) / 60

            self._update_task_status(
                task_id,
                {
                    "status": "failed",
                    "error": f"stale_abandoned: pending 超过 {age_min:.0f} 分钟",
                    "end_time": int(time.time()),
                },
            )
            self._remove_from_redis(task_id)
            logger.warning(
                f"[Dispatcher] 过期回收: {task_id} ({platform}) "
                f"pending {age_min:.0f}min → failed"
            )
            reaped += 1

        if reaped:
            logger.info(f"[Dispatcher] 本轮过期 pending 回收: {reaped} 个任务")
        return reaped

    def _remove_from_redis(self, task_id: str) -> None:
        """尝试从 Redis 队列移除任务"""
        queue = self._get_task_queue()
        if not queue:
            return
        try:
            prefix = "user" if task_id.startswith("ut_") else "candidate"
            queue.remove_task(task_id, prefix=prefix)
        except Exception as e:
            logger.debug(f"[Dispatcher] Redis 移除 {task_id} 失败（可忽略）: {e}")

    # ==================== 任务获取 ====================

    def _fetch_pending_tasks(self) -> list[dict]:
        """
        获取待执行任务（Redis 优先，MongoDB 补充）。

        Redis 中的任务已按 score 排序（user > candidate），
        MongoDB 提供重试任务和 Redis 不可用时的降级。
        """
        # 1. 从 Redis 弹出（限量，避免一次取太多）
        redis_tasks = self._fetch_from_redis()
        # 2. 从 MongoDB 查询 pending 任务（含重试）
        mongo_tasks = self._fetch_from_mongo()
        # 3. 去重：Redis 优先
        redis_ids = {t["task_id"] for t in redis_tasks}
        mongo_tasks = [t for t in mongo_tasks if t["task_id"] not in redis_ids]
        return redis_tasks + mongo_tasks

    def _fetch_from_redis(self) -> list[dict]:
        """从 Redis 弹出任务（已按优先级排序）"""
        queue = self._get_task_queue()
        if not queue:
            return []

        tasks = []
        limit = len(self.platforms) * 2
        for _ in range(limit):
            task = queue.pop_task()
            if not task:
                break
            task["_from_redis"] = True
            tasks.append(task)
        return tasks

    def _fetch_from_mongo(self) -> list[dict]:
        """从 MongoDB 查询 pending 任务（含到期重试）"""
        self.mongo.connect()
        now = int(time.time())
        return self.mongo.find(
            CRAWL_TASKS_COLLECTION,
            {
                "status": "pending",
                "$or": [
                    {"next_retry_at": {"$exists": False}},
                    {"next_retry_at": {"$lte": now}},
                ],
            },
            sort=[("priority", -1), ("created_at", 1)],
        )

    # ==================== 任务状态更新 ====================

    def _ensure_task_in_mongo(self, task: dict) -> None:
        """确保任务在 MongoDB 中存在（用户任务可能只在 Redis 中）"""
        col = self.mongo.get_collection(CRAWL_TASKS_COLLECTION)
        if col.find_one({"task_id": task["task_id"]}):
            return
        doc = {k: v for k, v in task.items() if not k.startswith("_")}
        doc.setdefault("created_at", int(time.time()))
        doc.setdefault("attempts", 0)
        col.insert_one(doc)

    def _update_task_status(self, task_id: str, updates: dict):
        """更新任务状态（MongoDB + MySQL + 日志）"""
        col = self.mongo.get_collection(CRAWL_TASKS_COLLECTION)
        col.update_one({"task_id": task_id}, {"$set": updates})
        self._update_mysql_task_status(task_id, updates)
        self._log_task_status_change(task_id, updates)

    def _log_task_status_change(self, task_id: str, updates: dict) -> None:
        try:
            status_col = self.mongo.get_collection(TASK_STATUS_COLLECTION)
            status_col.insert_one(
                {
                    "task_id": task_id,
                    "status": updates.get("status"),
                    "updated_at": int(time.time()),
                }
            )
        except Exception as e:
            logger.warning(f"[Dispatcher] task_status 日志写入失败 {task_id}: {e}")

    def _update_mysql_task_status(self, task_id: str, updates: dict) -> None:
        try:
            engine = self._get_mysql_engine()
            now_ts = int(time.time())
            set_clauses = []
            params = {"task_id": task_id, "last_modify_ts": now_ts}

            if "status" in updates:
                set_clauses.append("task_status = :status")
                params["status"] = updates["status"]
                if updates["status"] == "running":
                    set_clauses.append("start_time = :start_time")
                    params["start_time"] = now_ts
                elif updates["status"] in ("completed", "failed"):
                    set_clauses.append("end_time = :end_time")
                    params["end_time"] = now_ts
                if "error" in updates and updates["status"] == "failed":
                    set_clauses.append("error_message = :error_message")
                    params["error_message"] = updates["error"]
                    set_clauses.append("error_count = error_count + 1")
                elif updates["status"] == "completed":
                    set_clauses.append("error_count = 0")

            if "total_crawled" in updates:
                set_clauses.append("total_crawled = :total_crawled")
                params["total_crawled"] = updates["total_crawled"]
            if "success_count" in updates:
                set_clauses.append("success_count = :success_count")
                params["success_count"] = updates["success_count"]

            if set_clauses:
                sql = f"UPDATE crawling_tasks SET {', '.join(set_clauses)} WHERE task_id = :task_id"
                with engine.begin() as conn:
                    conn.execute(text(sql), params)
        except Exception as e:
            logger.warning(f"[Dispatcher] MySQL 状态更新失败 {task_id}: {e}")

    def _insert_task_to_mysql(self, task: dict) -> None:
        try:
            engine = self._get_mysql_engine()
            now_ts = int(time.time())
            config_params = json.dumps(
                {
                    "max_notes": task.get("max_notes"),
                    "priority": task.get("priority"),
                    "topic_title": task.get("topic_title", ""),
                },
                ensure_ascii=False,
            )

            with engine.begin() as conn:
                conn.execute(
                    text("""
                    INSERT INTO crawling_tasks
                        (task_id, topic_id, platform, search_keywords,
                         task_status, start_time, config_params,
                         scheduled_date, add_ts, last_modify_ts)
                    VALUES
                        (:task_id, :topic_id, :platform, :search_keywords,
                         'pending', :start_time, :config_params,
                         :scheduled_date, :add_ts, :last_modify_ts)
                    ON DUPLICATE KEY UPDATE last_modify_ts = :last_modify_ts
                """),
                    {
                        "task_id": task["task_id"],
                        "topic_id": (
                            None
                            if task.get("candidate_id", "").startswith("user")
                            else task.get("candidate_id", "")
                        ),
                        "platform": task["platform"],
                        "search_keywords": json.dumps(
                            task.get("search_keywords", []), ensure_ascii=False
                        ),
                        "start_time": now_ts,
                        "config_params": config_params,
                        "scheduled_date": date.today(),
                        "add_ts": now_ts,
                        "last_modify_ts": now_ts,
                    },
                )
        except Exception as e:
            logger.warning(f"[Dispatcher] MySQL 任务插入失败 {task.get('task_id')}: {e}")

    # ==================== 任务执行 ====================

    async def _execute_one(self, task: dict):
        """执行单个任务"""
        platform = task["platform"]
        task_id = task["task_id"]

        if self.dry_run:
            logger.info(f"[Dispatcher] DRY RUN: 跳过任务 {task_id} ({platform})")
            return

        # running 状态已在 _dispatch_round 中提前标记
        self._insert_task_to_mysql(task)

        worker = self.workers.get(platform)
        if not worker:
            logger.error(f"[Dispatcher] 平台 {platform} 无 worker")
            self._update_task_status(task_id, {"status": "failed", "error": "no_worker"})
            return

        result = await worker.execute_task(task)
        status = result.get("status", "failed")

        if status == "success":
            total_crawled = result.get("total_crawled", 0)
            self._update_task_status(
                task_id,
                {
                    "status": "completed",
                    "completed_at": int(time.time()),
                    "total_crawled": total_crawled,
                    "success_count": total_crawled,
                },
            )
            self.failure_counts[platform] = 0
            logger.info(f"[Dispatcher] 任务 {task_id} 完成, 爬取 {total_crawled} 条")

            # 连续空结果计入失败计数，触发熔断以避免浪费后续任务
            if total_crawled == 0:
                self.failure_counts[platform] = self.failure_counts.get(platform, 0) + 1
                # 标记当前 cookie 过期（crawler 内部吞掉了 DataFetchError，
                # 不会抛到 worker 层，只能靠 0 结果推断 cookie 失效）
                used_cookie_id = result.get("cookie_id")
                if used_cookie_id:
                    self.cookie_manager.mark_expired(platform, cookie_id=used_cookie_id)
                    logger.warning(
                        f"[Dispatcher] 任务 {task_id} 爬取 0 条，"
                        f"标记 cookie {used_cookie_id} 过期，"
                        f"{platform} 连续空结果 {self.failure_counts[platform]} 次"
                    )
                else:
                    logger.warning(
                        f"[Dispatcher] 任务 {task_id} 爬取 0 条内容，"
                        f"{platform} 连续空结果 {self.failure_counts[platform]} 次"
                    )
                if self.failure_counts[platform] >= self.CIRCUIT_THRESHOLD:
                    self._trip_circuit(platform, f"连续 {self.CIRCUIT_THRESHOLD} 次爬取 0 条内容")

        elif status == "blocked":
            # cookie 缺失，退回 pending 并触发熔断器（避免每 10 秒无限重试）
            self._update_task_status(task_id, {"status": "pending", "pending_since": int(time.time())})
            logger.warning(f"[Dispatcher] 任务 {task_id} 因 cookie 缺失阻塞")
            if not self._is_circuit_open(platform):
                self._trip_circuit(platform, "cookie 缺失")

        else:
            # 失败处理
            attempts = task.get("attempts", 0) + 1
            self.failure_counts[platform] = self.failure_counts.get(platform, 0) + 1

            if attempts >= self.MAX_ATTEMPTS:
                self._update_task_status(
                    task_id,
                    {
                        "status": "failed",
                        "attempts": attempts,
                        "error": result.get("error", "unknown"),
                        "end_time": int(time.time()),
                    },
                )
                logger.warning(f"[Dispatcher] 任务 {task_id} 重试耗尽，标记为失败")
            else:
                backoff = self.RETRY_BACKOFF[min(attempts - 1, len(self.RETRY_BACKOFF) - 1)]
                next_retry = int(time.time()) + backoff
                self._update_task_status(
                    task_id,
                    {
                        "status": "pending",
                        "attempts": attempts,
                        "next_retry_at": next_retry,
                        "last_error": result.get("error", "unknown"),
                        "pending_since": int(time.time()),
                    },
                )
                logger.info(
                    f"[Dispatcher] 任务 {task_id} 第 {attempts} 次失败，" f"{backoff}s 后重试"
                )

            # 检查熔断
            if self.failure_counts[platform] >= self.CIRCUIT_THRESHOLD:
                self._trip_circuit(platform, f"连续 {self.CIRCUIT_THRESHOLD} 次失败")

    # ==================== 调度循环 ====================

    async def _dispatch_round(self):
        """执行一轮调度"""
        tasks = self._fetch_pending_tasks()
        if not tasks:
            return

        dispatched = []
        push_back = []  # 锁占用的 Redis 任务，需推回
        circuit_dropped: dict[str, int] = {}  # 熔断丢弃计数 {platform: count}

        for task in tasks:
            platform = task["platform"]

            if platform not in self.platforms:
                continue

            # 熔断 → Redis 任务直接丢弃（MongoDB 中已有记录，恢复后自然拾起）
            if self._is_circuit_open(platform):
                if task.get("_from_redis"):
                    self._ensure_task_in_mongo(task)
                    circuit_dropped[platform] = circuit_dropped.get(platform, 0) + 1
                continue

            # 平台锁已占用
            lock = self.platform_locks.get(platform)
            if lock and lock.locked():
                # user 任务：等锁（create_task 会排队获取锁后执行）
                if task.get("_source") == "user":

                    async def _run_wait(t=task, p=platform):
                        async with self.platform_locks[p]:
                            await self._execute_one(t)

                    dispatched.append(asyncio.create_task(_run_wait()))
                    continue
                # 系统任务：锁占用 → 推回 Redis（锁很快释放）
                if task.get("_from_redis"):
                    push_back.append(task)
                continue

            # 提前标记 running，防止下一轮调度重复拉取
            self._ensure_task_in_mongo(task)
            self._update_task_status(
                task["task_id"],
                {"status": "running", "started_at": int(time.time())},
            )

            # 启动异步任务
            async def _run(t=task, p=platform):
                async with self.platform_locks[p]:
                    await self._execute_one(t)

            dispatched.append(asyncio.create_task(_run()))

        # 熔断丢弃日志（每平台只输出一次，恢复后重置）
        for plat, count in circuit_dropped.items():
            if plat not in self._circuit_drop_logged:
                logger.info(
                    f"[Dispatcher] {plat} 熔断中，丢弃 {count} 个 Redis 任务（MongoDB 中已有记录，恢复后自动拾起）"
                )
                self._circuit_drop_logged.add(plat)

        # 将锁占用的 Redis 任务推回队列
        queue = self._get_task_queue()
        if push_back and queue:
            for task in push_back:
                score = task.get("_redis_score", 10000)
                queue.push_back(task, score)
            logger.debug(f"[Dispatcher] {len(push_back)} 个任务推回 Redis 队列（锁占用）")

        if dispatched:
            logger.info(f"[Dispatcher] 本轮调度 {len(dispatched)} 个任务")
            for t in dispatched:
                self._running_tasks.add(t)
                t.add_done_callback(self._running_tasks.discard)

    async def _zombie_reaper_loop(self):
        """独立的僵尸回收循环（不受调度阻塞影响）"""
        while self._running:
            try:
                self._reap_zombie_tasks()
                self._reap_stale_pending_tasks()
            except Exception as e:
                logger.error(f"[Dispatcher] 僵尸回收异常: {e}")
            await asyncio.sleep(300)

    async def run(self):
        """启动调度主循环"""
        self._running = True
        self.ensure_indexes()
        self.cookie_manager.ensure_indexes()

        # 尝试连接 Redis
        queue = self._get_task_queue()
        redis_status = "已连接" if queue else "不可用（降级到 MongoDB 轮询）"

        logger.info(
            f"[Dispatcher] 启动调度器\n"
            f"  平台: {self.platforms}\n"
            f"  轮询间隔: {self.POLL_INTERVAL}s\n"
            f"  Redis: {redis_status}\n"
            f"  dry_run: {self.dry_run}"
        )

        # 启动时立即回收僵尸任务（处理上次异常退出遗留的 running 任务）
        try:
            self._reap_zombie_tasks()
            self._reap_stale_pending_tasks()
        except Exception as e:
            logger.error(f"[Dispatcher] 启动僵尸回收异常: {e}")

        # 启动独立僵尸回收循环
        asyncio.create_task(self._zombie_reaper_loop())

        while self._running:
            try:
                await self._dispatch_round()
            except Exception as e:
                logger.error(f"[Dispatcher] 调度轮次异常: {e}")

            await asyncio.sleep(self.POLL_INTERVAL)

    def stop(self):
        self._running = False
        logger.info("[Dispatcher] 调度器停止信号已发送")

    def get_stats(self) -> dict:
        self.mongo.connect()
        col = self.mongo.get_collection(CRAWL_TASKS_COLLECTION)
        stats = {
            "pending": col.count_documents({"status": "pending"}),
            "running": col.count_documents({"status": "running"}),
            "completed": col.count_documents({"status": "completed"}),
            "failed": col.count_documents({"status": "failed"}),
            "circuit_breakers": {
                p: "open" if self._is_circuit_open(p) else "closed" for p in self.platforms
            },
        }
        queue = self._get_task_queue()
        if queue:
            stats["redis_queue_size"] = queue.get_queue_size()
        return stats
