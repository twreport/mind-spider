# START.md — MindSpider 启动命令参考

> 服务器环境：conda，环境名 `mind-spider`。
> 所有命令执行前先激活环境：`conda activate mind-spider`

---

## 环境准备

```bash
conda activate mind-spider
pip install -e ".[dev]"
playwright install chromium
```

---

## 1. 主编排器 (`main.py`)

一站式入口，可调用表层采集、深层采集或完整流程。

```bash
# 完整流程（表层 + 深层）
python main.py --complete

# 仅表层采集
python main.py --broad-topic
python main.py --broad-topic --keywords 100        # 指定关键词数量（默认100）

# 仅深层采集
python main.py --deep-sentiment
python main.py --deep-sentiment --platforms xhs dy  # 指定平台
python main.py --deep-sentiment --max-keywords 30 --max-notes 80

# 指定日期
python main.py --complete --date 2026-02-25

# 测试模式（数据量缩减）
python main.py --complete --test

# 初始化数据库
python main.py --init-db

# 初始化配置
python main.py --setup

# 查看项目状态
python main.py --status
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--complete` | flag | — | 运行完整流程 |
| `--broad-topic` | flag | — | 仅表层采集 |
| `--deep-sentiment` | flag | — | 仅深层采集 |
| `--date` | YYYY-MM-DD | 今天 | 目标日期 |
| `--platforms` | 列表 | 全部 | 指定平台（xhs dy ks bili wb tieba zhihu） |
| `--keywords-count` | int | 100 | 表层采集关键词数量 |
| `--max-keywords` | int | 50 | 每平台最大关键词数 |
| `--max-notes` | int | 50 | 每关键词最大内容数 |
| `--test` | flag | — | 测试模式 |
| `--init-db` | flag | — | 初始化数据库 |
| `--setup` | flag | — | 初始化配置 |
| `--status` | flag | — | 查看状态 |

---

## 2. 表层采集调度器 (`BroadTopicExtraction/start_scheduler.py`)

24/7 常驻调度，管理 53+ 数据源的定时采集。

```bash
# 启动常驻调度
python BroadTopicExtraction/start_scheduler.py

# 单次执行后退出（测试用）
python BroadTopicExtraction/start_scheduler.py --once

# 仅运行指定分类
python BroadTopicExtraction/start_scheduler.py --categories hot_national hot_vertical
python BroadTopicExtraction/start_scheduler.py --categories media --once

# 列出所有数据源
python BroadTopicExtraction/start_scheduler.py --list

# 调整日志级别
python BroadTopicExtraction/start_scheduler.py --log-level DEBUG

# 指定 MongoDB 连接
python BroadTopicExtraction/start_scheduler.py --mongo-uri "mongodb://user:pass@host:27017"
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--categories` | 列表 | 全部 | 数据源分类（见下方） |
| `--once` | flag | — | 执行一次后退出 |
| `--list` | flag | — | 列出所有数据源 |
| `--mongo-uri` | string | 配置文件 | MongoDB 连接 URI |
| `--log-level` | choice | INFO | DEBUG / INFO / WARNING / ERROR |

**数据源分类：**
- `hot_national` — 全国热搜（10 源）
- `hot_local` — 本地热搜（1 源）
- `hot_vertical` — 垂直领域热搜（6 源）
- `media` — 媒体源
- `wechat` — 微信相关

---

## 3. 表层采集模块 (`BroadTopicExtraction/main.py`)

直接调用表层采集，支持指定数据源。

```bash
# 全源采集
python BroadTopicExtraction/main.py

# 指定数据源
python BroadTopicExtraction/main.py --sources weibo zhihu douyin

# 限制关键词数量
python BroadTopicExtraction/main.py --keywords 50

# 精简输出
python BroadTopicExtraction/main.py --quiet

# 查看支持的数据源
python BroadTopicExtraction/main.py --list-sources
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--sources` | 列表 | 全部 | 数据源（见下方） |
| `--keywords` | int (1-200) | 100 | 最大关键词数 |
| `--quiet` | flag | — | 精简输出 |
| `--list-sources` | flag | — | 列出支持的数据源 |

