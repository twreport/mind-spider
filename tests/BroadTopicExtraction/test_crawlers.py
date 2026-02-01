# -*- coding: utf-8 -*-
"""
Crawlers 模块单元测试

测试 crawlers/ 下的 Scrapy Items 和 Spider 基类
"""

import pytest
from unittest.mock import MagicMock, patch


class TestScrapyItems:
    """测试 Scrapy Item 定义"""

    def test_hot_search_item_fields(self):
        """测试热搜数据项字段"""
        from BroadTopicExtraction.crawlers.mindspider_crawlers.items import HotSearchItem

        item = HotSearchItem()
        item["title"] = "测试热搜"
        item["url"] = "https://example.com"
        item["position"] = 1
        item["platform"] = "weibo"
        item["hot_value"] = 1234567

        assert item["title"] == "测试热搜"
        assert item["position"] == 1
        assert item["hot_value"] == 1234567

    def test_hot_search_item_optional_fields(self):
        """测试热搜数据项可选字段"""
        from BroadTopicExtraction.crawlers.mindspider_crawlers.items import HotSearchItem

        item = HotSearchItem()
        item["title"] = "测试"
        item["url"] = "https://example.com"
        item["position"] = 1
        item["platform"] = "weibo"
        item["category"] = "娱乐"
        item["description"] = "这是描述"
        item["image"] = "https://example.com/img.jpg"
        item["extra"] = {"key": "value"}

        assert item["category"] == "娱乐"
        assert item["extra"]["key"] == "value"

    def test_local_hot_item_fields(self):
        """测试地方热搜数据项字段"""
        from BroadTopicExtraction.crawlers.mindspider_crawlers.items import LocalHotItem

        item = LocalHotItem()
        item["title"] = "北京本地热搜"
        item["url"] = "https://example.com"
        item["position"] = 1
        item["platform"] = "weibo"
        item["region"] = "beijing"

        assert item["region"] == "beijing"

    def test_vertical_hot_item_fields(self):
        """测试垂直榜单数据项字段"""
        from BroadTopicExtraction.crawlers.mindspider_crawlers.items import VerticalHotItem

        item = VerticalHotItem()
        item["title"] = "GitHub 热门项目"
        item["url"] = "https://github.com/test"
        item["position"] = 1
        item["platform"] = "github"
        item["vertical"] = "tech"
        item["stars"] = 10000
        item["author"] = "test_user"

        assert item["vertical"] == "tech"
        assert item["stars"] == 10000

    def test_media_item_fields(self):
        """测试媒体文章数据项字段"""
        from BroadTopicExtraction.crawlers.mindspider_crawlers.items import MediaItem

        item = MediaItem()
        item["title"] = "新闻标题"
        item["url"] = "https://example.com/news"
        item["platform"] = "rmrb"
        item["media_type"] = "central"
        item["content"] = "新闻正文内容..."
        item["summary"] = "摘要"
        item["publish_date"] = "2024-01-15"
        item["publish_time"] = "2024-01-15T10:00:00"
        item["author"] = "记者"
        item["tags"] = ["政治", "经济"]

        assert item["media_type"] == "central"
        assert item["publish_date"] == "2024-01-15"
        assert "政治" in item["tags"]

    def test_wechat_item_fields(self):
        """测试微信公众号数据项字段"""
        from BroadTopicExtraction.crawlers.mindspider_crawlers.items import WechatItem

        item = WechatItem()
        item["title"] = "公众号文章"
        item["url"] = "https://mp.weixin.qq.com/s/xxx"
        item["platform"] = "wechat"
        item["account_id"] = "test_account"
        item["account_name"] = "测试公众号"
        item["account_type"] = "tech"
        item["read_count"] = 10000
        item["like_count"] = 500

        assert item["account_name"] == "测试公众号"
        assert item["read_count"] == 10000


