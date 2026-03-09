# -*- coding: utf-8 -*-
"""
指标查询模块

从 MongoDB crawl_tasks / platform_cookies 聚合深层采集指标。
"""

import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from loguru import logger


def get_overview(mongo, dispatcher=None) -> Dict:
    """
    总览统计：各状态任务数、Redis 队列深度。

    dispatcher 可选，用于获取 Redis 队列大小。
    """
    mongo.connect()
    col = mongo.get_collection("crawl_tasks")

    today_start = int(datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp())

    stats = {
        "pending": col.count_documents({"status": "pending"}),
        "running": col.count_documents({"status": "running"}),
        "completed_today": col.count_documents(
            {"status": "completed", "completed_at": {"$gte": today_start}}
        ),
        "completed_total": col.count_documents({"status": "completed"}),
        "failed": col.count_documents({"status": "failed"}),
        "redis_queue_size": 0,
    }

    if dispatcher:
        try:
            queue = dispatcher._get_task_queue()
            if queue:
                stats["redis_queue_size"] = queue.get_queue_size()
        except Exception:
            pass

    return stats


def get_platform_health(mongo, cookie_manager=None, dispatcher=None) -> List[Dict]:
    """
    7 平台健康看板：cookie 状态、熔断器、最近任务、成功率。
    """
    from DeepSentimentCrawling.dispatcher import ALL_PLATFORMS

    mongo.connect()
    col = mongo.get_collection("crawl_tasks")
    now = int(time.time())
    h24_ago = now - 86400

    # cookie 状态
    cookie_statuses = {}
    if cookie_manager:
        for s in cookie_manager.get_all_status():
            cookie_statuses[s["platform"]] = s

    result = []
    for plat in ALL_PLATFORMS:
        # cookie 信息
        cs = cookie_statuses.get(plat, {})
        cookie_status = cs.get("status", "missing")
        cookie_saved_at = cs.get("saved_at")

        # 熔断器状态
        circuit = "closed"
        if dispatcher:
            circuit = "open" if dispatcher.circuit_open.get(plat, False) else "closed"

        # 最近一个任务
        last_task_doc = col.find_one(
            {"platform": plat},
            {"_id": 0},
            sort=[("created_at", -1)],
        )
        last_task = None
        if last_task_doc:
            last_task = {
                "status": last_task_doc.get("status"),
                "topic": last_task_doc.get("topic_title", ""),
                "item_count": last_task_doc.get("total_crawled", 0),
                "duration": None,
                "finished_at": last_task_doc.get("completed_at"),
            }
            started = last_task_doc.get("started_at")
            finished = last_task_doc.get("completed_at")
            if started and finished:
                last_task["duration"] = finished - started

        # 近 24h 统计
        total_24h = col.count_documents({"platform": plat, "created_at": {"$gte": h24_ago}})
        completed_24h = col.count_documents(
            {"platform": plat, "status": "completed", "created_at": {"$gte": h24_ago}}
        )
        failed_24h = col.count_documents(
            {"platform": plat, "status": "failed", "created_at": {"$gte": h24_ago}}
        )
        success_rate = round(completed_24h / total_24h * 100, 1) if total_24h > 0 else None

        result.append(
            {
                "platform": plat,
                "cookie_status": cookie_status,
                "cookie_saved_at": cookie_saved_at,
                "circuit_breaker": circuit,
                "last_task": last_task,
                "stats_24h": {
                    "total": total_24h,
                    "completed": completed_24h,
                    "failed": failed_24h,
                    "success_rate": success_rate,
                },
            }
        )

    return result


def get_task_list(
    mongo,
    platform: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> Dict:
    """任务列表（分页、可按平台/状态筛选）"""
    mongo.connect()
    col = mongo.get_collection("crawl_tasks")

    query: dict = {}
    if platform:
        query["platform"] = platform
    if status:
        query["status"] = status

    total = col.count_documents(query)
    docs = list(col.find(query, {"_id": 0}).sort([("created_at", -1)]).skip(offset).limit(limit))

    return {"total": total, "tasks": docs}


def get_volume_trend(mongo, hours: int = 48) -> Dict[str, List[Dict]]:
    """
    各平台数据产量趋势（按小时聚合 completed 任务数量）。

    返回 {platform: [{hour: "2026-03-09T12:00", count: 5}, ...]}
    """
    from DeepSentimentCrawling.dispatcher import ALL_PLATFORMS

    mongo.connect()
    col = mongo.get_collection("crawl_tasks")
    since = int((datetime.now() - timedelta(hours=hours)).timestamp())

    result = {}
    for plat in ALL_PLATFORMS:
        try:
            pipeline = [
                {
                    "$match": {
                        "platform": plat,
                        "status": "completed",
                        "completed_at": {"$gte": since},
                    }
                },
                {
                    "$addFields": {
                        "completed_dt": {"$toDate": {"$multiply": ["$completed_at", 1000]}}
                    }
                },
                {
                    "$group": {
                        "_id": {
                            "year": {"$year": "$completed_dt"},
                            "month": {"$month": "$completed_dt"},
                            "day": {"$dayOfMonth": "$completed_dt"},
                            "hour": {"$hour": "$completed_dt"},
                        },
                        "count": {"$sum": 1},
                    }
                },
                {"$sort": {"_id.year": 1, "_id.month": 1, "_id.day": 1, "_id.hour": 1}},
            ]
            docs = list(col.aggregate(pipeline))
            hourly = []
            for doc in docs:
                d = doc["_id"]
                hour_str = f"{d['year']:04d}-{d['month']:02d}-{d['day']:02d}T{d['hour']:02d}:00"
                hourly.append({"hour": hour_str, "count": doc["count"]})
            result[plat] = hourly
        except Exception as e:
            logger.warning(f"[DeepDashboard] 聚合 {plat} 趋势失败: {e}")
            result[plat] = []

    return result
