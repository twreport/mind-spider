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

## 1. 表层采集调度器 (`BroadTopicExtraction/start_scheduler.py`)

24/7 常驻调度，管理 53+ 数据源的定时采集，结果存入 MongoDB。

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

## 2. 深层采集常驻服务 (`DeepSentimentCrawling/start_deep_crawl.py`)

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

**平台代码：**

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

## 服务器后台部署（nohup）

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
echo $! > logs/broad_scheduler.pid

# 启动（指定分类）
nohup python BroadTopicExtraction/start_scheduler.py \
  --categories hot_national hot_vertical media \
  > logs/broad_scheduler.log 2>&1 &
echo $! > logs/broad_scheduler.pid
```

### 深层采集 — 常驻服务

```bash
# 启动（全部平台）
nohup python DeepSentimentCrawling/start_deep_crawl.py \
  > logs/deep_crawl.log 2>&1 &
echo $! > logs/deep_crawl.pid

# 启动（指定平台 + 端口）
nohup python DeepSentimentCrawling/start_deep_crawl.py \
  --platforms xhs,dy,bili --port 8080 \
  > logs/deep_crawl.log 2>&1 &
echo $! > logs/deep_crawl.pid
```

### 进程管理

```bash
# 查看运行中的进程
ps aux | grep -E "(start_scheduler|start_deep_crawl)" | grep -v grep

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
