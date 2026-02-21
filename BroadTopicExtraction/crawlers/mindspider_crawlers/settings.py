# -*- coding: utf-8 -*-
"""
MindSpider Scrapy 爬虫设置
"""

BOT_NAME = "mindspider_crawlers"

SPIDER_MODULES = ["mindspider_crawlers.spiders"]
NEWSPIDER_MODULE = "mindspider_crawlers.spiders"

# 遵守 robots.txt
ROBOTSTXT_OBEY = False

# 并发设置
CONCURRENT_REQUESTS = 16
CONCURRENT_REQUESTS_PER_DOMAIN = 8

# 下载延迟 (秒)
DOWNLOAD_DELAY = 1
RANDOMIZE_DOWNLOAD_DELAY = True

# 禁用 cookies (除非特定爬虫需要)
COOKIES_ENABLED = True

# 禁用 Telnet 控制台
TELNETCONSOLE_ENABLED = False

# 默认请求头
DEFAULT_REQUEST_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
}

# 启用的中间件
DOWNLOADER_MIDDLEWARES = {
    "mindspider_crawlers.middlewares.RandomUserAgentMiddleware": 400,
    "mindspider_crawlers.middlewares.RetryMiddleware": 500,
}

# 启用的管道
ITEM_PIPELINES = {
    "mindspider_crawlers.pipelines.MongoPipeline": 300,
}

# 日志设置
LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"

# 重试设置
RETRY_ENABLED = True
RETRY_TIMES = 3
RETRY_HTTP_CODES = [500, 502, 503, 504, 408, 429]

# 超时设置
DOWNLOAD_TIMEOUT = 30

# 缓存设置 (开发时可启用)
# HTTPCACHE_ENABLED = True
# HTTPCACHE_EXPIRATION_SECS = 3600
# HTTPCACHE_DIR = "httpcache"

# User-Agent 列表 (用于随机切换)
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]

# MongoDB 配置 (从环境变量或 config.py 读取)
import sys
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
try:
    from ms_config import settings
    MONGO_URI = settings.MONGO_URI
    MONGO_DATABASE = settings.MONGO_DB_NAME
except ImportError:
    MONGO_URI = "mongodb://localhost:27017"
    MONGO_DATABASE = "mindspider_raw"

# 配置文件目录
CONFIG_DIR = str(Path(__file__).parent.parent.parent / "config" / "sources")
