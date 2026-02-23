# -*- coding: utf-8 -*-
"""
深层采集服务入口

启动 TaskDispatcher + LoginConsole，24/7 运行。
从 MongoDB crawl_tasks 轮询待执行任务，自动触发 MediaCrawler 爬取。
"""

import argparse
import asyncio
import signal
import threading

import uvicorn
from loguru import logger

import sys
from pathlib import Path

# sys.path: MC_ROOT 需要在路径中（MC 内部依赖需要），项目根也需要
_PROJECT_ROOT = str(Path(__file__).parent.parent)
_MC_ROOT = str(Path(__file__).parent / "MediaCrawler")
if _MC_ROOT not in sys.path:
    sys.path.insert(0, _MC_ROOT)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from ms_config import settings
from DeepSentimentCrawling.cookie_manager import CookieManager
from DeepSentimentCrawling.dispatcher import TaskDispatcher
from DeepSentimentCrawling.login_console import app as login_app, init_cookie_manager, init_mongo_writer, cleanup as console_cleanup


def parse_args():
    parser = argparse.ArgumentParser(description="MindSpider 深层采集服务")
    parser.add_argument(
        "--port", type=int, default=None,
        help=f"登录控制台端口 (默认: {settings.LOGIN_CONSOLE_PORT})"
    )
    parser.add_argument(
        "--platforms", type=str, default=None,
        help="限制平台（逗号分隔），例如: xhs,dy,bili (默认: 全部7平台)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="试运行模式：只打印任务，不实际执行爬取"
    )
    return parser.parse_args()


def start_login_console(port: int):
    """在后台线程中启动 FastAPI 登录控制台"""
    config = uvicorn.Config(
        login_app,
        host="0.0.0.0",
        port=port,
        log_level="warning",
    )
    server = uvicorn.Server(config)
    server.run()


async def main():
    args = parse_args()

    port = args.port or settings.LOGIN_CONSOLE_PORT
    platforms = args.platforms.split(",") if args.platforms else None

    # 初始化组件
    cookie_manager = CookieManager()
    cookie_manager.ensure_indexes()

    init_cookie_manager(cookie_manager)

    dispatcher = TaskDispatcher(
        platforms=platforms,
        cookie_manager=cookie_manager,
        dry_run=args.dry_run,
    )

    init_mongo_writer(dispatcher.mongo)

    # 启动登录控制台（后台线程）
    console_thread = threading.Thread(
        target=start_login_console,
        args=(port,),
        daemon=True,
    )
    console_thread.start()
    logger.info(f"[DeepCrawl] 登录控制台已启动: http://0.0.0.0:{port}")

    # 优雅退出处理
    loop = asyncio.get_event_loop()
    stop_event = asyncio.Event()

    def _signal_handler():
        logger.info("[DeepCrawl] 收到退出信号，正在停止...")
        dispatcher.stop()
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows 不支持 add_signal_handler
            signal.signal(sig, lambda s, f: _signal_handler())

    # 打印启动信息
    plat_str = ", ".join(platforms) if platforms else "all (7)"
    logger.info(
        f"[DeepCrawl] 深层采集服务已启动\n"
        f"  平台: {plat_str}\n"
        f"  轮询间隔: {dispatcher.POLL_INTERVAL}s\n"
        f"  登录控制台: http://0.0.0.0:{port}\n"
        f"  dry_run: {args.dry_run}"
    )

    # 启动调度器主循环
    try:
        await dispatcher.run()
    except asyncio.CancelledError:
        pass
    finally:
        await console_cleanup()
        logger.info("[DeepCrawl] 服务已停止")


if __name__ == "__main__":
    asyncio.run(main())
