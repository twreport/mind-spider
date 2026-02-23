# -*- coding: utf-8 -*-
"""
深度爬取任务队列 — 基于 Redis ZSET + HASH

ZSET (mindspider:task_queue)  → 排序：member=prefixed_id, score=优先级分数
HASH (mindspider:task_data)   → 数据：field=prefixed_id, value=JSON task

优先级分层（score 越小，zpopmin 越先弹出）：
  - user 任务:      score ≈ timestamp           (0 × 1e10 + ts)
  - candidate 任务:  score ≈ 1e10 + timestamp    (1 × 1e10 + ts)

同一层内按时间 FIFO。
"""

import json
import time
from typing import List, Optional

from loguru import logger
from redis import Redis

import sys
from pathlib import Path

_PROJECT_ROOT = str(Path(__file__).parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
from ms_config import settings


class TaskQueue:
    """深度爬取任务队列（Redis ZSET + HASH）"""

    QUEUE_KEY = "mindspider:task_queue"
    DATA_KEY = "mindspider:task_data"

    # 优先级分层：user < candidate（score 越小优先级越高）
    TIER_USER = 0
    TIER_CANDIDATE = 1

    def __init__(self):
        self._redis: Optional[Redis] = None
        self._connected = False

    # ---------- 连接管理 ----------

    def connect(self) -> None:
        if self._connected:
            return
        self._redis = Redis(
            host=settings.REDIS_DB_HOST,
            port=settings.REDIS_DB_PORT,
            password=settings.REDIS_DB_PWD or None,
            db=settings.REDIS_DB_NUM,
            decode_responses=True,
        )
        self._redis.ping()
        self._connected = True
        logger.info(f"[TaskQueue] Redis 已连接: {settings.REDIS_DB_HOST}:{settings.REDIS_DB_PORT}")

    def disconnect(self) -> None:
        if self._redis:
            self._redis.close()
            self._connected = False

    def _ensure(self):
        if not self._connected:
            self.connect()

    # ---------- 入队 ----------

    def push_user_task(self, task: dict) -> str:
        """
        推送用户任务（最高优先级）。

        task 必须包含 task_id, platform, search_keywords 等字段，
        与 MongoDB crawl_tasks 文档格式一致。
        """
        self._ensure()
        task_id = task.get("task_id")
        if not task_id:
            raise ValueError("task_id 不能为空")

        prefixed_id = f"user:{task_id}"
        score = self.TIER_USER * 1e10 + time.time()

        pipe = self._redis.pipeline()
        pipe.zrem(self.QUEUE_KEY, prefixed_id)
        pipe.hdel(self.DATA_KEY, prefixed_id)
        pipe.zadd(self.QUEUE_KEY, {prefixed_id: score})
        pipe.hset(
            self.DATA_KEY, prefixed_id,
            json.dumps(task, ensure_ascii=False, default=str),
        )
        pipe.execute()

        logger.info(f"[TaskQueue] 用户任务入队: {task_id} (score={score:.0f})")
        return task_id

    def push_candidate_task(self, candidate_id: str, status: str, task: dict) -> str:
        """
        推送候选状态触发的任务（普通优先级）。
        """
        self._ensure()
        task_id = task.get("task_id")
        if not task_id:
            raise ValueError("task_id 不能为空")

        prefixed_id = f"candidate:{status}:{task_id}"
        score = self.TIER_CANDIDATE * 1e10 + time.time()

        pipe = self._redis.pipeline()
        pipe.zrem(self.QUEUE_KEY, prefixed_id)
        pipe.hdel(self.DATA_KEY, prefixed_id)
        pipe.zadd(self.QUEUE_KEY, {prefixed_id: score})
        pipe.hset(
            self.DATA_KEY, prefixed_id,
            json.dumps(task, ensure_ascii=False, default=str),
        )
        pipe.execute()

        logger.info(
            f"[TaskQueue] 候选任务入队: {task_id} "
            f"(status={status}, score={score:.0f})"
        )
        return task_id

    # ---------- 出队 ----------

    def pop_task(self) -> Optional[dict]:
        """
        取出优先级最高的任务。

        返回 task dict（含 _source / _redis_score / _prefixed_id 内部字段），
        队列为空返回 None。
        """
        self._ensure()
        result = self._redis.zpopmin(self.QUEUE_KEY)
        if not result:
            return None

        prefixed_id, score = result[0]

        # 从 HASH 取任务数据并清理
        task_json = self._redis.hget(self.DATA_KEY, prefixed_id)
        self._redis.hdel(self.DATA_KEY, prefixed_id)

        if not task_json:
            logger.warning(f"[TaskQueue] {prefixed_id} 无任务数据，跳过")
            return None

        task = json.loads(task_json)
        task["_source"] = prefixed_id.split(":")[0]   # "user" / "candidate"
        task["_redis_score"] = score
        task["_prefixed_id"] = prefixed_id
        return task

    def push_back(self, task: dict, score: float) -> None:
        """将未能执行的任务推回队列（保留原 score）。"""
        self._ensure()
        prefixed_id = task.get("_prefixed_id")
        if not prefixed_id:
            return

        clean = {k: v for k, v in task.items() if not k.startswith("_")}
        pipe = self._redis.pipeline()
        pipe.zadd(self.QUEUE_KEY, {prefixed_id: score})
        pipe.hset(
            self.DATA_KEY, prefixed_id,
            json.dumps(clean, ensure_ascii=False, default=str),
        )
        pipe.execute()

    # ---------- 查询 ----------

    def peek_tasks(self, limit: int = 10) -> List[dict]:
        """查看队列前 N 个任务（不取出）"""
        self._ensure()
        results = self._redis.zrange(self.QUEUE_KEY, 0, limit - 1, withscores=True)

        tasks = []
        for prefixed_id, score in results:
            task_json = self._redis.hget(self.DATA_KEY, prefixed_id)
            if task_json:
                task = json.loads(task_json)
                task["_source"] = prefixed_id.split(":")[0]
                task["_redis_score"] = score
                task["_prefixed_id"] = prefixed_id
                tasks.append(task)
        return tasks

    def get_queue_size(self) -> int:
        self._ensure()
        return self._redis.zcard(self.QUEUE_KEY)

    def remove_task(self, task_id: str, prefix: str = "user") -> bool:
        """按 task_id 移除任务（支持 user / candidate 前缀）"""
        self._ensure()
        patterns = [f"{prefix}:{task_id}"]
        if prefix == "candidate":
            for status in ("exploded", "confirmed", "rising"):
                patterns.append(f"candidate:{status}:{task_id}")

        removed = False
        for pid in patterns:
            if self._redis.zrem(self.QUEUE_KEY, pid):
                self._redis.hdel(self.DATA_KEY, pid)
                removed = True
                logger.info(f"[TaskQueue] 已移除: {pid}")
        return removed

    def clear_queue(self) -> int:
        """清空整个队列"""
        self._ensure()
        size = self._redis.zcard(self.QUEUE_KEY)
        if size:
            self._redis.delete(self.QUEUE_KEY)
            self._redis.delete(self.DATA_KEY)
        logger.info(f"[TaskQueue] 清空队列，移除 {size} 个任务")
        return size


# -------- 全局单例 --------

_task_queue: Optional[TaskQueue] = None


def get_task_queue() -> TaskQueue:
    """获取 TaskQueue 单例（自动连接）"""
    global _task_queue
    if _task_queue is None:
        _task_queue = TaskQueue()
        _task_queue.connect()
    return _task_queue


# -------- CLI 测试 --------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="深度爬取任务队列测试")
    parser.add_argument("--push-user", type=str, help="推送用户任务 (platform:关键词)")
    parser.add_argument("--push-candidate", type=str,
                        help="推送候选任务 (candidate_id:status:platform)")
    parser.add_argument("--pop", action="store_true", help="取出一个任务")
    parser.add_argument("--peek", type=int, default=0, help="查看前 N 个任务")
    parser.add_argument("--size", action="store_true", help="查看队列大小")
    parser.add_argument("--clear", action="store_true", help="清空队列")

    args = parser.parse_args()
    queue = get_task_queue()

    if args.push_user:
        parts = args.push_user.split(":", 1)
        platform = parts[0]
        keyword = parts[1] if len(parts) > 1 else "测试关键词"
        task_id = f"ut_{platform}_{int(time.time())}"
        task = {
            "task_id": task_id,
            "candidate_id": "user_manual",
            "topic_title": keyword,
            "platform": platform,
            "search_keywords": [keyword],
            "max_notes": 20,
            "priority": 100,
            "status": "pending",
            "created_at": int(time.time()),
            "attempts": 0,
        }
        queue.push_user_task(task)
        print(f"OK  用户任务已推送: {task_id}")

    elif args.push_candidate:
        parts = args.push_candidate.split(":")
        if len(parts) >= 3:
            cid, status, platform = parts[0], parts[1], parts[2]
            task_id = f"ct_{cid}_{platform}_{int(time.time())}"
            task = {
                "task_id": task_id,
                "candidate_id": cid,
                "topic_title": f"候选-{status}",
                "platform": platform,
                "search_keywords": [f"kw_{status}"],
                "max_notes": 50,
                "priority": 3,
                "status": "pending",
                "created_at": int(time.time()),
                "attempts": 0,
            }
            queue.push_candidate_task(cid, status, task)
            print(f"OK  候选任务已推送: {task_id}")
        else:
            print("格式: candidate_id:status:platform  (如 abc:exploded:xhs)")

    elif args.pop:
        task = queue.pop_task()
        if task:
            src = task.get("_source", "?")
            print(f"[{src}] {task['task_id']}  platform={task.get('platform')}"
                  f"  keywords={task.get('search_keywords')}")
        else:
            print("队列为空")

    elif args.peek:
        tasks = queue.peek_tasks(args.peek)
        print(f"队列前 {len(tasks)} 个任务:")
        for i, t in enumerate(tasks, 1):
            src = t.get("_source", "?")
            score = t.get("_redis_score", 0)
            print(f"  {i}. [{src}] {t['task_id'][:40]}  "
                  f"platform={t.get('platform')}  score={score:.0f}")

    elif args.size:
        print(f"队列大小: {queue.get_queue_size()}")

    elif args.clear:
        count = queue.clear_queue()
        print(f"已清空 {count} 个任务")

    queue.disconnect()
