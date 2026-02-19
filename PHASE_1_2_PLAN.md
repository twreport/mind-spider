# 信号检测 + 话题分析：实现计划

## Context

表层采集能力已完成，53 个数据源稳定运行，MongoDB 中持续积累数据。现在需要构建**信号检测**和**话题分析**两个能力——从 MongoDB 原始数据中识别和分析热点话题，输出结构化结果，并为深层采集提供触发信号。

系统采用能力化事件驱动架构（详见 HOTSPOT_METHODOLOGY.md），本计划涉及的两个能力：
1. **信号检测**（硬编码，每 30 分钟）：纯算法，零 API 成本，实时发现异动，维护早期预警库
2. **话题分析**（LLM，晨报 + 晚报）：深度分析，聚类归纳，生成结构化报告

这两个能力的输出同时服务于：
- 深层采集能力（触发验证和全平台爬取）
- 客户过滤能力（提供热点数据供个性化筛选）
- 传播指纹库（积累历史数据供趋势研判）

## 一、架构总览

```
MongoDB (mindspider_raw)
    │
    ├── 每 30 分钟 ──→ SignalDetector (硬编码)
    │                   │
    │                   ├── Layer 1: 单 collection 内部检测
    │                   │   ├── hot_national → velocity / new_entry / position_jump
    │                   │   └── hot_vertical → 同上（独立阈值）
    │                   │
    │                   ├── Layer 2: 跨 collection 交叉检测
    │                   │   ├── hot_national 内部 → cross_platform（跨平台共振）
    │                   │   ├── media × hot_national → authority_boost / media_lead
    │                   │   └── hot_vertical × hot_national → vertical_break
    │                   │
    │                   ├──→ MongoDB: signals collection
    │                   └──→ MongoDB: candidates collection（早期预警库）
    │                         │
    │                         ├── status: emerging → 可触发深层采集验证
    │                         ├── status: rising → 触发深层采集验证
    │                         └── status: confirmed → 触发深层采集全平台爬取
    │
    └── 每天 8:00 / 20:00 ──→ TopicAnalyzer (LLM)
                                ├── 读取 candidates（rising + emerging）
                                ├── 读取 hot_national + aggregator（去重合并）
                                ├── 读取 hot_vertical（独立分组）
                                ├── 读取 media（权威背书匹配）
                                ├── 读取 signals 信号记录
                                ├── 分层预处理 + 评分排序
                                ├── 调用 DeepSeek API 分析
                                ├── 写入 MySQL daily_topics
                                └── 触发深层采集（confirmed 话题）
```

注：深层采集（DeepSentimentCrawling）由调度编排层根据候选话题状态变化触发，不在本模块内实现，但本模块需要输出正确的触发信号。

## 二、新增文件

```
BroadTopicExtraction/
├── analyzer/
│   ├── __init__.py
│   ├── signal_detector.py    # 信号检测：7 种信号算法 + 写入 signals collection
│   ├── candidate_manager.py  # 早期预警库：候选话题状态机 + 写入 candidates collection
│   ├── topic_analyzer.py     # 话题分析：LLM 调用 + 结果解析 + MySQL 写入
│   └── data_reader.py        # MongoDB 数据读取与分层预处理
```

修改的现有文件：
- `scheduler/scheduler.py` — 注册信号检测和话题分析的定时任务
- `start_scheduler.py` — 集成新任务

## 三、Tier 1：信号检测模块 (signal_detector.py)

### 运行频率
每 30 分钟，跟随爬取周期，由调度器触发。

### 设计原则：分层处理，交叉验证

四个 MongoDB collection 数据性质不同，扮演不同角色：

| Collection | 角色 | 数据特征 | 处理方式 |
|-----------|------|---------|---------|
| hot_national | **主信号源** | 有 hot_value_history、position_history 时间序列 | 完整四种算法 |
| hot_vertical | **领域信号源** | 同上，但热度基线不同（科技/财经独立） | 同四种算法，阈值独立 |
| media | **权威背书源** | 静态文章，无 hot_value | 标题匹配，不做热度计算 |
| aggregator | **交叉验证源** | 镜像数据，与 hot_national 重叠 | 不独立分析，仅补充去重 |

### Layer 1: 单 collection 内部检测

#### 1. 热度飙升检测 (velocity) — 适用: hot_national, hot_vertical
```python
# 从 hot_value_history 取最近两个快照
# 计算增长率 = (current - previous) / previous
# hot_national 阈值: growth_rate > 0.5 且 hot_value > 10000
# hot_vertical 阈值: growth_rate > 0.5 且 hot_value > 5000（垂直领域基线更低）
```

