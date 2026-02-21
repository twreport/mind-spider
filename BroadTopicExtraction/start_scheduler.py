#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MindSpider 数据采集调度器 - 一键启动脚本

用法:
    uv run python BroadTopicExtraction/start_scheduler.py
    uv run python BroadTopicExtraction/start_scheduler.py --categories hot_national hot_vertical
    uv run python BroadTopicExtraction/start_scheduler.py --once
    uv run python BroadTopicExtraction/start_scheduler.py --list
    uv run python BroadTopicExtraction/start_scheduler.py --log-level ERROR
"""

import sys
import asyncio
import signal
import argparse
from pathlib import Path
from datetime import datetime

from loguru import logger

# 路径设置
module_dir = Path(__file__).parent
project_root = module_dir.parent
sys.path.insert(0, str(module_dir))
sys.path.insert(0, str(project_root))

from ms_config import settings
from scheduler.scheduler import MindSpiderScheduler
from scheduler.runner import TaskRunner

# 日志配置
LOG_DIR = module_dir / "logs"
LOG_DIR.mkdir(exist_ok=True)


def setup_logging(level: str = "INFO"):
    """配置日志级别，level 控制终端输出，文件始终记录 DEBUG"""
    logger.remove()
    logger.add(sys.stderr, level=level.upper(), format="{time:HH:mm:ss} | {level:<7} | {message}")
    logger.add(
        LOG_DIR / "scheduler_{time:YYYY-MM-DD}.log",
        rotation="00:00",
        retention="30 days",
        level="DEBUG",
        encoding="utf-8",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:<7} | {message}",
    )

# MongoDB 配置 (从统一配置读取)
MONGO_URI = settings.MONGO_URI


async def start_scheduler(categories=None, mongo_uri=MONGO_URI):
    """启动调度器，持续运行"""
    logger.info("=" * 60)
    logger.info("MindSpider 数据采集调度器启动")
    logger.info(f"启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info("=" * 60)

    runner = TaskRunner(mongo_uri=mongo_uri)
    scheduler = MindSpiderScheduler()

    # 注册处理器
    scheduler.register_handler("aggregator", runner.run_aggregator)
    scheduler.register_handler("scrapy", lambda name, config: asyncio.get_event_loop().run_in_executor(
        None, runner.run_scrapy, name, config
    ))

    # 设置任务
    job_count = scheduler.setup_jobs(categories=categories)
    logger.info(f"已注册 {job_count} 个定时任务")

    if job_count == 0:
        logger.warning("没有可执行的任务，请检查配置")
        return

    # 启动调度器
    scheduler.start()

    # 显示任务列表
    for job in scheduler.get_jobs():
        logger.info(f"  {job['id']:<30} 下次执行: {job['next_run_time']}")

    # 优雅退出
    stop_event = asyncio.Event()

    def _signal_handler():
        logger.info("收到退出信号，正在关闭...")
        stop_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            # Windows 不支持 add_signal_handler
            pass

    logger.info("调度器运行中，按 Ctrl+C 停止")

    try:
        await stop_event.wait()
    except KeyboardInterrupt:
        pass
    finally:
        scheduler.shutdown()
        runner.close()
        logger.info("调度器已停止")


async def run_once(categories=None, mongo_uri=MONGO_URI):
    """所有任务立即执行一次（用于测试）"""
    logger.info("=" * 60)
    logger.info("MindSpider 单次执行模式")
    logger.info("=" * 60)

    runner = TaskRunner(mongo_uri=mongo_uri)
    scheduler = MindSpiderScheduler()

    scheduler.register_handler("aggregator", runner.run_aggregator)
    scheduler.register_handler("scrapy", lambda name, config: asyncio.get_event_loop().run_in_executor(
        None, runner.run_scrapy, name, config
    ))

    try:
        await scheduler.run_all_once(categories=categories)
    finally:
        runner.close()

    logger.info("单次执行完成")


def list_sources():
    """列出所有已启用的数据源"""
    from pipeline import ConfigLoader

    loader = ConfigLoader()
    sources = loader.get_enabled_sources()

    by_category = {}
    for name, config in sources.items():
        cat = config.get("category", "unknown")
        by_category.setdefault(cat, []).append((name, config))

    total = 0
    for cat in sorted(by_category.keys()):
        items = by_category[cat]
        print(f"\n[{cat}] ({len(items)} 个)")
        for name, config in items:
            source_type = config.get("source_type", "?")
            display = config.get("display_name", name)
            schedule = config.get("schedule", {})
            sched_str = f"{schedule.get('type', '?')}"
            if schedule.get("minutes"):
                sched_str += f" {schedule['minutes']}min"
            elif schedule.get("hour") is not None:
                sched_str += f" {schedule.get('hour', 0):02d}:{schedule.get('minute', 0):02d}"
            print(f"  {name:<35} {display:<20} [{source_type:<10}] {sched_str}")
        total += len(items)

    print(f"\n共 {total} 个启用的数据源")


def main():
    parser = argparse.ArgumentParser(description="MindSpider 数据采集调度器")
    parser.add_argument(
        "--categories", nargs="+",
        choices=["hot_national", "hot_local", "hot_vertical", "media", "wechat"],
        help="只运行指定分类的任务",
    )
    parser.add_argument("--once", action="store_true", help="所有任务执行一次后退出")
    parser.add_argument("--list", action="store_true", help="列出所有启用的数据源")
    parser.add_argument("--mongo-uri", default=MONGO_URI, help="MongoDB 连接 URI")
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="终端日志级别 (默认 INFO，文件日志始终为 DEBUG)",
    )

    args = parser.parse_args()

    setup_logging(args.log_level)
    mongo_uri = args.mongo_uri

    if args.list:
        list_sources()
        return

    if args.once:
        asyncio.run(run_once(categories=args.categories, mongo_uri=mongo_uri))
    else:
        asyncio.run(start_scheduler(categories=args.categories, mongo_uri=mongo_uri))


if __name__ == "__main__":
    main()
