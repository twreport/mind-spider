# -*- coding: utf-8 -*-
"""
MindSpider 调度器

基于 APScheduler 实现定时任务调度
"""

import asyncio
from typing import Dict, List, Optional, Callable, Any
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.events import EVENT_JOB_EXECUTED, EVENT_JOB_ERROR, JobExecutionEvent
from loguru import logger

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from pipeline import ConfigLoader


class MindSpiderScheduler:
    """MindSpider 调度器"""

    def __init__(
        self,
        config_dir: Optional[str] = None,
        use_async: bool = True,
    ):
        """
        初始化调度器

        Args:
            config_dir: 配置文件目录
            use_async: 是否使用异步调度器
        """
        self.config_loader = ConfigLoader(config_dir)
        self.use_async = use_async

        if use_async:
            self.scheduler = AsyncIOScheduler()
        else:
            self.scheduler = BackgroundScheduler()

        self._job_handlers: Dict[str, Callable] = {}
        self._setup_listeners()

    def _setup_listeners(self) -> None:
        """设置事件监听器"""
        self.scheduler.add_listener(
            self._on_job_executed,
            EVENT_JOB_EXECUTED | EVENT_JOB_ERROR,
        )

    def _on_job_executed(self, event: JobExecutionEvent) -> None:
        """任务执行完成回调"""
        job_id = event.job_id
        if event.exception:
            logger.error(f"[Scheduler] 任务 {job_id} 执行失败: {event.exception}")
        else:
            logger.info(f"[Scheduler] 任务 {job_id} 执行完成")

    def register_handler(self, source_type: str, handler: Callable) -> None:
        """
        注册任务处理器

        Args:
            source_type: 数据源类型 (scrapy, aggregator)
            handler: 处理函数
        """
        self._job_handlers[source_type] = handler
        logger.debug(f"注册处理器: {source_type}")

    def setup_jobs(self, categories: Optional[List[str]] = None) -> int:
        """
        根据 YAML 配置设置所有任务

        Args:
            categories: 要设置的分类列表，None 表示所有

        Returns:
            设置的任务数量
        """
        sources = self.config_loader.get_enabled_sources()
        job_count = 0

        for source_name, config in sources.items():
            # 过滤分类
            if categories and config.get("category") not in categories:
                continue

            # 获取调度配置
            schedule = config.get("schedule", {})
            if not schedule:
                logger.warning(f"[{source_name}] 缺少调度配置，跳过")
                continue

            # 创建触发器
            trigger = self._create_trigger(schedule)
            if not trigger:
                logger.warning(f"[{source_name}] 无效的调度配置: {schedule}")
                continue

            # 添加任务
            self.scheduler.add_job(
                self._run_source,
                trigger,
                args=[source_name],
                id=source_name,
                name=config.get("display_name", source_name),
                replace_existing=True,
            )
            job_count += 1
            logger.info(
                f"[Scheduler] 添加任务: {source_name} "
                f"({config.get('display_name', '')}) - {schedule}"
            )

        return job_count

    def _create_trigger(self, schedule: Dict) -> Optional[IntervalTrigger | CronTrigger]:
        """
        根据配置创建触发器

        Args:
            schedule: 调度配置

        Returns:
            触发器对象
        """
        schedule_type = schedule.get("type", "interval")

        if schedule_type == "interval":
            minutes = schedule.get("minutes", 30)
            return IntervalTrigger(minutes=minutes)
        elif schedule_type == "cron":
            return CronTrigger(
                hour=schedule.get("hour", 0),
                minute=schedule.get("minute", 0),
                second=schedule.get("second", 0),
            )
        else:
            return None

    async def _run_source(self, source_name: str) -> None:
        """
        运行指定数据源的采集任务

        Args:
            source_name: 数据源名称
        """
        config = self.config_loader.get_source(source_name)
        if not config:
            logger.error(f"[Scheduler] 未找到配置: {source_name}")
            return

        source_type = config.get("source_type", "scrapy")
        handler = self._job_handlers.get(source_type)

        if not handler:
            logger.error(f"[Scheduler] 未注册处理器: {source_type}")
            return

        logger.info(f"[Scheduler] 开始执行: {source_name}")
        start_time = datetime.now()

        try:
            if asyncio.iscoroutinefunction(handler):
                await handler(source_name, config)
            else:
                handler(source_name, config)

            elapsed = (datetime.now() - start_time).total_seconds()
            logger.info(f"[Scheduler] {source_name} 完成，耗时 {elapsed:.2f}s")

        except Exception as e:
            logger.error(f"[Scheduler] {source_name} 执行失败: {e}")
            raise

    def add_job(
        self,
        func: Callable,
        source_name: str,
        schedule: Dict,
        **kwargs: Any,
    ) -> None:
        """
        手动添加任务

        Args:
            func: 任务函数
            source_name: 任务 ID
            schedule: 调度配置
            **kwargs: 其他参数
        """
        trigger = self._create_trigger(schedule)
        if trigger:
            self.scheduler.add_job(
                func,
                trigger,
                id=source_name,
                replace_existing=True,
                **kwargs,
            )

    def remove_job(self, source_name: str) -> None:
        """移除任务"""
        try:
            self.scheduler.remove_job(source_name)
            logger.info(f"[Scheduler] 移除任务: {source_name}")
        except Exception as e:
            logger.warning(f"[Scheduler] 移除任务失败: {e}")

    def pause_job(self, source_name: str) -> None:
        """暂停任务"""
        self.scheduler.pause_job(source_name)
        logger.info(f"[Scheduler] 暂停任务: {source_name}")

    def resume_job(self, source_name: str) -> None:
        """恢复任务"""
        self.scheduler.resume_job(source_name)
        logger.info(f"[Scheduler] 恢复任务: {source_name}")

    def get_jobs(self) -> List[Dict]:
        """获取所有任务信息"""
        jobs = []
        for job in self.scheduler.get_jobs():
            jobs.append({
                "id": job.id,
                "name": job.name,
                "next_run_time": str(job.next_run_time) if job.next_run_time else None,
                "trigger": str(job.trigger),
            })
        return jobs

    def start(self) -> None:
        """启动调度器"""
        if not self.scheduler.running:
            self.scheduler.start()
            logger.info("[Scheduler] 调度器已启动")

    def shutdown(self, wait: bool = True) -> None:
        """关闭调度器"""
        if self.scheduler.running:
            self.scheduler.shutdown(wait=wait)
            logger.info("[Scheduler] 调度器已关闭")

    async def run_once(self, source_name: str) -> None:
        """立即执行一次指定任务"""
        await self._run_source(source_name)

    async def run_all_once(self, categories: Optional[List[str]] = None) -> None:
        """立即执行所有任务一次"""
        sources = self.config_loader.get_enabled_sources()

        for source_name, config in sources.items():
            if categories and config.get("category") not in categories:
                continue

            try:
                await self._run_source(source_name)
            except Exception as e:
                logger.error(f"[Scheduler] {source_name} 执行失败: {e}")