**可选数据源：** `weibo` `zhihu` `bilibili-hot-search` `toutiao` `douyin` `github-trending-today` `coolapk` `tieba` `wallstreetcn` `thepaper` `cls-hot` `xueqiu`

---

## 4. 深层采集 CLI (`DeepSentimentCrawling/main.py`)

基于已提取话题，对 7 个社交平台进行深层内容采集。

```bash
cd DeepSentimentCrawling

# 采集全部平台
python main.py

# 单平台采集
python main.py --platform xhs
python main.py --platform dy --max-notes 100

# 多平台采集
python main.py --platforms xhs dy bili

# 控制采集规模
python main.py --platform xhs --max-keywords 20 --max-notes 30

# 指定日期
python main.py --date 2026-02-25

# 测试模式（关键词和内容各限10条）
python main.py --platform xhs --test

# 指定登录方式
python main.py --platform xhs --login-type cookie

# 查看近期话题
python main.py --list-topics
python main.py --list-topics --days 3

# 查看平台使用指南
python main.py --guide
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--platform` | string | — | 单平台（xhs/dy/ks/bili/wb/tieba/zhihu） |
| `--platforms` | 列表 | 全部 | 多平台 |
| `--date` | YYYY-MM-DD | 今天 | 目标日期 |
| `--max-keywords` | int | 50 | 每平台最大关键词数 |
| `--max-notes` | int | 50 | 每关键词最大内容数 |
| `--login-type` | choice | qrcode | qrcode / phone / cookie |
| `--test` | flag | — | 测试模式 |
| `--list-topics` | flag | — | 查看近期话题 |
| `--days` | int | 7 | 话题查询天数 |
| `--guide` | flag | — | 平台使用指南 |

---

## 5. 深层采集常驻服务 (`DeepSentimentCrawling/start_deep_crawl.py`)

24/7 运行的深层采集服务，含任务调度器和登录控制台。

```bash
cd DeepSentimentCrawling

# 启动服务
python start_deep_crawl.py

# 指定登录控制台端口
python start_deep_crawl.py --port 8080

# 限制平台（逗号分隔）
python start_deep_crawl.py --platforms xhs,dy,bili

# 试运行（仅打印任务，不执行）
python start_deep_crawl.py --dry-run
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--port` | int | 配置文件 | 登录控制台端口 |
| `--platforms` | string | 全部 | 逗号分隔的平台列表 |
| `--dry-run` | flag | — | 试运行，不实际采集 |

---

## 6. MediaCrawler 底层爬虫 (`DeepSentimentCrawling/MediaCrawler/main.py`)

底层爬虫引擎，支持搜索、详情、创作者三种采集模式。

```bash
cd DeepSentimentCrawling/MediaCrawler

# 搜索模式（默认）
python main.py --platform xhs --keywords "关键词1,关键词2"

# 详情模式
python main.py --platform dy --type detail

# 创作者模式
python main.py --platform bili --type creator

# 指定登录方式
python main.py --platform xhs --lt cookie --cookies "your_cookie_here"

# 采集评论
python main.py --platform xhs --get_comment yes --get_sub_comment yes

# 数据保存方式
python main.py --platform xhs --save_data_option db     # 存数据库
python main.py --platform xhs --save_data_option json   # 存JSON文件

# 初始化数据库表
python main.py --init_db mysql
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--platform` | enum | xhs | 平台 |
| `--lt` | enum | qrcode | 登录方式（qrcode/phone/cookie） |
| `--type` | enum | search | 采集类型（search/detail/creator） |
| `--start` | int | 配置文件 | 起始页码 |
| `--keywords` | string | 配置文件 | 搜索关键词（逗号分隔） |
| `--get_comment` | bool | 配置文件 | 是否采集一级评论 |
| `--get_sub_comment` | bool | 配置文件 | 是否采集二级评论 |
| `--save_data_option` | enum | json | 保存方式（csv/db/json/sqlite/postgresql） |
| `--init_db` | enum | — | 初始化数据库（sqlite/mysql/postgresql） |
| `--cookies` | string | — | Cookie 字符串 |