class TestBaseSpider:
    """测试 Spider 基类"""

    def test_base_spider_attributes(self):
        """测试基类属性"""
        from BroadTopicExtraction.crawlers.mindspider_crawlers.spiders.base import (
            BaseSpider,
        )

        # BaseSpider 是抽象类，检查类属性
        assert hasattr(BaseSpider, "name")
        assert hasattr(BaseSpider, "custom_settings")

    def test_base_spider_custom_settings(self):
        """测试基类自定义设置"""
        from BroadTopicExtraction.crawlers.mindspider_crawlers.spiders.base import (
            BaseSpider,
        )

        settings = BaseSpider.custom_settings

        # 检查是字典类型
        assert isinstance(settings, dict)


class TestSpiderParsing:
    """测试 Spider 解析逻辑"""

    def test_weibo_spider_exists(self):
        """测试微博爬虫存在"""
        from BroadTopicExtraction.crawlers.mindspider_crawlers.spiders.hot_national.weibo import (
            WeiboHotSpider,
        )

        assert WeiboHotSpider.name == "weibo_hot"

    def test_baidu_spider_exists(self):
        """测试百度爬虫存在"""
        from BroadTopicExtraction.crawlers.mindspider_crawlers.spiders.hot_national.baidu import (
            BaiduHotSpider,
        )

        assert BaiduHotSpider.name == "baidu_hot"

    def test_douyin_spider_exists(self):
        """测试抖音爬虫存在"""
        from BroadTopicExtraction.crawlers.mindspider_crawlers.spiders.hot_national.douyin import (
            DouyinHotSpider,
        )

        assert DouyinHotSpider.name == "douyin_hot"

    def test_rmrb_spider_exists(self):
        """测试人民日报爬虫存在"""
        from BroadTopicExtraction.crawlers.mindspider_crawlers.spiders.media.rmrb import (
            RmrbSpider,
        )

        assert RmrbSpider.name == "rmrb"


class TestScrapyPipelines:
    """测试 Scrapy Pipelines"""

    def test_pipeline_exists(self):
        """测试 Pipeline 存在"""
        from BroadTopicExtraction.crawlers.mindspider_crawlers.pipelines import (
            MongoPipeline,
        )

        assert MongoPipeline is not None

    def test_pipeline_has_required_methods(self):
        """测试 Pipeline 有必要的方法"""
        from BroadTopicExtraction.crawlers.mindspider_crawlers.pipelines import (
            MongoPipeline,
        )

        # 检查 Pipeline 类有必要的方法
        assert hasattr(MongoPipeline, "process_item")
        assert hasattr(MongoPipeline, "open_spider")
        assert hasattr(MongoPipeline, "close_spider")


class TestScrapyMiddlewares:
    """测试 Scrapy Middlewares"""

    def test_middleware_exists(self):
        """测试 Middleware 存在"""
        from BroadTopicExtraction.crawlers.mindspider_crawlers.middlewares import (
            RandomUserAgentMiddleware,
            RetryMiddleware,
            ProxyMiddleware,
        )

        assert RandomUserAgentMiddleware is not None
        assert RetryMiddleware is not None
        assert ProxyMiddleware is not None

    def test_random_user_agent_middleware(self):
        """测试随机 User-Agent 中间件"""
        from BroadTopicExtraction.crawlers.mindspider_crawlers.middlewares import (
            RandomUserAgentMiddleware,
        )

        user_agents = ["UA1", "UA2", "UA3"]
        middleware = RandomUserAgentMiddleware(user_agents)

        assert middleware.user_agents == user_agents


class TestScrapySettings:
    """测试 Scrapy Settings"""

    def test_settings_bot_name(self):
        """测试 BOT_NAME 设置"""
        from BroadTopicExtraction.crawlers.mindspider_crawlers import settings

        assert settings.BOT_NAME == "mindspider_crawlers"

    def test_settings_robotstxt(self):
        """测试 ROBOTSTXT_OBEY 设置"""
        from BroadTopicExtraction.crawlers.mindspider_crawlers import settings

        # 爬虫通常不遵守 robots.txt
        assert hasattr(settings, "ROBOTSTXT_OBEY")

    def test_settings_download_delay(self):
        """测试下载延迟设置"""
        from BroadTopicExtraction.crawlers.mindspider_crawlers import settings

        assert hasattr(settings, "DOWNLOAD_DELAY")
        assert settings.DOWNLOAD_DELAY >= 0
