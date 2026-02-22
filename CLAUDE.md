# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

MindSpider is an AI-powered sentiment monitoring system for Chinese social media platforms. It adopts a **capability-based, event-driven architecture** with five core capabilities that can be independently triggered and interconnected through feedback loops:

### Six Core Capabilities

| 能力 | 代码位置 | 职责 |
|------|---------|------|
| 表层采集 (Surface Collection) | `BroadTopicExtraction/` | 爬热榜、媒体、聚合器，写入 MongoDB |
| 信号检测 (Signal Detection) | `BroadTopicExtraction/analyzer/` | 硬编码算法发现异动，输出信号 |
| 候选话题管理 (Candidate Management) | `BroadTopicExtraction/analyzer/` | 话题生命周期状态机，触发决策 |
| 深层采集 (Deep Collection) | `DeepSentimentCrawling/` | 爬 7 个社交平台的详细内容（帖子、评论） |
| 话题分析 (Topic Analysis) | `BroadTopicExtraction/analyzer/` | LLM 深度分析、聚类、研判 |
| 客户过滤 (Client Filtering) | 待实现 | 个性化相关性评分、推送 |

These capabilities are NOT sequential steps — they are services triggered by multiple sources (scheduled, event-driven, client-initiated, feedback loops) and orchestrated dynamically.

### 开发进度

| 能力 | 状态 | 说明 |
|------|------|------|
| 表层采集 | ✅ 已完成 | 8 个聚合器，15 个爬虫，53 个数据源 |
| 信号检测 | 🚧 开发中 | 7 种信号类型 |
| 候选话题管理 | 🚧 开发中 | 状态机（emerging/rising/confirmed/exploded/tracking/closed） |
| 话题分析 | 🚧 开发中 | LLM 晨报/晚报，语义聚类 |
| 深层采集 | 📋 计划中 | 7 平台详细内容爬取，多触发源 |
| 客户过滤 | 📋 计划中 | 兴趣画像，相关性评分 |

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
# BroadTopicExtraction - 调度器
uv run python BroadTopicExtraction/start_scheduler.py                          # 启动持续调度
uv run python BroadTopicExtraction/start_scheduler.py --once                   # 所有任务执行一次
uv run python BroadTopicExtraction/start_scheduler.py --list                   # 列出所有数据源
uv run python BroadTopicExtraction/start_scheduler.py --log-level ERROR        # 终端只显示错误
uv run python BroadTopicExtraction/start_scheduler.py --categories hot_national hot_vertical  # 只跑指定分类

# BroadTopicExtraction - 旧入口
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
┌──────────────────────────────────────────────────────────────────┐
│                       调度与编排层                                  │
│            （事件驱动，管理触发、优先级、反馈）                       │
└──┬──────┬──────┬──────┬──────┬──────┬────────────────────────────┘
   │      │      │      │      │      │
   ▼      ▼      ▼      ▼      ▼      ▼
┌──────┐┌──────┐┌──────┐┌──────┐┌──────┐┌──────┐
│ 表层 ││ 信号 ││ 候选 ││ 深层 ││ 话题 ││ 客户 │
│ 采集 ││ 检测 ││ 管理 ││ 采集 ││ 分析 ││ 过滤 │
└──┬───┘└──┬───┘└──┬───┘└──┬───┘└──┬───┘└──┬───┘
   └───────┴───────┴───────┴───────┴───────┘
                        │
                        ▼
               ┌────────────────┐
               │   共享数据层     │
               │ MongoDB + MySQL │
               └────────────────┘
