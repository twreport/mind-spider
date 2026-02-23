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

    POLL_INTERVAL = 10        # 轮询间隔（秒）
    CIRCUIT_THRESHOLD = 3     # 连续失败次数触发熔断
    CIRCUIT_RESET_SEC = 1800  # 熔断恢复时间（30 分钟）
    MAX_ATTEMPTS = 3          # 单任务最大重试次数
    RETRY_BACKOFF = [120, 240, 480]  # 重试退避（秒）
    HEALTH_CHECK_INTERVAL = 30  # 每 30 轮执行一次 cookie 健康检查（≈5 分钟）

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
        self.circuit_open_until: dict[str, float] = {}

        self._running = False
        self._health_check_counter = 0

        for plat in self.platforms:
            self.workers[plat] = PlatformWorker(cookie_manager=self.cookie_manager)
            self.platform_locks[plat] = asyncio.Lock()
            self.failure_counts[plat] = 0
            self.circuit_open_until[plat] = 0

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
            self._mysql_engine = create_engine(url, future=True)
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
        self.mongo.create_indexes(CRAWL_TASKS_COLLECTION, [
            {"keys": [("task_id", 1)], "options": {"unique": True}},
            {"keys": [("status", 1), ("priority", -1), ("created_at", 1)]},
            {"keys": [("candidate_id", 1), ("platform", 1)]},
        ])
        self.mongo.create_indexes(TASK_STATUS_COLLECTION, [
            {"keys": [("task_id", 1)]},
            {"keys": [("status", 1)]},
            {"keys": [("updated_at", -1)]},
        ])

    # ==================== 熔断器 ====================

    def _is_circuit_open(self, platform: str) -> bool:
        until = self.circuit_open_until.get(platform, 0)
        if until > 0 and time.time() < until:
            return True
        if until > 0:
            self.circuit_open_until[platform] = 0
            self.failure_counts[platform] = 0
            logger.info(f"[Dispatcher] {platform} 熔断器已自动恢复")
        return False

    def _trip_circuit(self, platform: str, reason: str):
        self.circuit_open_until[platform] = time.time() + self.CIRCUIT_RESET_SEC
        logger.warning(f"[Dispatcher] {platform} 熔断器触发: {reason}")
        alert_circuit_open(platform, reason)

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
            status_col.insert_one({
                "task_id": task_id,
                "status": updates.get("status"),
                "updated_at": int(time.time()),
            })
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
            config_params = json.dumps({
                "max_notes": task.get("max_notes"),
                "priority": task.get("priority"),
                "topic_title": task.get("topic_title", ""),
            }, ensure_ascii=False)

            with engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO crawling_tasks
                        (task_id, topic_id, platform, search_keywords,
                         task_status, start_time, config_params,
                         scheduled_date, add_ts, last_modify_ts)
                    VALUES
                        (:task_id, :topic_id, :platform, :search_keywords,
                         'pending', :start_time, :config_params,
                         :scheduled_date, :add_ts, :last_modify_ts)
                    ON DUPLICATE KEY UPDATE last_modify_ts = :last_modify_ts
                """), {
                    "task_id": task["task_id"],
                    "topic_id": None if task.get("candidate_id", "").startswith("user") else task.get("candidate_id", ""),
                    "platform": task["platform"],
                    "search_keywords": json.dumps(
                        task.get("search_keywords", []), ensure_ascii=False
                    ),
                    "start_time": now_ts,
                    "config_params": config_params,
                    "scheduled_date": date.today(),
                    "add_ts": now_ts,
                    "last_modify_ts": now_ts,
                })
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

        # 确保 MongoDB 中有任务文档（用户任务可能只在 Redis 中）
        self._ensure_task_in_mongo(task)

        # 标记为 running
        self._update_task_status(task_id, {
            "status": "running",
            "started_at": int(time.time()),
        })
        self._insert_task_to_mysql(task)

        worker = self.workers.get(platform)
        if not worker:
            logger.error(f"[Dispatcher] 平台 {platform} 无 worker")
            self._update_task_status(task_id, {"status": "failed", "error": "no_worker"})
            return

        result = await worker.execute_task(task)
        status = result.get("status", "failed")

        if status == "success":
            self._update_task_status(task_id, {
                "status": "completed",
                "completed_at": int(time.time()),
            })
            self.failure_counts[platform] = 0
            logger.info(f"[Dispatcher] 任务 {task_id} 完成")

        elif status == "blocked":
            # cookie 缺失，退回 pending（不计入重试）
            self._update_task_status(task_id, {"status": "pending"})
            logger.warning(f"[Dispatcher] 任务 {task_id} 因 cookie 缺失阻塞")

        else:
            # 失败处理
            attempts = task.get("attempts", 0) + 1
            self.failure_counts[platform] = self.failure_counts.get(platform, 0) + 1

            if attempts >= self.MAX_ATTEMPTS:
                self._update_task_status(task_id, {
                    "status": "failed",
                    "attempts": attempts,
                    "error": result.get("error", "unknown"),
                    "end_time": int(time.time()),
                })
                logger.warning(f"[Dispatcher] 任务 {task_id} 重试耗尽，标记为失败")
            else:
                backoff = self.RETRY_BACKOFF[min(attempts - 1, len(self.RETRY_BACKOFF) - 1)]
                next_retry = int(time.time()) + backoff
                self._update_task_status(task_id, {
                    "status": "pending",
                    "attempts": attempts,
                    "next_retry_at": next_retry,
                    "last_error": result.get("error", "unknown"),
                })
                logger.info(
                    f"[Dispatcher] 任务 {task_id} 第 {attempts} 次失败，"
                    f"{backoff}s 后重试"
                )

            # 检查熔断
            if self.failure_counts[platform] >= self.CIRCUIT_THRESHOLD:
                self._trip_circuit(platform, f"连续 {self.CIRCUIT_THRESHOLD} 次失败")

    # ==================== Cookie 健康检查 ====================

    async def _cookie_health_check(self):
        """定时巡检所有平台 cookie 健康状态"""
        for platform in self.platforms:
            cookies = await asyncio.to_thread(self.cookie_manager.load_cookies, platform)
            if not cookies:
                continue  # 没 cookie 的平台跳过（已是 missing/expired）

            healthy = await asyncio.to_thread(self.cookie_manager.check_health, platform)
            if not healthy:
                logger.warning(f"[Dispatcher] 健康检查失败: {platform} cookie 可能已过期")
                await asyncio.to_thread(self.cookie_manager.mark_expired, platform)

    # ==================== 调度循环 ====================

    async def _dispatch_round(self):
        """执行一轮调度"""
        tasks = self._fetch_pending_tasks()
        if not tasks:
            return

        dispatched = []
        push_back = []  # 未能调度的 Redis 任务，需推回

        for task in tasks:
            platform = task["platform"]

            if platform not in self.platforms:
                continue

            # 熔断 → 跳过
            if self._is_circuit_open(platform):
                if task.get("_from_redis"):
                    push_back.append(task)
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
                # 系统任务：跳过
                if task.get("_from_redis"):
                    push_back.append(task)
                continue

            # 启动异步任务
            async def _run(t=task, p=platform):
                async with self.platform_locks[p]:
                    await self._execute_one(t)

            dispatched.append(asyncio.create_task(_run()))

        # 将未调度的 Redis 任务推回队列
        queue = self._get_task_queue()
        if push_back and queue:
            for task in push_back:
                score = task.get("_redis_score", 10000)
                queue.push_back(task, score)
            logger.debug(f"[Dispatcher] {len(push_back)} 个任务推回 Redis 队列")

        if dispatched:
            logger.info(f"[Dispatcher] 本轮调度 {len(dispatched)} 个任务")
            await asyncio.gather(*dispatched, return_exceptions=True)

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

        while self._running:
            try:
                self._health_check_counter += 1
                if self._health_check_counter >= self.HEALTH_CHECK_INTERVAL:
                    self._health_check_counter = 0
                    await self._cookie_health_check()

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
                p: "open" if self._is_circuit_open(p) else "closed"
                for p in self.platforms
            },
        }
        queue = self._get_task_queue()
        if queue:
            stats["redis_queue_size"] = queue.get_queue_size()
        return stats
