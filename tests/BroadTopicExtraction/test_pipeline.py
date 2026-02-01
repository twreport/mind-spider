# -*- coding: utf-8 -*-
"""
Pipeline 模块单元测试

测试 pipeline/ 下的配置加载器、MongoDB 写入器和数据处理器
"""

import pytest
import hashlib
from unittest.mock import MagicMock, patch


class TestConfigLoader:
    """测试 YAML 配置加载器"""

    def test_load_sources(self, temp_yaml_config):
        """测试加载信源配置"""
        from BroadTopicExtraction.pipeline.config_loader import ConfigLoader

        loader = ConfigLoader(config_dir=str(temp_yaml_config))
        sources = loader.get_all_sources()

        assert "weibo_hot" in sources
        assert "rmrb" in sources

    def test_get_source(self, temp_yaml_config):
        """测试获取单个信源配置"""
        from BroadTopicExtraction.pipeline.config_loader import ConfigLoader

        loader = ConfigLoader(config_dir=str(temp_yaml_config))
        config = loader.get_source("weibo_hot")

        assert config is not None
        assert config["name"] == "微博热搜"
        assert config["category"] == "hot_national"

    def test_get_source_not_found(self, temp_yaml_config):
        """测试获取不存在的信源"""
        from BroadTopicExtraction.pipeline.config_loader import ConfigLoader

        loader = ConfigLoader(config_dir=str(temp_yaml_config))
        config = loader.get_source("not_exist")

        assert config is None

    def test_get_sources_by_category(self, temp_yaml_config):
        """测试按分类获取信源"""
        from BroadTopicExtraction.pipeline.config_loader import ConfigLoader

        loader = ConfigLoader(config_dir=str(temp_yaml_config))
        sources = loader.get_sources_by_category("hot_national")

        assert "weibo_hot" in sources
        assert "rmrb" not in sources

    def test_get_sources_by_type(self, temp_yaml_config):
        """测试按采集方式获取信源"""
        from BroadTopicExtraction.pipeline.config_loader import ConfigLoader

        loader = ConfigLoader(config_dir=str(temp_yaml_config))

        aggregator_sources = loader.get_sources_by_type("aggregator")
        scrapy_sources = loader.get_sources_by_type("scrapy")

        assert "weibo_hot" in aggregator_sources
        assert "rmrb" in scrapy_sources

    def test_get_enabled_sources(self, temp_yaml_config):
        """测试获取启用的信源"""
        from BroadTopicExtraction.pipeline.config_loader import ConfigLoader

        loader = ConfigLoader(config_dir=str(temp_yaml_config))
        sources = loader.get_enabled_sources()

        assert len(sources) == 2

    def test_list_categories(self, temp_yaml_config):
        """测试列出所有分类"""
        from BroadTopicExtraction.pipeline.config_loader import ConfigLoader

        loader = ConfigLoader(config_dir=str(temp_yaml_config))
        categories = loader.list_categories()

        assert "hot_national" in categories
        assert "media" in categories

    def test_list_sources(self, temp_yaml_config):
        """测试列出所有信源名称"""
        from BroadTopicExtraction.pipeline.config_loader import ConfigLoader

        loader = ConfigLoader(config_dir=str(temp_yaml_config))
        sources = loader.list_sources()

        assert "weibo_hot" in sources
        assert "rmrb" in sources

    def test_get_schedule_config(self, temp_yaml_config):
        """测试获取调度配置"""
        from BroadTopicExtraction.pipeline.config_loader import ConfigLoader

        loader = ConfigLoader(config_dir=str(temp_yaml_config))
        schedule = loader.get_schedule_config()

        assert schedule["timezone"] == "Asia/Shanghai"
        assert len(schedule["jobs"]) == 1

    def test_reload(self, temp_yaml_config):
        """测试重新加载配置"""
        from BroadTopicExtraction.pipeline.config_loader import ConfigLoader

        loader = ConfigLoader(config_dir=str(temp_yaml_config))
        initial_count = len(loader.get_all_sources())

        loader.reload()

        assert len(loader.get_all_sources()) == initial_count

    def test_missing_config_dir(self, tmp_path):
        """测试配置目录不存在"""
        from BroadTopicExtraction.pipeline.config_loader import ConfigLoader

        loader = ConfigLoader(config_dir=str(tmp_path / "nonexistent"))
        sources = loader.get_all_sources()

        assert len(sources) == 0