#### 2. 新上榜检测 (new_entry) — 适用: hot_national, hot_vertical
```python
# 查询 first_seen_at 在最近 30 分钟内的文档
# hot_national: hot_value > 50000 或 position <= 10
# hot_vertical: hot_value > 10000 或 position <= 5（垂直榜单更短）
```

#### 3. 排名跃升检测 (position_jump) — 适用: hot_national, hot_vertical
```python
# 从 position_history 取最近两个快照
# 计算排名变化 = previous_position - current_position
# 阈值: jump >= 10 (排名上升 10 位以上)
```

### Layer 2: 跨 collection 交叉检测

#### 4. 跨平台共振检测 (cross_platform) — 跨 hot_national 内部多平台
```python
# 查询最近 1 小时内 hot_national 中所有 platform 的 title
# 用模糊匹配 (去除标点后完全匹配 或 包含关系) 找到同一话题
# 阈值: 出现在 >= 3 个不同 platform 上
```

#### 5. 权威背书检测 (authority_boost) — media × hot_national
```python
# 查询最近 12 小时 media collection 的标题
# 与 hot_national 当前在榜话题做标题匹配
# 匹配成功 → 生成 authority_boost 信号，附带媒体来源（新华社/央视/人民日报等）
# 权威媒体独家报道但社交媒体尚未上榜 → 生成 media_lead 信号（早期预警）
```

#### 6. 垂直破圈检测 (vertical_break) — hot_vertical × hot_national
```python
# 查询 hot_vertical 中的话题标题
# 与 hot_national 当前在榜话题做标题匹配
# 匹配成功 → 生成 vertical_break 信号（行业话题进入全国视野）
# 这类话题通常有更深的分析价值
```

### 信号存储

写入 MongoDB `signals` collection：

```json
// Layer 1 信号示例（单 collection 内部）
{
  "signal_id": "velocity_abc123_1707700000",
  "signal_type": "velocity",
  "layer": 1,
  "title": "某某事件",
  "platform": "weibo",
  "source_collection": "hot_national",
  "detected_at": 1707700000,
  "details": {
    "previous_value": 100000,
    "current_value": 200000,
    "growth_rate": 1.0
  },
  "consumed": false
}

// Layer 2 信号示例（跨 collection 交叉）
{
  "signal_id": "authority_boost_xyz_1707700000",
  "signal_type": "authority_boost",
  "layer": 2,
  "title": "某某事件",
  "platforms": ["weibo", "baidu"],
  "media_sources": ["xinhua", "cctv"],
  "source_collection": "cross",
  "detected_at": 1707700000,
  "details": {
    "hot_national_count": 2,
    "media_count": 2,
    "media_names": ["新华社", "央视新闻"]
  },
  "consumed": false
}
```

信号类型汇总：
| signal_type | layer | 含义 | 触发条件 |
|------------|-------|------|---------|
| velocity | 1 | 热度飙升 | 增长率 > 50% |
| new_entry | 1 | 新上榜高位 | 首次出现即高热度/高排名 |
| position_jump | 1 | 排名跃升 | 排名上升 ≥ 10 位 |
| cross_platform | 2 | 跨平台共振 | ≥ 3 个平台同时在榜 |
| authority_boost | 2 | 权威背书 | 央媒报道 + 社交媒体在榜 |
| media_lead | 2 | 媒体先发 | 央媒报道但社交媒体尚未上榜 |
| vertical_break | 2 | 垂直破圈 | 行业话题进入全国热搜 |

`consumed` 字段标记该信号是否已被 LLM 分析消费过。

## 四、Tier 2：LLM 分析模块 (topic_analyzer.py)

### 运行频率
每天 2 次，cron 触发：
- 晨报：08:00（分析前一天 20:00 到今天 08:00 的数据）
- 晚报：20:00（分析今天 08:00 到 20:00 的数据）

### 数据预处理流程 (data_reader.py)

