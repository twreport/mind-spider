# -*- coding: utf-8 -*-
"""
pytest 配置和共享 fixtures
"""

import pytest
import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(project_root / "BroadTopicExtraction"))


@pytest.fixture
def mock_mongo_client():
    """Mock MongoDB 客户端"""
    client = MagicMock()
    client.admin.command.return_value = {"ok": 1}
    return client


@pytest.fixture
def mock_mongo_collection():
    """Mock MongoDB 集合"""
    collection = MagicMock()
    collection.find_one.return_value = None
    collection.insert_one.return_value = MagicMock(inserted_id="test_id")
    collection.insert_many.return_value = MagicMock(inserted_ids=["id1", "id2"])
    collection.update_one.return_value = MagicMock(modified_count=1)
    collection.count_documents.return_value = 0
    return collection


@pytest.fixture
def mock_httpx_client():
    """Mock httpx 异步客户端"""
    client = AsyncMock()
    client.is_closed = False
    return client


@pytest.fixture
def sample_hot_search_item():
    """示例热搜数据"""
    return {
        "title": "测试热搜标题",
        "url": "https://example.com/test",
        "position": 1,
        "platform": "weibo",
        "hot_value": 1234567,
    }


@pytest.fixture
def sample_media_item():
    """示例媒体文章数据"""
    return {
        "title": "测试新闻标题",
        "url": "https://example.com/news/1",
        "platform": "rmrb",
        "media_type": "central",
        "content": "这是新闻正文内容...",
        "publish_date": "2024-01-15",
        "author": "记者",
    }


@pytest.fixture
def sample_source_config():
    """示例信源配置"""
    return {
        "weibo_hot": {
            "name": "微博热搜",
            "category": "hot_national",
            "source_type": "aggregator",
            "aggregator_name": "tophub",
            "aggregator_source": "weibo",
            "mongo_collection": "raw_hot_national",
            "dedup_fields": ["title", "platform"],
            "time_varying_fields": ["position", "hot_value"],
            "enabled": True,
        },
        "rmrb": {
            "name": "人民日报",
            "category": "media",
            "source_type": "scrapy",
            "spider_name": "rmrb",
            "mongo_collection": "raw_media",
            "dedup_fields": ["url"],
            "time_varying_fields": [],
            "enabled": True,
        },
    }


@pytest.fixture
def temp_yaml_config(tmp_path, sample_source_config):
    """创建临时 YAML 配置文件"""
    import yaml

    config_dir = tmp_path / "config" / "sources"
    config_dir.mkdir(parents=True)

    # 写入测试配置
    config_file = config_dir / "test.yaml"
    with open(config_file, "w", encoding="utf-8") as f:
        yaml.dump(sample_source_config, f, allow_unicode=True)

    # 写入调度配置
    schedule_file = tmp_path / "config" / "schedule.yaml"
    schedule_config = {
        "timezone": "Asia/Shanghai",
        "jobs": [
            {"id": "test_job", "cron": "0 8 * * *", "sources": ["weibo_hot"]}
        ],
    }
    with open(schedule_file, "w", encoding="utf-8") as f:
        yaml.dump(schedule_config, f, allow_unicode=True)

    return config_dir