class TestMongoWriter:
    """测试 MongoDB 写入器"""

    def test_init_default(self):
        """测试默认初始化"""
        with patch("BroadTopicExtraction.pipeline.mongo_writer.settings") as mock_settings:
            mock_settings.MONGO_URI = "mongodb://localhost:27017"
            mock_settings.MONGO_DB_NAME = "test_db"

            from BroadTopicExtraction.pipeline.mongo_writer import MongoWriter

            writer = MongoWriter()

            assert writer.mongo_uri == "mongodb://localhost:27017"
            assert writer.db_name == "test_db"

    def test_init_custom(self):
        """测试自定义初始化"""
        with patch("BroadTopicExtraction.pipeline.mongo_writer.settings"):
            from BroadTopicExtraction.pipeline.mongo_writer import MongoWriter

            writer = MongoWriter(
                mongo_uri="mongodb://custom:27017",
                db_name="custom_db",
            )

            assert writer.mongo_uri == "mongodb://custom:27017"
            assert writer.db_name == "custom_db"

    def test_context_manager(self, mock_mongo_client):
        """测试上下文管理器"""
        with patch("BroadTopicExtraction.pipeline.mongo_writer.settings"):
            with patch("BroadTopicExtraction.pipeline.mongo_writer.MongoClient") as MockClient:
                MockClient.return_value = mock_mongo_client

                from BroadTopicExtraction.pipeline.mongo_writer import MongoWriter

                with MongoWriter(mongo_uri="mongodb://test:27017", db_name="test") as writer:
                    assert writer._client is not None

    def test_insert_many_empty(self, mock_mongo_client):
        """测试批量插入空列表"""
        with patch("BroadTopicExtraction.pipeline.mongo_writer.settings"):
            from BroadTopicExtraction.pipeline.mongo_writer import MongoWriter

            writer = MongoWriter(mongo_uri="mongodb://test:27017", db_name="test")
            result = writer.insert_many("test_collection", [])

            assert result == []

    def test_bulk_write_empty(self, mock_mongo_client):
        """测试批量写入空操作列表"""
        with patch("BroadTopicExtraction.pipeline.mongo_writer.settings"):
            from BroadTopicExtraction.pipeline.mongo_writer import MongoWriter

            writer = MongoWriter(mongo_uri="mongodb://test:27017", db_name="test")
            result = writer.bulk_write("test_collection", [])

            assert result == {"inserted": 0, "modified": 0, "upserted": 0}