```
1. 分 collection 读取时间窗口内的数据

   hot_national（主信号源）:
   - 读取全部字段，包括 hot_value_history、position_history
   - 这是候选话题的主要来源

   hot_vertical（领域信号源）:
   - 按 vertical 字段分组（tech / finance / entertainment）
   - 读取全部字段，阈值独立于 hot_national

   media（权威背书源）:
   - 只读取 title、source、platform、published_at
   - 不参与热度排名，仅用于交叉匹配

   aggregator（交叉验证源）:
   - 用于补充 hot_national 的覆盖盲区
   - 与 hot_national 去重后，仅保留 hot_national 中没有的条目

2. 跨平台标题聚合（仅 hot_national + 去重后的 aggregator）
   - 按标题相似度分组（去标点后精确匹配）
   - 计算每个话题的: 出现平台数、最高热度、平均排名

3. 综合评分排序
   base_score = (platform_count * 30) + (max_hot_value / 10000) + (50 - avg_position)

   加权调整:
   - authority_boost 信号命中 → score * 1.5
   - vertical_break 信号命中 → score * 1.3
   - media_lead 信号命中 → score + 50（尚未上榜但央媒已报道）

4. 取 Top 150 候选话题

5. 附加 hot_vertical 中未与 hot_national 重叠的高热度话题（Top 30）

6. 附加 media 中未匹配到任何热搜的央媒独家报道（作为"潜在热点"标记）

7. 读取未消费的 signals，附加到候选列表
```

### LLM Prompt 策略

#### 话题聚类与筛选

```
你是一个专业的中文新闻分析师。以下是过去 12 小时从多个平台采集的热点候选列表。

## 候选话题（共 {N} 条）

### 全国热搜话题
格式: [序号] 标题 | 平台数:{n} | 热度:{value} | 来源:{platforms}
{national_list}

### 行业热点话题
格式: [序号] 标题 | 领域:{vertical} | 热度:{value} | 来源:{platforms}
{vertical_list}

### 央媒关注话题（可能尚未成为社交媒体热点）
格式: [序号] 标题 | 来源:{media_name}
{media_list}

## 异动信号
以下话题被算法检测到异常：
{signals_list}

## 任务
1. 将相似话题合并（不同平台对同一事件的不同表述视为同一话题）
2. 从中筛选出最重要的 50 个独立话题
3. 对每个话题输出:
   - name: 简洁话题名（5-15字）
   - description: 话题描述（50-100字，说明事件背景和当前进展）
   - keywords: 3-5个搜索关键词（用于后续在社交平台搜索相关内容）
   - category: 分类（时政/科技/财经/娱乐/体育/社会/国际/军事）
   - importance: 重要性评分 1-100
   - source_titles: 原始标题列表（用于关联原始数据）

输出严格 JSON 格式:
{"topics": [{"name": "...", "description": "...", "keywords": [...], "category": "...", "importance": 85, "source_titles": [...]}]}
```

### 结果写入

写入 MySQL `daily_topics` 表：
- `topic_id`: `topic_{date}_{index}_{hash}` 格式
- `topic_name`: LLM 输出的 name
- `topic_description`: LLM 输出的 description
- `keywords`: JSON 数组，LLM 输出的 keywords
- `relevance_score`: importance / 100.0
- `news_count`: source_titles 的数量
- `extract_date`: 当天日期
- `processing_status`: "completed"

同时写入 `topic_news_relation` 表，通过 source_titles 匹配回 MongoDB 原始数据的 item_id。

标记 signals collection 中已消费的信号 `consumed: true`。

## 五、调度器集成

在 `scheduler.py` 中新增任务：

```python
# 信号检测 + 早期预警库更新，每 30 分钟
scheduler.add_job(signal_detector.detect_and_update_candidates, IntervalTrigger(minutes=30), id="signal_detector")

# 话题分析：LLM 晨报
scheduler.add_job(topic_analyzer.run_morning, CronTrigger(hour=8, minute=0), id="topic_morning")

# 话题分析：LLM 晚报
scheduler.add_job(topic_analyzer.run_evening, CronTrigger(hour=20, minute=0), id="topic_evening")
```

信号检测的执行流程：
1. 运行 7 种信号检测算法 → 写入 signals collection
2. 将新信号关联到 candidates collection（创建新候选或更新已有候选）
3. 评估候选话题状态转换（emerging → rising → confirmed / faded）
4. confirmed 状态的话题输出触发信号（供调度编排层触发深层采集）

## 五.1、早期预警库 (candidate_manager.py)

### candidates collection 结构

