# -*- coding: utf-8 -*-
"""
Scheduler 模块单元测试

测试 scheduler/ 下的任务执行器和调度器
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from pathlib import Path


class TestTaskRunner:
    """测试任务执行器"""

    def test_init(self):
        """测试初始化"""
        with patch("BroadTopicExtraction.scheduler.runner.DataProcessor"):
            from BroadTopicExtraction.scheduler.runner import TaskRunner

            runner = TaskRunner(mongo_uri="mongodb://test:27017")

            assert runner.mongo_uri == "mongodb://test:27017"
            assert runner.processor is None

    def test_get_processor_lazy(self):
        """测试延迟初始化处理器"""
        with patch("BroadTopicExtraction.scheduler.runner.DataProcessor") as MockProcessor:
            mock_processor = MagicMock()
            MockProcessor.return_value = mock_processor

            from BroadTopicExtraction.scheduler.runner import TaskRunner

            runner = TaskRunner(mongo_uri="mongodb://test:27017")

            # 第一次调用应该创建处理器
            processor1 = runner._get_processor()
            assert processor1 is mock_processor
            mock_processor.connect.assert_called_once()

            # 第二次调用应该返回同一个处理器
            processor2 = runner._get_processor()
            assert processor2 is processor1

    @pytest.mark.asyncio
    async def test_run_aggregator_missing_config(self):
        """测试聚合器任务缺少配置"""
        with patch("BroadTopicExtraction.scheduler.runner.DataProcessor"):
            from BroadTopicExtraction.scheduler.runner import TaskRunner

            runner = TaskRunner()
            config = {}  # 缺少 aggregator_name 和 aggregator_source

            result = await runner.run_aggregator("test_source", config)

            assert result["success"] is False
            assert "缺少聚合器配置" in result["error"]

    @pytest.mark.asyncio
    async def test_run_aggregator_unknown_aggregator(self):
        """测试未知聚合器"""
        with patch("BroadTopicExtraction.scheduler.runner.DataProcessor"):
            with patch("BroadTopicExtraction.scheduler.runner.get_aggregator") as mock_get:
                mock_get.return_value = None

                from BroadTopicExtraction.scheduler.runner import TaskRunner

                runner = TaskRunner()
                config = {
                    "aggregator_name": "unknown",
                    "aggregator_source": "test",
                }

                result = await runner.run_aggregator("test_source", config)

                assert result["success"] is False
                assert "未知聚合器" in result["error"]

    @pytest.mark.asyncio
    async def test_run_aggregator_fetch_failed(self):
        """测试聚合器获取数据失败"""
        with patch("BroadTopicExtraction.scheduler.runner.DataProcessor"):
            with patch("BroadTopicExtraction.scheduler.runner.get_aggregator") as mock_get:
                # Mock 聚合器
                mock_aggregator = AsyncMock()
                mock_aggregator.__aenter__ = AsyncMock(return_value=mock_aggregator)
                mock_aggregator.__aexit__ = AsyncMock(return_value=None)

                # Mock 失败的结果
                mock_result = MagicMock()
                mock_result.success = False
                mock_result.error = "API 错误"
                mock_aggregator.fetch = AsyncMock(return_value=mock_result)

                mock_get.return_value = mock_aggregator

                from BroadTopicExtraction.scheduler.runner import TaskRunner

                runner = TaskRunner()
                config = {
                    "aggregator_name": "tophub",
                    "aggregator_source": "weibo",
                }

                result = await runner.run_aggregator("test_source", config)

                assert result["success"] is False
                assert result["error"] == "API 错误"

    @pytest.mark.asyncio
    async def test_run_aggregator_success(self):
        """测试聚合器任务成功"""
        with patch("BroadTopicExtraction.scheduler.runner.DataProcessor") as MockProcessor:
            mock_processor = MagicMock()
            mock_processor.process_batch_optimized.return_value = {
                "inserted": 5,
                "updated": 3,
                "skipped": 2,
            }
            MockProcessor.return_value = mock_processor

            with patch("BroadTopicExtraction.scheduler.runner.get_aggregator") as mock_get:
                # Mock 聚合器
                mock_aggregator = AsyncMock()
                mock_aggregator.__aenter__ = AsyncMock(return_value=mock_aggregator)
                mock_aggregator.__aexit__ = AsyncMock(return_value=None)

                # Mock 成功的结果
                mock_result = MagicMock()
                mock_result.success = True
                mock_result.count = 10
                mock_result.items = [{"title": f"item{i}"} for i in range(10)]
                mock_aggregator.fetch = AsyncMock(return_value=mock_result)

                mock_get.return_value = mock_aggregator

                from BroadTopicExtraction.scheduler.runner import TaskRunner

                runner = TaskRunner()
                config = {
                    "aggregator_name": "tophub",
                    "aggregator_source": "weibo",
                }

                result = await runner.run_aggregator("test_source", config)

                assert result["success"] is True
                assert result["fetched"] == 10
                assert result["inserted"] == 5
                assert result["updated"] == 3
                assert result["skipped"] == 2

    def test_run_scrapy_missing_spider_name(self):
        """测试 Scrapy 任务缺少 spider_name"""
        with patch("BroadTopicExtraction.scheduler.runner.DataProcessor"):
            from BroadTopicExtraction.scheduler.runner import TaskRunner

            runner = TaskRunner()
            config = {}  # 缺少 spider_name

            result = runner.run_scrapy("test_source", config)

            assert result["success"] is False
            assert "缺少 spider_name" in result["error"]

    def test_run_scrapy_timeout(self):
        """测试 Scrapy 任务超时"""
        import subprocess

        with patch("BroadTopicExtraction.scheduler.runner.DataProcessor"):
            with patch("subprocess.run") as mock_run:
                mock_run.side_effect = subprocess.TimeoutExpired(cmd="scrapy", timeout=300)

                from BroadTopicExtraction.scheduler.runner import TaskRunner

                runner = TaskRunner()
                config = {"spider_name": "test_spider"}

                result = runner.run_scrapy("test_source", config)

                assert result["success"] is False
                assert "超时" in result["error"]

    def test_run_scrapy_success(self):
        """测试 Scrapy 任务成功"""
        with patch("BroadTopicExtraction.scheduler.runner.DataProcessor"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    returncode=0,
                    stderr="2026-01-01 [scrapy.statscollectors] INFO: Dumping Scrapy stats:\n{'item_scraped_count': 10}",
                )

                from BroadTopicExtraction.scheduler.runner import TaskRunner

                runner = TaskRunner()
                config = {"spider_name": "test_spider"}

                result = runner.run_scrapy("test_source", config)

                assert result["success"] is True

    def test_run_scrapy_failed(self):
        """测试 Scrapy 任务失败"""
        with patch("BroadTopicExtraction.scheduler.runner.DataProcessor"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(returncode=1, stderr="Spider error")

                from BroadTopicExtraction.scheduler.runner import TaskRunner

                runner = TaskRunner()
                config = {"spider_name": "test_spider"}

                result = runner.run_scrapy("test_source", config)

                assert result["success"] is False
                assert "Spider error" in result["error"]

    @pytest.mark.asyncio
    async def test_run_task_aggregator(self):
        """测试 run_task 自动选择聚合器"""
        with patch("BroadTopicExtraction.scheduler.runner.DataProcessor"):
            from BroadTopicExtraction.scheduler.runner import TaskRunner

            runner = TaskRunner()
            runner.run_aggregator = AsyncMock(return_value={"success": True})

            config = {
                "source_type": "aggregator",
                "aggregator_name": "tophub",
                "aggregator_source": "weibo",
            }

            result = await runner.run_task("test_source", config)

            runner.run_aggregator.assert_called_once_with("test_source", config)
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_run_task_scrapy(self):
        """测试 run_task 自动选择 Scrapy"""
        with patch("BroadTopicExtraction.scheduler.runner.DataProcessor"):
            from BroadTopicExtraction.scheduler.runner import TaskRunner

            runner = TaskRunner()
            runner.run_scrapy = MagicMock(return_value={"success": True})

            config = {
                "source_type": "scrapy",
                "spider_name": "test_spider",
            }

            result = await runner.run_task("test_source", config)

            assert result["success"] is True

    def test_close(self):
        """测试关闭资源"""
        with patch("BroadTopicExtraction.scheduler.runner.DataProcessor") as MockProcessor:
            mock_processor = MagicMock()
            MockProcessor.return_value = mock_processor

            from BroadTopicExtraction.scheduler.runner import TaskRunner

            runner = TaskRunner()
            runner._get_processor()  # 初始化处理器

            runner.close()

            mock_processor.close.assert_called_once()
            assert runner.processor is None
