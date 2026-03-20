# -*- coding: utf-8 -*-
"""
指标查询模块

从 MongoDB crawl_tasks / platform_cookies 聚合深层采集指标。
从 MySQL fish 库查询爬取结果内容数据。
"""

import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from urllib.parse import quote_plus

from loguru import logger
from sqlalchemy import create_engine, text

from ms_config import settings

# --- MySQL fish 库连接 ---

_fish_engine = None


def _get_fish_engine():
    global _fish_engine
    if _fish_engine is None:
        _fish_engine = create_engine(
            f"mysql+pymysql://{settings.DB_USER}:{quote_plus(settings.DB_PASSWORD)}"
            f"@{settings.DB_HOST}:{settings.DB_PORT}/fish?charset={settings.DB_CHARSET}",
            pool_recycle=3600,
            pool_pre_ping=True,
        )
    return _fish_engine


# 平台表映射：content=内容表, comment=评论表, id_col=内容ID列
PLATFORM_TABLES = {
    "xhs": {"content": "xhs_note", "comment": "xhs_note_comment", "id_col": "note_id"},
    "dy": {"content": "douyin_aweme", "comment": "douyin_aweme_comment", "id_col": "aweme_id"},
    "ks": {"content": "kuaishou_video", "comment": "kuaishou_video_comment", "id_col": "video_id"},
    "bili": {
        "content": "bilibili_video",
        "comment": "bilibili_video_comment",
        "id_col": "video_id",
    },
    "wb": {"content": "weibo_note", "comment": "weibo_note_comment", "id_col": "note_id"},
    "zhihu": {"content": "zhihu_content", "comment": "zhihu_comment", "id_col": "content_id"},
}

