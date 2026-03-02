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

## 服务器部署（systemd）

> 服务器：`10.168.1.80`，部署路径：`/deploy/parallel-universe/mind-spider`
> Python 环境：`/root/anaconda3/envs/mind-spider/bin/python`

两个 systemd 服务均已配置为 `enabled`（开机自启）+ `Restart=always`（崩溃自动重启）。

### 服务一览

| 服务名 | 说明 | 日志路径 |
|--------|------|----------|
| `mindspider-broad-crawl` | 表层采集调度器（53+ 数据源） | `logs/broad_crawl.log` |
| `mindspider-deep-crawl` | 深层采集服务（7 平台 Playwright，端口 8777） | `logs/deep_crawl.log` |

### 常用命令

```bash
# 查看服务状态
systemctl status mindspider-broad-crawl
systemctl status mindspider-deep-crawl

# 启动 / 停止 / 重启
systemctl start mindspider-broad-crawl
systemctl stop mindspider-broad-crawl
systemctl restart mindspider-broad-crawl

systemctl start mindspider-deep-crawl
systemctl stop mindspider-deep-crawl
systemctl restart mindspider-deep-crawl

# 查看实时日志
journalctl -u mindspider-broad-crawl -f
journalctl -u mindspider-deep-crawl -f

# 或直接 tail 日志文件
tail -f /deploy/parallel-universe/mind-spider/logs/broad_crawl.log
tail -f /deploy/parallel-universe/mind-spider/logs/deep_crawl.log

# 查看服务配置
systemctl cat mindspider-broad-crawl
systemctl cat mindspider-deep-crawl
```

### 代码更新流程

```bash
# 本地
git push

# 服务器
cd /deploy/parallel-universe/mind-spider
git pull
systemctl restart mindspider-broad-crawl
systemctl restart mindspider-deep-crawl
```

### service 文件位置

- `/etc/systemd/system/mindspider-broad-crawl.service`
- `/etc/systemd/system/mindspider-deep-crawl.service`

修改 service 文件后需执行 `systemctl daemon-reload`。