---

## 7. 数据库工具

```bash
# 初始化数据库表（SQLAlchemy）
python schema/init_database.py

# 数据库管理（状态、统计、清理）
python schema/db_manager.py
```

---

## 8. 代码质量

```bash
black .              # 格式化
ruff check .         # Lint
pytest tests/ -v     # 测试
```

---

## 服务器后台部署（nohup）

以下命令适用于 Linux 服务器长期运行，日志输出到 `logs/` 目录。

```bash
# 先确保在项目根目录，并激活环境
cd /path/to/mind-spider
conda activate mind-spider
mkdir -p logs
```

### 表层采集 — 常驻调度器

```bash
# 启动（全部分类）
nohup python BroadTopicExtraction/start_scheduler.py \
  > logs/broad_scheduler.log 2>&1 &

# 启动（指定分类）
nohup python BroadTopicExtraction/start_scheduler.py \
  --categories hot_national hot_vertical media \
  > logs/broad_scheduler.log 2>&1 &

# 记录 PID 方便后续管理
nohup python BroadTopicExtraction/start_scheduler.py \
  > logs/broad_scheduler.log 2>&1 &
echo $! > logs/broad_scheduler.pid
```

### 深层采集 — 常驻服务

```bash
# 启动（全部平台）
nohup python DeepSentimentCrawling/start_deep_crawl.py \
  > logs/deep_crawl.log 2>&1 &

# 启动（指定平台 + 端口）
nohup python DeepSentimentCrawling/start_deep_crawl.py \
  --platforms xhs,dy,bili --port 8080 \
  > logs/deep_crawl.log 2>&1 &

# 记录 PID
nohup python DeepSentimentCrawling/start_deep_crawl.py \
  > logs/deep_crawl.log 2>&1 &
echo $! > logs/deep_crawl.pid
```

### 完整流程 — 一站式常驻

```bash
nohup python main.py --complete \
  > logs/mind_spider.log 2>&1 &
echo $! > logs/mind_spider.pid
```

### 进程管理

```bash
# 查看运行中的 MindSpider 进程
ps aux | grep -E "(start_scheduler|start_deep_crawl|main.py)" | grep -v grep

# 实时查看日志
tail -f logs/broad_scheduler.log
tail -f logs/deep_crawl.log

# 停止服务（通过 PID 文件）
kill $(cat logs/broad_scheduler.pid)
kill $(cat logs/deep_crawl.pid)

# 停止服务（通过进程名）
pkill -f "start_scheduler.py"
pkill -f "start_deep_crawl.py"
```

---

## 平台代码速查

| 代码 | 平台 |
|------|------|
| `xhs` | 小红书 |
| `dy` | 抖音 |
| `ks` | 快手 |
| `bili` | B站 |
| `wb` | 微博 |
| `tieba` | 百度贴吧 |
| `zhihu` | 知乎 |

---

## 典型工作流

```bash
conda activate mind-spider
cd /path/to/mind-spider

# 1. 首次使用：初始化
python main.py --setup
python main.py --init-db

# 2. 日常测试：表层采集跑一轮
python BroadTopicExtraction/start_scheduler.py --once

# 3. 日常测试：基于话题深层采集
cd DeepSentimentCrawling && python main.py --platform xhs --max-notes 50

# 4. 生产部署：后台启动表层 + 深层
cd /path/to/mind-spider
nohup python BroadTopicExtraction/start_scheduler.py > logs/broad_scheduler.log 2>&1 &
nohup python DeepSentimentCrawling/start_deep_crawl.py > logs/deep_crawl.log 2>&1 &
```
