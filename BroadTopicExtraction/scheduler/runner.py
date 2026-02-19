# -*- coding: utf-8 -*-
"""
任务执行器

负责执行 Scrapy 爬虫和聚合器任务
"""

import asyncio
import re
import subprocess
from typing import Dict, List, Optional, Any
from pathlib import Path
from loguru import logger

import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from pipeline import DataProcessor
from aggregators import get_aggregator


class TaskRunner:
    """任务执行器"""

    def __init__(self, mongo_uri: Optional[str] = None):
        """
        初始化任务执行器

        Args:
            mongo_uri: MongoDB 连接 URI
        """
        self.mongo_uri = mongo_uri
        self.processor: Optional[DataProcessor] = None
        self._scrapy_project_path = Path(__file__).parent.parent / "crawlers"

    def _get_processor(self) -> DataProcessor:
        """获取数据处理器"""
        if self.processor is None:
            self.processor = DataProcessor(mongo_uri=self.mongo_uri)
            self.processor.connect()
        return self.processor

    async def run_aggregator(self, source_name: str, config: Dict) -> Dict:
        """
        运行聚合器任务

        Args:
            source_name: 信源名称
            config: 信源配置

        Returns:
            执行结果统计
        """
        aggregator_name = config.get("aggregator_name")
        aggregator_source = config.get("aggregator_source")

        if not aggregator_name or not aggregator_source:
            logger.error(f"[{source_name}] 缺少聚合器配置")
            return {"success": False, "error": "缺少聚合器配置"}

        # 获取聚合器
        aggregator = get_aggregator(aggregator_name)
        if not aggregator:
            logger.error(f"[{source_name}] 未知聚合器: {aggregator_name}")
            return {"success": False, "error": f"未知聚合器: {aggregator_name}"}

        try:
            # 获取数据
            async with aggregator:
                result = await aggregator.fetch(aggregator_source)

            if not result.success:
                logger.error(f"[{source_name}] 获取数据失败: {result.error}")
                return {"success": False, "error": result.error}

            # 处理数据
            processor = self._get_processor()
            stats = processor.process_batch_optimized(result.items, source_name)

            logger.info(
                f"[{source_name}] 聚合器任务完成: "
                f"获取 {result.count} 条, "
                f"插入 {stats['inserted']}, "
                f"更新 {stats['updated']}, "
                f"跳过 {stats['skipped']}"
            )

            return {
                "success": True,
                "fetched": result.count,
                **stats,
            }

        except Exception as e:
            logger.error(f"[{source_name}] 聚合器任务失败: {e}")
            return {"success": False, "error": str(e)}

    def run_scrapy(self, source_name: str, config: Dict) -> Dict:
        """
        运行 Scrapy 爬虫任务

        Args:
            source_name: 信源名称
            config: 信源配置

        Returns:
            执行结果
        """
        spider_name = config.get("spider_name")
        if not spider_name:
            logger.error(f"[{source_name}] 缺少 spider_name 配置")
            return {"success": False, "error": "缺少 spider_name 配置"}

        try:
            # 构建 Scrapy 命令
            cmd = [
                sys.executable, "-m", "scrapy", "crawl", spider_name,
                "-s", f"SOURCE_NAME={source_name}",
            ]

            # 添加额外参数
            if config.get("region"):
                cmd.extend(["-a", f"region={config['region']}"])

            logger.info(f"[{source_name}] 启动 Scrapy 爬虫: {spider_name}")

            # 执行爬虫
            result = subprocess.run(
                cmd,
                cwd=str(self._scrapy_project_path),
                capture_output=True,
                text=True,
                timeout=300,  # 5 分钟超时
            )

            if result.returncode == 0:
                # 从 Scrapy 输出中提取爬取统计
                item_count = self._parse_scrapy_item_count(result.stderr)
                if item_count is not None:
                    if item_count == 0:
                        logger.warning(f"[{source_name}] Scrapy 爬虫完成但未爬取到数据 (item_scraped_count=0)")
                    else:
                        logger.info(f"[{source_name}] Scrapy 爬虫完成: 爬取 {item_count} 条")
                else:
                    logger.info(f"[{source_name}] Scrapy 爬虫完成 (未找到统计信息)")

                # 记录 Scrapy 警告/错误（即使 returncode=0 也可能有）
                if result.stderr:
                    for line in result.stderr.splitlines():
                        if "ERROR" in line:
                            logger.error(f"[{source_name}] {line.strip()}")
                        elif "WARNING" in line and "ScrapyDeprecationWarning" not in line and "Scrapy stats" not in line:
                            logger.warning(f"[{source_name}] {line.strip()}")

                return {"success": True, "item_count": item_count}
            else:
                # 失败时记录完整的 stderr 尾部
                stderr_tail = result.stderr[-2000:] if result.stderr else "(无输出)"
                logger.error(f"[{source_name}] Scrapy 爬虫失败 (returncode={result.returncode}):\n{stderr_tail}")
                return {"success": False, "error": result.stderr}

        except subprocess.TimeoutExpired:
            logger.error(f"[{source_name}] Scrapy 爬虫超时")
            return {"success": False, "error": "执行超时"}
        except Exception as e:
            logger.error(f"[{source_name}] Scrapy 爬虫异常: {e}")
            return {"success": False, "error": str(e)}

    def _parse_scrapy_item_count(self, stderr: str) -> Optional[int]:
        """从 Scrapy 的 stats dump 中提取 item_scraped_count"""
        if not stderr:
            return None
        match = re.search(r"'item_scraped_count':\s*(\d+)", stderr)
        if match:
            return int(match.group(1))
        # Scrapy 不输出 item_scraped_count 时表示 0 条
        if "Dumping Scrapy stats" in stderr:
            return 0
        return None

    async def run_task(self, source_name: str, config: Dict) -> Dict:
        """
        运行任务 (自动判断类型)

        Args:
            source_name: 信源名称
            config: 信源配置

        Returns:
            执行结果
        """
        source_type = config.get("source_type", "scrapy")

        if source_type == "aggregator":
            return await self.run_aggregator(source_name, config)
        else:
            # Scrapy 是同步的，在线程池中运行
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None, self.run_scrapy, source_name, config
            )

    def close(self) -> None:
        """关闭资源"""
        if self.processor:
            self.processor.close()
            self.processor = None


async def create_default_handlers(runner: TaskRunner) -> Dict[str, Any]:
    """
    创建默认的任务处理器

    Args:
        runner: TaskRunner 实例

    Returns:
        处理器字典
    """
    return {
        "aggregator": runner.run_aggregator,
        "scrapy": lambda name, config: asyncio.get_event_loop().run_in_executor(
            None, runner.run_scrapy, name, config
        ),
    }
