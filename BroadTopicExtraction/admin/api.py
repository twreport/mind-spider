# -*- coding: utf-8 -*-
"""
API 路由

提供健康状态、数据趋势、错误日志等 API 端点。
"""

import json

from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from ms_config import settings

from .templates import get_dashboard_html
from . import metrics as m
from . import log_reader

router = APIRouter()

# 由 app.py 注入
_mongo = None
_config_loader = None


def init(mongo, config_loader):
    """注入共享实例"""
    global _mongo, _config_loader
    _mongo = mongo
    _config_loader = config_loader


def _check_token(token: str):
    """校验访问令牌"""
    expected = settings.ADMIN_DASHBOARD_TOKEN
    if not expected:
        return
    if token != expected:
        raise HTTPException(status_code=403, detail="Invalid token")


@router.get("/", response_class=HTMLResponse)
async def dashboard(token: str = Query("")):
    """主面板页面"""
    _check_token(token)
    return HTMLResponse(get_dashboard_html(token))


@router.get("/api/status")
async def api_status(token: str = Query("")):
    """所有源的健康状态"""
    _check_token(token)
    if not _mongo or not _config_loader:
        raise HTTPException(status_code=500, detail="服务未初始化")
    data = m.get_source_statuses(_mongo, _config_loader)
    content = json.loads(json.dumps(data, ensure_ascii=False, default=str))
    return JSONResponse(content)


@router.get("/api/volumes")
async def api_volumes(token: str = Query(""), hours: int = Query(48, ge=1, le=168)):
    """各集合按小时文档数"""
    _check_token(token)
    if not _mongo:
        raise HTTPException(status_code=500, detail="服务未初始化")
    data = m.get_collection_volumes(_mongo, hours)
    return JSONResponse(data)


@router.get("/api/errors")
async def api_errors(
    token: str = Query(""),
    source: str = Query(""),
    limit: int = Query(50, ge=1, le=200),
):
    """错误日志"""
    _check_token(token)
    data = log_reader.get_error_logs(source=source or None, limit=limit)
    return JSONResponse(data)


@router.get("/api/source/{name}")
async def api_source_detail(name: str, token: str = Query("")):
    """单源详细执行历史"""
    _check_token(token)
    if not _mongo:
        raise HTTPException(status_code=500, detail="服务未初始化")
    data = m.get_source_history(_mongo, name)
    content = json.loads(json.dumps(data, ensure_ascii=False, default=str))
    return JSONResponse(content)
