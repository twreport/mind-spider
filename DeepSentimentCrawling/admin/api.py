# -*- coding: utf-8 -*-
"""
深层采集监控面板 — API 路由

所有端点挂载在 /dashboard 前缀下，与 LoginConsole 路由互不冲突。
"""

import json
from typing import Optional

from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from loguru import logger

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from ms_config import settings

from . import metrics as m
from . import log_reader
from .templates import get_dashboard_html

router = APIRouter(prefix="/dashboard")

# 由 start_deep_crawl.py 注入
_mongo = None
_cookie_manager = None
_dispatcher = None


def init(mongo, cookie_manager=None, dispatcher=None):
    """注入共享实例"""
    global _mongo, _cookie_manager, _dispatcher
    _mongo = mongo
    _cookie_manager = cookie_manager
    _dispatcher = dispatcher


def _check_token(token: str):
    """校验访问令牌（复用 LOGIN_CONSOLE_TOKEN）"""
    expected = settings.LOGIN_CONSOLE_TOKEN
    if not expected:
        return
    if token != expected:
        raise HTTPException(status_code=403, detail="Invalid token")


@router.get("/", response_class=HTMLResponse)
async def dashboard(token: str = Query("")):
    """主面板页面（不校验 token，前端 JS 调 API 时校验）"""
    return HTMLResponse(get_dashboard_html(token))


@router.get("/api/overview")
async def api_overview(token: str = Query("")):
    """总览统计"""
    _check_token(token)
    if not _mongo:
        raise HTTPException(status_code=500, detail="服务未初始化")
    data = m.get_overview(_mongo, _dispatcher)
    return JSONResponse(data)


@router.get("/api/platforms")
async def api_platforms(token: str = Query("")):
    """7 平台健康看板"""
    _check_token(token)
    if not _mongo:
        raise HTTPException(status_code=500, detail="服务未初始化")
    data = m.get_platform_health(_mongo, _cookie_manager, _dispatcher)
    content = json.loads(json.dumps(data, ensure_ascii=False, default=str))
    return JSONResponse(content)


@router.get("/api/tasks")
async def api_tasks(
    token: str = Query(""),
    platform: str = Query(""),
    status: str = Query(""),
    limit: int = Query(30, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """任务列表（分页、可按平台/状态筛选）"""
    _check_token(token)
    if not _mongo:
        raise HTTPException(status_code=500, detail="服务未初始化")
    data = m.get_task_list(
        _mongo,
        platform=platform or None,
        status=status or None,
        limit=limit,
        offset=offset,
    )
    content = json.loads(json.dumps(data, ensure_ascii=False, default=str))
    return JSONResponse(content)


@router.get("/api/volumes")
async def api_volumes(
    token: str = Query(""),
    hours: int = Query(48, ge=1, le=168),
):
    """各平台数据产量趋势"""
    _check_token(token)
    if not _mongo:
        raise HTTPException(status_code=500, detail="服务未初始化")
    data = m.get_volume_trend(_mongo, hours)
    return JSONResponse(data)


@router.get("/api/errors")
async def api_errors(
    token: str = Query(""),
    platform: str = Query(""),
    limit: int = Query(50, ge=1, le=200),
):
    """ERROR 级别日志"""
    _check_token(token)
    data = log_reader.get_error_logs(platform=platform or None, limit=limit)
    return JSONResponse(data)
