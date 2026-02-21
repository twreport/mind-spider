# -*- coding: utf-8 -*-
"""
TaskDispatcher — 异步任务调度器

从 MongoDB crawl_tasks 轮询待执行任务，按优先级调度到 PlatformWorker。
每个平台同时只运行一个任务（平台锁），连续失败触发熔断器。
"""

import asyncio
import time
from typing import Optional

from loguru import logger

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import settings

from BroadTopicExtraction.pipeline.mongo_writer import MongoWriter
from DeepSentimentCrawling.worker import PlatformWorker
from DeepSentimentCrawling.cookie_manager import CookieManager
from DeepSentimentCrawling.alert import alert_circuit_open

CRAWL_TASKS_COLLECTION = "crawl_tasks"

# 所有支持的平台
ALL_PLATFORMS = ["xhs", "dy", "bili", "wb", "ks", "tieba", "zhihu"]


class TaskDispatcher:
    """异步爬取任务调度器"""

    POLL_INTERVAL = 30        # 轮询间隔（秒）
    CIRCUIT_THRESHOLD = 3     # 连续失败次数触发熔断
    CIRCUIT_RESET_SEC = 1800  # 熔断恢复时间（30 分钟）
    MAX_ATTEMPTS = 3          # 单任务最大重试次数
    RETRY_BACKOFF = [120, 240, 480]  # 重试退避（秒）

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

        self.workers: dict[str, PlatformWorker] = {}
        self.platform_locks: dict[str, asyncio.Lock] = {}
        self.failure_counts: dict[str, int] = {}
        self.circuit_open_until: dict[str, float] = {}

        self._running = False

        for plat in self.platforms:
            self.workers[plat] = PlatformWorker(cookie_manager=self.cookie_manager)
            self.platform_locks[plat] = asyncio.Lock()
            self.failure_counts[plat] = 0
            self.circuit_open_until[plat] = 0

    def ensure_indexes(self):
        """创建 crawl_tasks 索引"""
        self.mongo.connect()
        self.mongo.create_indexes(CRAWL_TASKS_COLLECTION, [
            {"keys": [("task_id", 1)], "options": {"unique": True}},
            {"keys": [("status", 1), ("priority", -1), ("created_at", 1)]},
            {"keys": [("candidate_id", 1), ("platform", 1)]},
        ])

    def _is_circuit_open(self, platform: str) -> bool:
        """检查平台熔断器状态"""
        until = self.circuit_open_until.get(platform, 0)
        if until > 0 and time.time() < until:
            return True
        # 自动恢复
        if until > 0:
            self.circuit_open_until[platform] = 0
            self.failure_counts[platform] = 0
            logger.info(f"[Dispatcher] {platform} 熔断器已自动恢复")
        return False

    def _trip_circuit(self, platform: str, reason: str):
        """触发熔断器"""
        self.circuit_open_until[platform] = time.time() + self.CIRCUIT_RESET_SEC
        logger.warning(f"[Dispatcher] {platform} 熔断器触发: {reason}")
        alert_circuit_open(platform, reason)

    def _fetch_pending_tasks(self) -> list[dict]:
        """查询待执行任务，按 priority DESC, created_at ASC 排序"""
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

    def _update_task_status(self, task_id: str, updates: dict):
        """更新任务状态"""
        col = self.mongo.get_collection(CRAWL_TASKS_COLLECTION)
        col.update_one({"task_id": task_id}, {"$set": updates})

    async def _execute_one(self, task: dict):
        """执行单个任务（在平台锁内）"""
        platform = task["platform"]
        task_id = task["task_id"]

        if self.dry_run:
            logger.info(f"[Dispatcher] DRY RUN: 跳过任务 {task_id} ({platform})")
            return

        # 标记为 running
        self._update_task_status(task_id, {
            "status": "running",
            "started_at": int(time.time()),
        })

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
            # cookie 缺失，退回 pending，不计入重试
            self._update_task_status(task_id, {"status": "pending"})
            logger.warning(f"[Dispatcher] 任务 {task_id} 因 cookie 缺失阻塞")

        else:
            # 失败
            attempts = task.get("attempts", 0) + 1
            self.failure_counts[platform] = self.failure_counts.get(platform, 0) + 1

            if attempts >= self.MAX_ATTEMPTS:
                self._update_task_status(task_id, {
                    "status": "failed",
                    "attempts": attempts,
                    "error": result.get("error", "unknown"),
                    "failed_at": int(time.time()),
                })
                logger.warning(f"[Dispatcher] 任务 {task_id} 重试耗尽，标记为失败")
            else:
                # 计算退避时间
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

    async def _dispatch_round(self):
        """执行一轮调度"""
        tasks = self._fetch_pending_tasks()
        if not tasks:
            return

        dispatched = []
        for task in tasks:
            platform = task["platform"]

            # 跳过不支持的平台
            if platform not in self.platforms:
                continue

            # 检查熔断器
            if self._is_circuit_open(platform):
                continue

            # 检查平台锁（非阻塞）
            lock = self.platform_locks.get(platform)
            if lock and lock.locked():
                continue

            # 启动异步任务
            async def _run(t=task, p=platform):
                async with self.platform_locks[p]:
                    await self._execute_one(t)

            dispatched.append(asyncio.create_task(_run()))

        if dispatched:
            logger.info(f"[Dispatcher] 本轮调度 {len(dispatched)} 个任务")
            await asyncio.gather(*dispatched, return_exceptions=True)

    async def run(self):
        """启动调度主循环"""
        self._running = True
        self.ensure_indexes()
        self.cookie_manager.ensure_indexes()

        logger.info(
            f"[Dispatcher] 启动调度器，平台: {self.platforms}，"
            f"轮询间隔: {self.POLL_INTERVAL}s，"
            f"dry_run: {self.dry_run}"
        )

        while self._running:
            try:
                await self._dispatch_round()
            except Exception as e:
                logger.error(f"[Dispatcher] 调度轮次异常: {e}")

            await asyncio.sleep(self.POLL_INTERVAL)

    def stop(self):
        """停止调度器"""
        self._running = False
        logger.info("[Dispatcher] 调度器停止信号已发送")

    def get_stats(self) -> dict:
        """获取调度器状态信息"""
        self.mongo.connect()
        col = self.mongo.get_collection(CRAWL_TASKS_COLLECTION)
        return {
            "pending": col.count_documents({"status": "pending"}),
            "running": col.count_documents({"status": "running"}),
            "completed": col.count_documents({"status": "completed"}),
            "failed": col.count_documents({"status": "failed"}),
            "circuit_breakers": {
                p: "open" if self._is_circuit_open(p) else "closed"
                for p in self.platforms
            },
        }
