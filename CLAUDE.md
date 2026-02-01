# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MindSpider is an AI-powered sentiment crawler for Chinese social media platforms. It operates as a two-stage pipeline:

1. **BroadTopicExtraction**: Collects daily trending news and extracts key topics using DeepSeek/OpenAI API
2. **DeepSentimentCrawling**: Crawls 7 Chinese social media platforms (Xiaohongshu, Douyin, Kuaishou, Bilibili, Weibo, Tieba, Zhihu) based on extracted topics

## Python 环境

本项目统一使用 **uv** 作为 Python 包管理和运行环境。

```bash
# 安装 uv (如果尚未安装)
# Windows: powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
# Linux/macOS: curl -LsSf https://astral.sh/uv/install.sh | sh

# 创建虚拟环境并安装依赖
uv venv
uv pip install -e ".[dev]"
uv run playwright install chromium
```

## Commands

### Installation
```bash
uv pip install -e .
uv run playwright install chromium
uv pip install -e ".[dev]"  # for development
```

### Running the Project
```bash
# Full workflow (topic extraction + crawling)
uv run python main.py --complete

# Individual stages
uv run python main.py --broad-topic --keywords 100
uv run python main.py --deep-sentiment --platforms xhs dy bili --max-keywords 50 --max-notes 50

# Setup and status
uv run python main.py --setup
uv run python main.py --status
uv run python main.py --init-db

# Test mode (reduced data)
uv run python main.py --complete --test
```

### Module-specific Commands
```bash
# BroadTopicExtraction
cd BroadTopicExtraction && uv run python main.py --keywords 100 --list-sources

# DeepSentimentCrawling
cd DeepSentimentCrawling && uv run python main.py --guide
cd DeepSentimentCrawling && uv run python main.py --list-topics --days 7
cd DeepSentimentCrawling && uv run python main.py --platform xhs --max-notes 50
```

### Code Quality
```bash
uv run black .              # format
uv run ruff check .         # lint
uv run mypy .               # type check
uv run pytest tests/ -v     # test
uv run pre-commit run --all-files
```

## Architecture

```
News Sources → BroadTopicExtraction → daily_topics table → DeepSentimentCrawling → Platform-specific tables
```

### Key Entry Points
- `main.py` - Root orchestrator (`MindSpider` class) - coordinates both stages
- `BroadTopicExtraction/main.py` - Topic extraction CLI (`BroadTopicExtraction` class)
- `DeepSentimentCrawling/main.py` - Crawling CLI (`DeepSentimentCrawling` class)

### Core Components
- `BroadTopicExtraction/topic_extractor.py` - AI-powered keyword extraction using DeepSeek API
- `BroadTopicExtraction/get_today_news.py` - News collection from multiple Chinese sources
- `DeepSentimentCrawling/keyword_manager.py` - Retrieves topics and manages keyword distribution
- `DeepSentimentCrawling/platform_crawler.py` - Multi-platform crawler orchestrator
- `DeepSentimentCrawling/MediaCrawler/` - Platform-specific crawlers using Playwright

### Database
- Schema: `schema/mindspider_tables.sql`
- ORM models: `schema/models_sa.py`
- Core tables: `daily_news`, `daily_topics`, `topic_news_relation`, `crawling_tasks`
- Platform tables: `xhs_note`, `douyin_aweme`, `kuaishou_video`, `bilibili_video`, `weibo_note`, `tieba_note`, `zhihu_content`

### Configuration
- `.env` - Environment variables (database credentials, API keys)
- `config.py` - Generated from `config.py.example`, uses Pydantic Settings
- Supports MySQL and PostgreSQL
- AI API: DeepSeek recommended (`MINDSPIDER_API_KEY`, `MINDSPIDER_BASE_URL`)

## Platform Codes
- `xhs` - Xiaohongshu (小红书)
- `dy` - Douyin (抖音)
- `ks` - Kuaishou (快手)
- `bili` - Bilibili (哔哩哔哩)
- `wb` - Weibo (微博)
- `tieba` - Tieba (贴吧)
- `zhihu` - Zhihu (知乎)

## Notes
- `BroadTopicExtraction.run_daily_extraction()` is async - uses `asyncio.run()` for execution
- Most platforms require login (QR code, phone, or cookie-based)
- Crawlers include delays to avoid platform rate limiting
- Commercial use requires written permission from original MediaCrawler author

## 项目优缺点分析

### 优点

**1. 智能化的两阶段架构**
- 先通过 AI 提取热点话题，再针对性爬取，比盲目全量爬取更高效
- 话题与内容通过 `topic_id` 关联，便于后续舆情分析

**2. 多平台统一管理**
- 7 个主流中文社交平台统一接口
- 统一的数据模型和存储结构

**3. 配置灵活**
- 支持 MySQL/PostgreSQL 双数据库
- AI API 可切换（DeepSeek 成本更低）
- Pydantic Settings 管理配置，类型安全

**4. 开发规范完善**
- 完整的代码质量工具链（black、ruff、mypy、pytest）
- 异步支持，数据库连接池

### 缺点

**1. 单机架构，无法水平扩展**
- 没有分布式任务队列（如 Celery + Redis）
- 没有多节点协调机制
- 大规模爬取时会成为瓶颈

**2. 反爬能力较弱**
- 仅靠简单延迟和代理
- 缺乏指纹伪装、验证码识别等高级反检测
- 依赖 Playwright 浏览器自动化，资源消耗大

**3. 运维能力不足**
- 缺乏监控告警系统
- 没有任务重试、断点续爬机制
- 登录态管理需要人工介入（扫码）

**4. 数据处理能力有限**
- 主要是采集存储，缺乏实时分析管道
- 没有情感分析模块（名字叫 Sentiment 但实际只是爬取）

### 适用场景

本项目更适合**小规模、研究性质**的舆情监测场景。如果要做生产级的集群爬虫系统，需要补充分布式调度、反爬对抗、监控告警等能力。