```

Capabilities communicate through shared data (MongoDB collections, MySQL tables) and are connected by seven feedback loops:
- Signal detection → Candidate management (new signals trigger state transitions)
- Candidate management → Deep collection (state changes trigger crawling at different scales)
- Deep collection → Signal detection (discover signals not visible on hot lists)
- Deep collection → Candidate management (validate early warning candidates)
- Topic analysis → Candidate management (LLM upgrades/downgrades candidate status)
- Topic analysis → Deep collection (LLM identifies topics needing deeper investigation)
- Fingerprint library → Signal detection (adaptive thresholds)

### Key Entry Points
- `main.py` - Root orchestrator (`MindSpider` class)
- `BroadTopicExtraction/start_scheduler.py` - Surface collection scheduler (53 data sources)
- `BroadTopicExtraction/analyzer/` - Signal detection + Topic analysis (in development)
- `DeepSentimentCrawling/main.py` - Deep collection CLI (`DeepSentimentCrawling` class)

### Core Components
- `BroadTopicExtraction/scheduler/` - APScheduler-based task scheduling with jitter
- `BroadTopicExtraction/pipeline/` - Data pipeline: config loading, MongoDB writing, deduplication
- `BroadTopicExtraction/aggregators/` - 8 aggregator implementations (tophub, newsnow, official API, etc.)
- `BroadTopicExtraction/spiders/` - 15 Scrapy spiders for direct page crawling
- `BroadTopicExtraction/analyzer/` - Signal detection + LLM topic analysis (in development)
- `DeepSentimentCrawling/keyword_manager.py` - Retrieves topics and manages keyword distribution
- `DeepSentimentCrawling/platform_crawler.py` - Multi-platform crawler orchestrator
- `DeepSentimentCrawling/MediaCrawler/` - Platform-specific crawlers using Playwright

### Database
- Schema: `schema/mindspider_tables.sql`
- ORM models: `schema/models_sa.py`
- MongoDB collections: `hot_national`, `hot_vertical`, `hot_local`, `media`, `aggregator`, `signals`, `candidates`, `fingerprints`
- MySQL core tables: `daily_news`, `daily_topics`, `topic_news_relation`, `crawling_tasks`
- MySQL platform tables: `xhs_note`, `douyin_aweme`, `kuaishou_video`, `bilibili_video`, `weibo_note`, `tieba_note`, `zhihu_content`

### Configuration
- `.env` - Environment variables (database credentials, API keys)
- `ms_config.py` - Generated from `ms_config.py.example`, uses Pydantic Settings
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
- **爬取 7 个平台的诀窍都在 `PLATFORM_DEBUG_NOTES.md` 里面，请认真阅读，在调试或测试各大平台爬虫时必读。**

## 项目优缺点分析

### 优点

**1. 能力化事件驱动架构**
- 六个核心能力（表层采集、信号检测、候选话题管理、深层采集、话题分析、客户过滤）平等并行
- 多触发源（定时/事件/客户/反馈）动态编排，而非固定流水线
- 反馈环机制：深层数据反哺信号检测，LLM 指导爬取方向

**2. 多层级数据源分角色处理**
- 全国热搜（主信号源）、地方热搜（潜伏期探测器）、垂直社区（领域信号源）、传统媒体（权威背书层）、聚合平台（数据冗余层）
- 层级跃迁检测：地方→全国、垂直→全国、社交→央媒等

**3. 多平台统一管理**
- 7 个主流中文社交平台统一接口
- 统一的数据模型和存储结构

**4. 配置灵活**
- 支持 MySQL/PostgreSQL 双数据库
- AI API 可切换（DeepSeek 成本更低）
- Pydantic Settings 管理配置，类型安全

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

本项目适合**中小规模舆情监测**场景，支持通用热点检测和客户个性化监测（品牌/地方/行业）。如果要做生产级的集群爬虫系统，需要补充分布式调度、反爬对抗、监控告警等能力。

## 相关文档

- **HOTSPOT_METHODOLOGY.md** - 舆情热点分析设计哲学与方法论（核心设计文档）
- **PHASE_1_2_PLAN.md** - 信号检测 + 话题分析模块实现计划
- **DEPLOYMENT.md** - 部署指南，包含服务器配置、反爬策略、各平台风控特点、数据量估算