class TestDataProcessor:
    """测试数据处理器"""

    def test_generate_item_id(self, temp_yaml_config):
        """测试生成唯一 ID"""
        with patch("BroadTopicExtraction.pipeline.mongo_writer.settings"):
            from BroadTopicExtraction.pipeline.processor import DataProcessor

            processor = DataProcessor(
                mongo_uri="mongodb://test:27017",
                config_dir=str(temp_yaml_config),
            )

            item = {"title": "测试标题", "platform": "weibo"}
            item_id = processor._generate_item_id(
                item, "weibo_hot", ["title", "platform"]
            )

            # 验证是 MD5 哈希
            expected = hashlib.md5("weibo_hot_测试标题_weibo".encode("utf-8")).hexdigest()
            assert item_id == expected

    def test_generate_item_id_missing_field(self, temp_yaml_config):
        """测试生成 ID 时字段缺失"""
        with patch("BroadTopicExtraction.pipeline.mongo_writer.settings"):
            from BroadTopicExtraction.pipeline.processor import DataProcessor

            processor = DataProcessor(
                mongo_uri="mongodb://test:27017",
                config_dir=str(temp_yaml_config),
            )

            item = {"title": "测试标题"}  # 缺少 platform
            item_id = processor._generate_item_id(
                item, "weibo_hot", ["title", "platform"]
            )

            # 缺失字段应该用空字符串
            expected = hashlib.md5("weibo_hot_测试标题_".encode("utf-8")).hexdigest()
            assert item_id == expected

    def test_build_new_doc(self, temp_yaml_config):
        """测试构建新文档"""
        with patch("BroadTopicExtraction.pipeline.mongo_writer.settings"):
            from BroadTopicExtraction.pipeline.processor import DataProcessor

            processor = DataProcessor(
                mongo_uri="mongodb://test:27017",
                config_dir=str(temp_yaml_config),
            )

            item = {"title": "测试", "position": 1, "hot_value": 1000}
            now = 1700000000

            doc = processor._build_new_doc(
                item,
                item_id="test_id",
                source_name="weibo_hot",
                time_varying_fields=["position", "hot_value"],
                now=now,
            )

            assert doc["item_id"] == "test_id"
            assert doc["source"] == "weibo_hot"
            assert doc["first_seen_at"] == now
            assert doc["last_seen_at"] == now
            assert doc["position_history"] == [{"ts": now, "val": 1}]
            assert doc["hot_value_history"] == [{"ts": now, "val": 1000}]

    def test_build_update_ops(self, temp_yaml_config):
        """测试构建更新操作"""
        with patch("BroadTopicExtraction.pipeline.mongo_writer.settings"):
            from BroadTopicExtraction.pipeline.processor import DataProcessor

            processor = DataProcessor(
                mongo_uri="mongodb://test:27017",
                config_dir=str(temp_yaml_config),
            )

            item = {"position": 5, "hot_value": 2000}
            now = 1700000000

            ops = processor._build_update_ops(
                item, ["position", "hot_value"], now
            )

            assert ops["$set"]["last_seen_at"] == now
            assert ops["$set"]["position"] == 5
            assert ops["$set"]["hot_value"] == 2000
            assert ops["$push"]["position_history"] == {"ts": now, "val": 5}
            assert ops["$push"]["hot_value_history"] == {"ts": now, "val": 2000}

    def test_build_update_ops_partial(self, temp_yaml_config):
        """测试部分字段更新"""
        with patch("BroadTopicExtraction.pipeline.mongo_writer.settings"):
            from BroadTopicExtraction.pipeline.processor import DataProcessor

            processor = DataProcessor(
                mongo_uri="mongodb://test:27017",
                config_dir=str(temp_yaml_config),
            )

            item = {"position": 5}  # 只有 position，没有 hot_value
            now = 1700000000

            ops = processor._build_update_ops(
                item, ["position", "hot_value"], now
            )

            assert "position" in ops["$set"]
            assert "hot_value" not in ops["$set"]

    def test_process_unknown_source(self, temp_yaml_config, mock_mongo_client):
        """测试处理未知信源"""
        with patch("BroadTopicExtraction.pipeline.mongo_writer.settings"):
            with patch("BroadTopicExtraction.pipeline.mongo_writer.MongoClient") as MockClient:
                MockClient.return_value = mock_mongo_client

                from BroadTopicExtraction.pipeline.processor import DataProcessor

                processor = DataProcessor(
                    mongo_uri="mongodb://test:27017",
                    config_dir=str(temp_yaml_config),
                )

                with pytest.raises(ValueError, match="未知信源"):
                    processor.process({"title": "test"}, "unknown_source")


class TestProcessResult:
    """测试处理结果类"""

    def test_to_dict(self):
        """测试转换为字典"""
        from BroadTopicExtraction.pipeline.processor import ProcessResult

        result = ProcessResult(
            action="inserted",
            item_id="test_id",
            source="weibo_hot",
        )
        d = result.to_dict()

        assert d["action"] == "inserted"
        assert d["item_id"] == "test_id"
        assert d["source"] == "weibo_hot"
