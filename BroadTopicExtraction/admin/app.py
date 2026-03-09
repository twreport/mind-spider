# -*- coding: utf-8 -*-
"""
Admin Dashboard — FastAPI 应用入口

启动方式:
    uv run python BroadTopicExtraction/admin/app.py
    uv run python BroadTopicExtraction/admin/app.py --port 8778
"""

import argparse
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
_PROJECT_ROOT = str(Path(__file__).parent.parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
_BROAD_ROOT = str(Path(__file__).parent.parent)
if _BROAD_ROOT not in sys.path:
    sys.path.insert(0, _BROAD_ROOT)

import uvicorn
from fastapi import FastAPI
from loguru import logger

from ms_config import settings
from pipeline.mongo_writer import MongoWriter
from pipeline.config_loader import ConfigLoader

from BroadTopicExtraction.admin import api
from BroadTopicExtraction.admin.metrics import ensure_indexes

app = FastAPI(title="MindSpider Admin Dashboard", docs_url=None, redoc_url=None)

# 注册路由
app.include_router(api.router)


@app.on_event("startup")
async def startup():
    """启动时连接 MongoDB 并创建索引"""
    mongo = MongoWriter()
    mongo.connect()
    config_loader = ConfigLoader()

    # 注入到 API 模块
    api.init(mongo, config_loader)

    # 创建索引
    ensure_indexes(mongo)

    enabled = config_loader.get_enabled_sources()
    logger.info(
        f"[Admin] Dashboard 已启动，监控 {len(enabled)} 个数据源，"
        f"端口 {settings.ADMIN_DASHBOARD_PORT}"
    )


def main():
    parser = argparse.ArgumentParser(description="MindSpider Admin Dashboard")
    parser.add_argument(
        "--port", type=int, default=settings.ADMIN_DASHBOARD_PORT,
        help=f"监听端口 (默认: {settings.ADMIN_DASHBOARD_PORT})",
    )
    parser.add_argument(
        "--host", type=str, default="0.0.0.0",
        help="监听地址 (默认: 0.0.0.0)",
    )
    args = parser.parse_args()

    logger.info(f"[Admin] 正在启动 Admin Dashboard http://{args.host}:{args.port}/")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
