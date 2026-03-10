# CLAUDE.md

MindSpider — 中文社交媒体舆情监测系统，能力化事件驱动架构。

## 六大能力模块

| 能力 | 代码位置 | 状态 |
|------|---------|------|
| 表层采集 | `BroadTopicExtraction/` (aggregators, spiders, scheduler, pipeline) | ✅ 完成 |
| 信号检测 | `BroadTopicExtraction/analyzer/` | 🚧 开发中 |
| 候选话题管理 | `BroadTopicExtraction/analyzer/` (状态机: emerging/rising/confirmed/exploded/tracking/closed) | 🚧 开发中 |
| 深层采集 | `DeepSentimentCrawling/` (7 平台, Playwright) | 🚧 开发中 |
| 话题分析 | `BroadTopicExtraction/analyzer/` (LLM) | 🚧 开发中 |
| 客户过滤 | 待实现 | 📋 计划中 |

## 入口文件

- `main.py` — 根编排器 (`MindSpider`)
- `BroadTopicExtraction/start_scheduler.py` — 表层采集调度 (53 数据源)
- `BroadTopicExtraction/analyzer/` — 信号检测 + 话题分析
- `DeepSentimentCrawling/main.py` — 深层采集 CLI
- `DeepSentimentCrawling/dispatcher.py` — 深层采集调度器
- `BroadTopicExtraction/admin/app.py` — 浅层采集监控面板 (端口 8778)

## 常用命令

```bash
# 环境 (统一用 uv)
uv pip install -e ".[dev]"
uv run playwright install chromium

# 运行
uv run python main.py --complete                    # 全流程
uv run python main.py --broad-topic --keywords 100  # 表层采集
uv run python BroadTopicExtraction/start_scheduler.py --once  # 调度器单次

# 深层采集
cd DeepSentimentCrawling && uv run python main.py --platform xhs --max-notes 50

# 代码质量
uv run black . && uv run ruff check .
uv run pytest tests/ -v
```

## 数据库

- **Schema**: `schema/mindspider_tables.sql` | **ORM**: `schema/models_sa.py`
- **MongoDB**: `hot_national`, `hot_vertical`, `hot_local`, `media`, `aggregator`, `signals`, `candidates`, `fingerprints`, `crawl_runs`
- **MySQL 核心表**: `daily_news`, `daily_topics`, `topic_news_relation`, `crawling_tasks`
- **MySQL 平台表**: `xhs_note`, `douyin_aweme`, `kuaishou_video`, `bilibili_video`, `weibo_note`, `tieba_note`, `zhihu_content`

## 配置

- `.env` — 数据库凭证、API keys
- `ms_config.py` — Pydantic Settings (从 `ms_config.py.example` 生成)
- AI API: DeepSeek (`MINDSPIDER_API_KEY`, `MINDSPIDER_BASE_URL`)

## 平台代码

`xhs`(小红书) `dy`(抖音) `ks`(快手) `bili`(B站) `wb`(微博) `tieba`(贴吧) `zhihu`(知乎)

## 注意

- 爬虫调试必读 `PLATFORM_DEBUG_NOTES.md`
- 设计文档: `HOTSPOT_METHODOLOGY.md` | 实现计划: `PHASE_1_2_PLAN.md` | 部署: `DEPLOYMENT.md`
- 如果需要了解爬取数据展示方面的问题打开 `DASHBOARD.md` 查看
- 如果涉及远程服务器操作，参照 `REMOTE_SERVER.md`
