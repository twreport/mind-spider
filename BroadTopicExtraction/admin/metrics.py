# -*- coding: utf-8 -*-
"""
指标查询模块

从 MongoDB crawl_runs 集合聚合爬虫健康指标。
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional

from loguru import logger

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from pipeline.mongo_writer import MongoWriter
from pipeline.config_loader import ConfigLoader


def ensure_indexes(mongo: MongoWriter) -> None:
    """启动时创建 crawl_runs 索引"""
    try:
        mongo.create_indexes("crawl_runs", [
            {
                "keys": [("source_name", 1), ("started_at", -1)],
                "options": {"name": "source_name_started_at"},
            },
            {
                "keys": [("started_at", 1)],
                "options": {"name": "ttl_30d", "expireAfterSeconds": 30 * 24 * 3600},
            },
        ])
        logger.info("[Admin] crawl_runs 索引已创建")
    except Exception as e:
        logger.warning(f"[Admin] 创建索引失败: {e}")


def get_source_statuses(
    mongo: MongoWriter, config_loader: ConfigLoader
) -> List[Dict]:
    """
    获取每个启用源的最近状态和连续失败数。

    对每个源取最近 20 条 crawl_runs 记录，从最新开始计算连续失败数。
    """
    enabled = config_loader.get_enabled_sources()
    col = mongo.get_collection("crawl_runs")

    statuses = []
    for source_name, config in enabled.items():
        # 取最近 20 条，按时间倒序
        runs = list(
            col.find(
                {"source_name": source_name},
                {"success": 1, "started_at": 1, "finished_at": 1,
                 "item_count": 1, "duration_seconds": 1, "error_message": 1},
            )
            .sort([("started_at", -1)])
            .limit(20)
        )

        consecutive_failures = 0
        for run in runs:
            if not run.get("success"):
                consecutive_failures += 1
            else:
                break

        last_run = runs[0] if runs else None

        statuses.append({
            "source_name": source_name,
            "display_name": config.get("display_name", source_name),
            "category": config.get("category", ""),
            "source_type": config.get("source_type", ""),
            "consecutive_failures": consecutive_failures,
            "last_success": last_run.get("success") if last_run else None,
            "last_started_at": last_run["started_at"].isoformat() if last_run and isinstance(last_run.get("started_at"), datetime) else str(last_run.get("started_at", "")) if last_run else None,
            "last_finished_at": last_run["finished_at"].isoformat() if last_run and isinstance(last_run.get("finished_at"), datetime) else str(last_run.get("finished_at", "")) if last_run else None,
            "last_item_count": last_run.get("item_count") if last_run else None,
            "last_duration": last_run.get("duration_seconds") if last_run else None,
            "last_error": last_run.get("error_message") if last_run else None,
            "total_runs": len(runs),
        })

    # 按连续失败数降序排列（问题源排前面）
    statuses.sort(key=lambda s: (-s["consecutive_failures"], s["source_name"]))
    return statuses


def get_collection_volumes(
    mongo: MongoWriter, hours: int = 48
) -> Dict[str, List[Dict]]:
    """
    获取各 MongoDB 集合按小时的文档数。

    返回 {collection_name: [{hour: "2024-01-01T12:00", count: 42}, ...]}
    """
    collections = ["aggregator", "hot_national", "hot_vertical", "media"]
    since_ts = int((datetime.now() - timedelta(hours=hours)).timestamp())
    result = {}

    for coll_name in collections:
        try:
            col = mongo.get_collection(coll_name)
            pipeline = [
                {"$match": {"first_seen_at": {"$gte": since_ts}}},
                # first_seen_at 是 Unix 秒级时间戳(int)，需转为 Date 才能用日期操作符
                {"$addFields": {"_dt": {"$toDate": {"$multiply": ["$first_seen_at", 1000]}}}},
                {
                    "$group": {
                        "_id": {
                            "year": {"$year": {"date": "$_dt", "timezone": "Asia/Shanghai"}},
                            "month": {"$month": {"date": "$_dt", "timezone": "Asia/Shanghai"}},
                            "day": {"$dayOfMonth": {"date": "$_dt", "timezone": "Asia/Shanghai"}},
                            "hour": {"$hour": {"date": "$_dt", "timezone": "Asia/Shanghai"}},
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
            result[coll_name] = hourly
        except Exception as e:
            logger.warning(f"[Admin] 聚合 {coll_name} 失败: {e}")
            result[coll_name] = []

    return result


def get_recent_runs(mongo: MongoWriter, limit: int = 100) -> List[Dict]:
    """获取最近 N 条执行记录"""
    col = mongo.get_collection("crawl_runs")
    docs = list(
        col.find({}, {"_id": 0})
        .sort([("started_at", -1)])
        .limit(limit)
    )
    # 序列化 datetime
    for doc in docs:
        for key in ("started_at", "finished_at"):
            if isinstance(doc.get(key), datetime):
                doc[key] = doc[key].isoformat()
    return docs


def get_source_history(
    mongo: MongoWriter, source_name: str, limit: int = 50
) -> List[Dict]:
    """获取单源执行历史"""
    col = mongo.get_collection("crawl_runs")
    docs = list(
        col.find({"source_name": source_name}, {"_id": 0})
        .sort([("started_at", -1)])
        .limit(limit)
    )
    for doc in docs:
        for key in ("started_at", "finished_at"):
            if isinstance(doc.get(key), datetime):
                doc[key] = doc[key].isoformat()
    return docs