```json
{
  "candidate_id": "cand_abc123",
  "canonical_title": "某某事件",
  "source_titles": ["标题变体1", "标题变体2"],
  "status": "rising",
  "first_seen_at": 1707700000,
  "first_platform": "weibo",
  "signal_ids": ["velocity_abc_1707700000", "cross_platform_abc_1707701800"],
  "signal_types": ["velocity", "cross_platform"],
  "platform_count": 3,
  "max_hot_value": 5000000,
  "hot_value_snapshots": [{"ts": 1707700000, "value": 50000}, {"ts": 1707701800, "value": 500000}],
  "updated_at": 1707701800,
  "consecutive_quiet_cycles": 0
}
```

### 状态转换规则（硬编码）

- **emerging → rising**：累积 ≥ 2 个不同类型的信号，或跨平台数 ≥ 2
- **rising → confirmed**：跨平台数 ≥ 3，或被 LLM 晨报/晚报确认
- **任意状态 → faded**：连续 3 个检测周期（90 分钟）无新信号且热度下降

### 指纹数据采集

候选话题 status 变为 confirmed 或 faded 后，自动提取传播指纹写入 fingerprints collection：

```json
{
  "topic_key": "某某事件",
  "category": "社会",
  "outcome": "confirmed",
  "fingerprint": {
    "first_seen_at": 1707700000,
    "first_platform": "weibo",
    "peak_hot_value": 5000000,
    "peak_platform_count": 5,
    "total_duration_hours": 48,
    "time_to_cross_platform": 1.5,
    "timeline": [...]
  }
}
```

## 六、关键依赖（已有）

| 依赖 | 用途 | 状态 |
|------|------|------|
| `pymongo` | 读取 MongoDB | ✅ 已安装 |
| `openai` | 调用 DeepSeek API | ✅ 已安装 |
| `sqlalchemy` | 写入 MySQL | ✅ 已安装 |
| `tenacity` | API 重试 | ✅ 已安装 |
| `config.settings` | 读取 API Key 等配置 | ✅ 已有 |
| `MongoWriter` | MongoDB 读写 | ✅ 已有 |
| `DatabaseManager` | MySQL 读写 | ✅ 已有 |

无需新增任何依赖。

## 七、实现步骤

1. 创建 `analyzer/data_reader.py` — MongoDB 数据读取、分 collection 处理、跨平台聚合、评分排序
2. 创建 `analyzer/signal_detector.py` — 7 种信号检测算法（3 种内部 + 4 种交叉）+ 写入 signals collection
3. 创建 `analyzer/candidate_manager.py` — 早期预警库状态机 + 语义匹配（硬编码三级）+ 指纹数据采集
4. 创建 `analyzer/topic_analyzer.py` — LLM 调用 + 结果解析 + MySQL 写入 + 标记 confirmed
5. 修改 `scheduler/scheduler.py` — 注册信号检测和话题分析任务
6. 修改 `start_scheduler.py` — 集成新任务到启动流程
7. 为 signals、candidates、fingerprints collection 创建 MongoDB 索引

## 八、验证方案

```bash
# 1. 测试信号检测（单次执行）
uv run python -c "
from BroadTopicExtraction.analyzer.signal_detector import SignalDetector
detector = SignalDetector()
signals = detector.detect()
print(f'检测到 {len(signals)} 个信号')
for s in signals[:5]:
    print(f'  [{s[\"signal_type\"]}] {s[\"title\"]} ({s[\"platform\"]})')
"

# 2. 测试数据预处理
uv run python -c "
from BroadTopicExtraction.analyzer.data_reader import DataReader
reader = DataReader()
candidates = reader.get_candidates(hours=12)
print(f'候选话题: {len(candidates)} 个')
for c in candidates[:10]:
    print(f'  {c[\"title\"]} | 平台数:{c[\"platform_count\"]} | 热度:{c[\"max_hot_value\"]}')
"

# 3. 测试早期预警库
uv run python -c "
from BroadTopicExtraction.analyzer.candidate_manager import CandidateManager
manager = CandidateManager()
stats = manager.get_stats()
print(f'候选话题统计: {stats}')
"

# 4. 测试 LLM 分析（单次执行）
uv run python -c "
import asyncio
from BroadTopicExtraction.analyzer.topic_analyzer import TopicAnalyzer
analyzer = TopicAnalyzer()
asyncio.run(analyzer.run_analysis(report_type='evening'))
"

# 5. 检查 MySQL 结果
# 用数据库工具查看 daily_topics 表是否有新记录

# 6. 检查 MongoDB 早期预警库
# 用 Compass 查看 candidates collection 的状态分布

# 7. 启动完整调度器验证
uv run python BroadTopicExtraction/start_scheduler.py
# 观察信号检测日志、候选话题状态变化、LLM 分析日志
```