# 各平台字段映射 → 统一为 nickname, title, liked_count, comment_count, share_count, create_time
PLATFORM_FIELD_MAP = {
    "xhs": {
        "nickname": "nickname",
        "title": "title",
        "desc": "desc",
        "liked": "liked_count",
        "comment": "comment_count",
        "share": "share_count",
        "time": "time",
    },
    "dy": {
        "nickname": "nickname",
        "title": "title",
        "desc": "desc",
        "liked": "liked_count",
        "comment": "comment_count",
        "share": "share_count",
        "time": "create_time",
    },
    "ks": {
        "nickname": "nickname",
        "title": "title",
        "desc": "desc",
        "liked": "liked_count",
        "comment": None,
        "share": None,
        "time": "create_time",
    },
    "bili": {
        "nickname": "nickname",
        "title": "title",
        "desc": "desc",
        "liked": "liked_count",
        "comment": "video_comment",
        "share": "video_share_count",
        "time": "create_time",
    },
    "wb": {
        "nickname": "nickname",
        "title": None,
        "desc": "content",
        "liked": "liked_count",
        "comment": "comments_count",
        "share": "shared_count",
        "time": "create_time",
    },
    "zhihu": {
        "nickname": "user_nickname",
        "title": "title",
        "desc": "content_text",
        "liked": "voteup_count",
        "comment": "comment_count",
        "share": None,
        "time": "created_time",
    },
}


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
    7 平台健康看板：cookie 状态、熔断器、最近任务、成功率、综合健康度。
    """
    from DeepSentimentCrawling.dispatcher import ALL_PLATFORMS

    mongo.connect()
    col = mongo.get_collection("crawl_tasks")
    now = int(time.time())
    h24_ago = now - 86400

    # cookie 状态（cookie 池聚合）
    cookie_statuses = {}  # platform -> list of cookie entries
    if cookie_manager:
        for s in cookie_manager.get_all_status():
            plat = s["platform"]
            if plat not in cookie_statuses:
                cookie_statuses[plat] = []
            cookie_statuses[plat].append(s)

    # 熔断器事件集合
    circuit_col = mongo.get_collection("circuit_events")

    result = []
    for plat in ALL_PLATFORMS:
        # cookie 信息（cookie 池聚合）
        entries = cookie_statuses.get(plat, [])
        active_count = sum(1 for e in entries if e.get("status") == "active")
        total_count = len([e for e in entries if e.get("status") != "missing"])
        # 综合 cookie 状态：有 active 则 active，全部过期则 expired，无记录则 missing
        if active_count > 0:
            cookie_status = "active"
        elif total_count > 0:
            cookie_status = "expired"
        else:
            cookie_status = "missing"
        # 最新 saved_at
        cookie_saved_at = max(
            (e.get("saved_at") for e in entries if e.get("saved_at")),
            default=None,
        )

        # 熔断器状态（内存）
        circuit = "closed"
        if dispatcher:
            circuit = "open" if dispatcher.circuit_open.get(plat, False) else "closed"

        # 最近熔断事件（MongoDB 持久化）
        last_circuit_event = None
        try:
            evt = circuit_col.find_one(
                {"platform": plat},
                sort=[("timestamp", -1)],
            )
            if evt:
                last_circuit_event = {
                    "event": evt.get("event"),
                    "reason": evt.get("reason", ""),
                    "timestamp": evt.get("timestamp"),
                }
        except Exception:
            pass

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

        # 检测最近 3 个 completed 任务是否全部 0 结果
        all_recent_zero = False
        if completed_24h > 0:
            recent_completed = list(
                col.find(
                    {"platform": plat, "status": "completed"},
                    {"total_crawled": 1, "_id": 0},
                    sort=[("completed_at", -1)],
                    limit=3,
                )
            )
            if recent_completed and all(
                doc.get("total_crawled", 0) == 0 for doc in recent_completed
            ):
                all_recent_zero = True

        # 综合健康度判定
        health = "unknown"
        health_reason = ""
        if cookie_status == "missing":
            health = "unknown"
        elif cookie_status == "expired" or circuit == "open":
            health = "unhealthy"
            health_reason = "cookie 过期" if cookie_status == "expired" else "熔断器开启"
        elif (
            last_circuit_event
            and last_circuit_event["event"] == "open"
            and (now - last_circuit_event["timestamp"]) < 3600
        ):
            health = "degraded"
            health_reason = "近 1 小时内熔断过"
        elif completed_24h > 0 and all_recent_zero:
            health = "degraded"
            health_reason = "近期爬取 0 结果"
        else:
            health = "healthy"

        result.append(
            {
                "platform": plat,
                "cookie_status": cookie_status,
                "cookie_saved_at": cookie_saved_at,
                "active_cookie_count": active_count,
                "total_cookie_count": total_count,
                "circuit_breaker": circuit,
                "last_circuit_event": last_circuit_event,
                "health": health,
                "health_reason": health_reason,
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


def get_top_candidates(
    mongo, limit: int = 10, offset: int = 0, sort_key: str = "max_score", sort_order: str = "desc"
) -> Dict:
    """
    24 小时内热度最高的候选话题。

    从 candidates 集合查询近 24h 有更新的候选，
    计算其 snapshots 中的最高 score_pos，按指定字段排序后取 top N。
    """
    mongo.connect()
    col = mongo.get_collection("candidates")
    h24_ago = int(time.time()) - 86400

    docs = list(
        col.find(
            {"updated_at": {"$gte": h24_ago}, "status": {"$nin": ["closed", "faded"]}},
            {
                "candidate_id": 1,
                "canonical_title": 1,
                "status": 1,
                "snapshots": 1,
                "status_history": 1,
                "first_seen_at": 1,
                "updated_at": 1,
                "platform_count": 1,
            },
        )
    )

    candidates = []
    for doc in docs:
        snaps = doc.get("snapshots", [])
        if not snaps:
            continue
        max_score = max(s.get("score_pos", 0) for s in snaps)
        cur_score = snaps[-1].get("score_pos", 0)

        # 从 status_history 中找第一个 confirmed 的时间作为 triggered_at
        triggered_at = None
        for h in doc.get("status_history", []):
            if h.get("status") == "confirmed":
                triggered_at = h.get("ts")
                break

        candidates.append(
            {
                "candidate_id": doc.get("candidate_id", ""),
                "title": doc.get("canonical_title", ""),
                "status": doc.get("status", ""),
                "max_score": max_score,
                "current_score": cur_score,
                "platform_count": doc.get("platform_count", 0),
                "first_seen_at": doc.get("first_seen_at"),
                "updated_at": doc.get("updated_at"),
                "triggered_at": triggered_at,
                "triggered": max_score >= 10000,
                "confirmed": max_score >= 4000,
            }
        )

    # 允许排序的字段白名单
    allowed_keys = {"max_score", "current_score", "platform_count", "triggered_at", "first_seen_at"}
    if sort_key not in allowed_keys:
        sort_key = "max_score"
    reverse = sort_order != "asc"

    def _sort_val(c):
        v = c.get(sort_key)
        # None 始终排最后
        if v is None:
            return (1, 0)
        return (0, v)

    candidates.sort(key=_sort_val, reverse=reverse)
    total = len(candidates)
    return {"total": total, "items": candidates[offset : offset + limit]}


def get_candidate_detail(mongo, candidate_id: str) -> Optional[Dict]:
    """
    获取单个候选的完整快照和状态历史（用于绘制热度曲线）。
    """
    mongo.connect()
    col = mongo.get_collection("candidates")
    doc = col.find_one(
        {"candidate_id": candidate_id},
        {
            "_id": 0,
            "candidate_id": 1,
            "canonical_title": 1,
            "status": 1,
            "snapshots": 1,
            "status_history": 1,
            "first_seen_at": 1,
        },
    )
    if not doc:
        return None

    # 去重 status_history：只保留状态变化的节点
    raw_history = doc.get("status_history", [])
    transitions = []
    prev_status = None
    for h in raw_history:
        if h.get("status") != prev_status:
            transitions.append(
                {
                    "ts": h["ts"],
                    "status": h["status"],
                    "reason": h.get("reason", ""),
                }
            )
            prev_status = h["status"]

    return {
        "candidate_id": doc.get("candidate_id"),
        "title": doc.get("canonical_title", ""),
        "status": doc.get("status", ""),
        "snapshots": doc.get("snapshots", []),
        "transitions": transitions,
    }


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
                            "year": {
                                "$year": {"date": "$completed_dt", "timezone": "Asia/Shanghai"}
                            },
                            "month": {
                                "$month": {"date": "$completed_dt", "timezone": "Asia/Shanghai"}
                            },
                            "day": {
                                "$dayOfMonth": {
                                    "date": "$completed_dt",
                                    "timezone": "Asia/Shanghai",
                                }
                            },
                            "hour": {
                                "$hour": {"date": "$completed_dt", "timezone": "Asia/Shanghai"}
                            },
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


# --- MySQL fish 库查询 ---


def get_crawl_results(limit: int = 20) -> List[Dict]:
    """
    爬取结果总览 — 按话题聚合各平台内容数 + 评论数。

    从 crawling_tasks 按 topic_id 分组，取最近 N 个话题，
    再统计每个话题在各平台的内容数和评论数。
    """
    try:
        engine = _get_fish_engine()
        with engine.connect() as conn:
            # 1. 查最近 N 个不同 topic_id 及其话题名
            topic_rows = conn.execute(
                text(
                    "SELECT topic_id, config_params, MAX(scheduled_date) AS last_date "
                    "FROM crawling_tasks "
                    "WHERE topic_id IS NOT NULL AND topic_id != '' "
                    "GROUP BY topic_id, config_params "
                    "ORDER BY last_date DESC "
                    "LIMIT :limit"
                ),
                {"limit": limit},
            ).fetchall()

            if not topic_rows:
                return []

            # 去重 topic_id（config_params 可能不同但 topic_id 相同）
            seen = set()
            topics = []
            for row in topic_rows:
                tid = row[0]
                if tid in seen:
                    continue
                seen.add(tid)
                # 从 config_params JSON 提取 topic_title
                topic_name = tid
                try:
                    params = json.loads(row[1]) if row[1] else {}
                    topic_name = params.get("topic_title", tid)
                except (json.JSONDecodeError, TypeError):
                    pass
                topics.append({"topic_id": tid, "topic_name": topic_name, "last_date": str(row[2])})

            # 2. 对每个话题，查各平台内容数和评论数
            results = []
            for topic in topics:
                tid = topic["topic_id"]
                # 获取该话题的所有 task_id
                task_rows = conn.execute(
                    text("SELECT task_id FROM crawling_tasks WHERE topic_id = :tid"),
                    {"tid": tid},
                ).fetchall()
                task_ids = [r[0] for r in task_rows]
                if not task_ids:
                    continue

                placeholders = ",".join([f":t{i}" for i in range(len(task_ids))])
                params = {f"t{i}": v for i, v in enumerate(task_ids)}

                platform_counts = {}
                total_content = 0
                total_comments = 0

                for plat, tbl in PLATFORM_TABLES.items():
                    # 内容数
                    content_sql = (
                        f"SELECT COUNT(*) FROM {tbl['content']} "
                        f"WHERE crawling_task_id IN ({placeholders})"
                    )
                    cnt = conn.execute(text(content_sql), params).scalar() or 0

                    # 评论数
                    comment_sql = (
                        f"SELECT COUNT(*) FROM {tbl['comment']} c "
                        f"INNER JOIN {tbl['content']} p ON c.{tbl['id_col']} = p.{tbl['id_col']} "
                        f"WHERE p.crawling_task_id IN ({placeholders})"
                    )
                    cmt = conn.execute(text(comment_sql), params).scalar() or 0

                    platform_counts[plat] = {"content": cnt, "comments": cmt}
                    total_content += cnt
                    total_comments += cmt

                results.append(
                    {
                        "topic_id": tid,
                        "topic_name": topic["topic_name"],
                        "last_date": topic["last_date"],
                        "platforms": platform_counts,
                        "total_content": total_content,
                        "total_comments": total_comments,
                    }
                )

            return results
    except Exception as e:
        logger.error(f"[DeepDashboard] 查询爬取结果失败: {e}")
        return []


def get_topic_contents(topic_id: str) -> Dict:
    """
    话题内容明细 — 查某话题下所有平台的具体内容。

    返回 {platform: [{nickname, title, liked, comments, shares, pub_time}, ...]}
    """
    try:
        engine = _get_fish_engine()
        with engine.connect() as conn:
            # 获取该话题的所有 task_id
            task_rows = conn.execute(
                text("SELECT task_id FROM crawling_tasks WHERE topic_id = :tid"),
                {"tid": topic_id},
            ).fetchall()
            task_ids = [r[0] for r in task_rows]
            if not task_ids:
                return {}

            placeholders = ",".join([f":t{i}" for i in range(len(task_ids))])
            params = {f"t{i}": v for i, v in enumerate(task_ids)}

            result = {}
            for plat, tbl in PLATFORM_TABLES.items():
                fm = PLATFORM_FIELD_MAP[plat]
                # 构建 SELECT 列
                cols = ["crawling_task_id"]
                if fm["nickname"]:
                    cols.append(f"{fm['nickname']} AS nickname")
                else:
                    cols.append("'' AS nickname")
                if fm["title"]:
                    cols.append(f"{fm['title']} AS title")
                else:
                    cols.append("'' AS title")
                if fm["desc"]:
                    cols.append(f"`{fm['desc']}` AS `desc`")
                else:
                    cols.append("'' AS `desc`")
                if fm["liked"]:
                    cols.append(f"{fm['liked']} AS liked")
                else:
                    cols.append("'0' AS liked")
                if fm["comment"]:
                    cols.append(f"{fm['comment']} AS comments")
                else:
                    cols.append("'0' AS comments")
                if fm["share"]:
                    cols.append(f"{fm['share']} AS shares")
                else:
                    cols.append("'0' AS shares")
                if fm["time"]:
                    cols.append(f"{fm['time']} AS pub_time")
                else:
                    cols.append("'' AS pub_time")

                select_str = ", ".join(cols)
                sql = (
                    f"SELECT {select_str} FROM {tbl['content']} "
                    f"WHERE crawling_task_id IN ({placeholders}) "
                    f"ORDER BY id DESC LIMIT 200"
                )
                rows = conn.execute(text(sql), params).fetchall()
                if not rows:
                    continue

                items = []
                for row in rows:
                    # 标题：优先 title，fallback desc 截断
                    title_val = row[2] or ""
                    desc_val = row[3] or ""
                    display_title = (
                        title_val
                        if title_val
                        else (desc_val[:80] + "..." if len(desc_val) > 80 else desc_val)
                    )

                    items.append(
                        {
                            "nickname": row[1] or "",
                            "title": display_title,
                            "liked": str(row[4] or 0),
                            "comments": str(row[5] or 0),
                            "shares": str(row[6] or 0),
                            "pub_time": str(row[7] or ""),
                        }
                    )
                result[plat] = items

            return result
    except Exception as e:
        logger.error(f"[DeepDashboard] 查询话题内容失败: {e}")
        return {}
