# Phase 2 整体流程设计

## 背景

表层采集已完成，53 个数据源每 30 分钟向 MongoDB 写入数据。Phase 2 的目标是构建信号检测、候选话题管理、话题分析能力，并打通深层爬取触发，形成完整的舆情监测闭环。

系统采用能力化事件驱动架构（详见 HOTSPOT_METHODOLOGY.md），不是线性流水线。六个能力平等并行，通过多种触发源激活，通过反馈环形成闭环。

## 架构总图

```
┌──────────────────────────────────────────────────────────────────┐
│                         调度与编排层                               │
│            触发源: 定时 / 事件 / 客户 / 反馈 / 人工                │
└──┬──────┬──────┬──────┬──────┬──────┬────────────────────────────┘
   │      │      │      │      │      │
   ▼      ▼      ▼      ▼      ▼      ▼
┌──────┐┌──────┐┌──────┐┌──────┐┌──────┐┌──────┐
│ 表层 ││ 信号 ││ 候选 ││ 深层 ││ 话题 ││ 客户 │  ← 六个能力（平等并行）
│ 采集 ││ 检测 ││ 管理 ││ 采集 ││ 分析 ││ 过滤 │
│  ✅  ││      ││      ││      ││      ││      │
└──┬───┘└──┬───┘└──┬───┘└──┬───┘└──┬───┘└──┬───┘
   │       │       │       │       │       │
   │  ┌────┴───────┴───────┴───────┴───┐   │
   │  │         反馈环网络               │   │
   │  │  信号→候选（新信号纳入管理）      │   │
   │  │  候选→深层（状态变化触发爬取）    │   │
   │  │  深层→信号（水面下信号）          │   │
   │  │  深层→候选（预警验证/状态更新）   │   │
   │  │  分析→候选（LLM 研判升降级）     │   │
   │  │  分析→深层（指导补充爬取）        │   │
   │  │  指纹→信号（自适应阈值）          │   │
   │  └────────────────────────────────┘   │
   │                                       │
   └───────────┬───────────────────────────┘
               ▼
      ┌────────────────┐
      │   共享数据层     │
      │ MongoDB + MySQL │
      └────────────────┘
```

## 六个能力及其触发源

### 能力 1：表层采集（✅ 已完成）

从 53 个数据源采集热榜、媒体、聚合器数据，写入 MongoDB。

触发源：
- 定时（每 30 分钟，常规巡检）
- 客户（特定地方榜/垂直榜需要更高频采集）— Phase 3
- 事件（某候选话题需要追踪特定平台热榜变化）— 后续

### 能力 2：信号检测（本次实现）

纯算法（零 API 成本），从 MongoDB 原始数据中发现异动。只负责"发现"，不负责"决策"。

触发源：
- 定时（每 30 分钟，跟随表层采集）
- 反馈（深层采集发现异常讨论量 → 生成新信号）

两层检测：
- Layer 1 — 单 collection 内部：velocity / new_entry / position_jump
- Layer 2 — 跨 collection 交叉：cross_platform / authority_boost / media_lead / vertical_break

输出：
- `signals` collection — 原始信号记录

### 能力 3：候选话题管理（本次实现）

管理话题生命周期，做触发决策。接收信号检测的输出，决定话题状态流转，触发深层爬取。

触发源：
- 事件：收到新信号（信号检测产出）
- 事件：深层爬取完成（验证结果反馈）
- 事件：话题分析结果（LLM 研判升降级）
- 事件：聚类分析结果（话题合并/更新）
- 定时：周期性检查 tracking 状态话题（持续追踪）
- 定时：周期性清理衰退话题（faded/closed）

输出：
- `candidates` collection — 候选话题状态机
- 触发事件 → 深层采集（rising/confirmed/exploded/再次爬取）

### 能力 4：深层采集（对接触发）

在 7 个社交平台爬取详细内容（帖子、评论、讨论）。

触发源：
- 事件：候选话题 rising → 验证性爬取（1-2 个核心平台，少量数据）
- 事件：候选话题 confirmed → 全平台爬取（7 平台，大规模）
- 事件：候选话题 exploded → 最高频持续爬取（全平台，每小时增量）
- 事件：快速通道直接触发（极高热度/排位/权威媒体）
- 定时：tracking 话题持续追踪（增量抓取新内容）
- 客户：品牌关键词定时巡检 — Phase 3

输出：
- MySQL 各平台内容表 — 帖子/评论数据
- 反馈事件 → 信号检测（水面下信号）
- 反馈事件 → 候选话题管理（验证结果）

### 能力 5：话题分析（本次实现）

LLM 深度分析、语义聚类、趋势研判。

触发源：
- 定时（晨报 08:00 / 晚报 20:00）
- 事件：紧急信号（多个高优先级信号叠加 → 即时分析）
- 事件：深层爬取完成 → 话题聚类分析

输出：
- MySQL `daily_topics` — 结构化话题
- 聚类结果 — 相似话题合并、归类
- 反馈事件 → 候选话题管理（LLM 研判升降级）
- 反馈事件 → 深层采集（补充爬取新角度）

### 能力 6：客户过滤（Phase 3）

个性化相关性评分、推送。本次不实现，但数据模型预留扩展点。

## 候选话题生命周期

每个话题独立管理，不建立话题间的父子或图关系。话题间的关联在深层爬取后由聚类分析处理。

### 状态机

```
┌──────────────────────────────────────────────────────────┐
│                    快速通道（直接触发）                      │
│  极高热度 / 极高排位 / 权威媒体独家报道                      │
│  → 跳过 emerging/rising，直接进入 confirmed 或 exploded    │
└──────────────────────────┬───────────────────────────────┘
                           │
                           ▼
┌───────────┐  信号累积  ┌───────────┐  确认  ┌───────────┐  持续爆发  ┌───────────┐
│ emerging  │ ────────→ │  rising   │ ────→ │ confirmed │ ────────→ │ exploded  │
│  (观察中)  │           │  (上升中)  │       │  (已确认)  │           │  (爆发中)  │
└─────┬─────┘           └─────┬─────┘       └─────┬─────┘           └─────┬─────┘
      │                       │                     │                       │
      │ 无新信号               │ 无新信号              │                       │
      ▼                       ▼                     ▼                       ▼
┌───────────┐           ┌───────────┐        触发深层爬取            最高优先级爬取
│  faded    │           │  faded    │             │                 + 即时 LLM 分析
│ (衰退消失) │           │ (衰退消失) │             ▼                       │
└───────────┘           └───────────┘       ┌───────────┐                 │
                                            │ tracking  │ ←───────────────┘
                                            │ (追踪中)   │   (爆发减弱后降级)
                                            └─────┬─────┘
                                                  │
                                        ┌─────────┼─────────┐
                                        ▼         ▼         ▼
                                   热度再升   自然衰退   聚类合并
                                   → 再次爬取  → closed  → 更新话题
                                   (可能重回     ┌────────┐
                                    exploded)    │ closed │
                                        │       │(已关闭) │
                                        │       └────────┘
                                        ▼
                                   回到 tracking
```

### 状态说明

| 状态 | 含义 | 进入条件 | 可触发的动作 |
|------|------|---------|------------|
| emerging | 刚发现，观察中 | 首次检测到信号 | 无 |
| rising | 多信号确认，正在上升 | ≥2 种不同信号，或跨平台 ≥2 | 触发验证性深层爬取 |
| confirmed | 确认为热点 | 跨平台 ≥3，或 LLM 确认，或快速通道 | 触发全平台深层爬取 |
| exploded | 爆发性重大事件 | confirmed 后持续爆发增长，或快速通道极端情况 | 最高频深层爬取 + 即时 LLM 分析 + 优先客户推送 |
| tracking | 已爬取，持续追踪 | 深层爬取完成且爆发减弱 | 定时增量爬取 |
| faded | 衰退消失 | 连续 N 个周期无新信号且热度下降 | 提取传播指纹入库 |
| closed | 生命周期结束 | tracking 状态下长时间无新活动 | 提取传播指纹入库 |

### exploded 与 confirmed 的区别

| 维度 | confirmed | exploded |
|------|-----------|----------|
| 深层爬取频率 | 触发一次全平台爬取 | 最高频持续爬取（如每小时增量） |
| LLM 分析 | 等晨报/晚报，或紧急信号触发 | 立即触发即时分析，持续更新研判 |
| 客户推送 | 常规推送 | 最高优先级紧急推送 |
| 监测力度 | 标准 | 最大化（更多平台、更高频率、更深内容） |

### 快速通道规则

满足以下条件，直接进入 confirmed：
- hot_national 中 position ≤ 10 且 hot_value 较高
- 同时出现在 ≥ 3 个平台
- 央媒（新华社/央视/人民日报）报道且社交平台热度正在上升

满足以下极端条件，直接进入 exploded：
- hot_national 中 position ≤ 1 且 hot_value 极高，同时跨平台 ≥ 4
- 多个央媒同时报道 + 社交平台多平台同时爆发
- confirmed 后 30 分钟内热度仍在加速增长且跨平台数持续增加

### 持续追踪机制

confirmed/exploded → tracking 后（爆发减弱时降级）：
- 定时检查话题是否仍在榜
- 如果热度再次飙升（velocity 信号）→ 再次触发深层爬取（极端情况可重回 exploded）
- 如果出现新的关联信号（如官方回应导致二次峰值）→ 再次爬取
- 如果长时间无新活动 → 进入 closed，提取传播指纹

## 话题聚类分析

**时机**：深层爬取完成后，作为话题分析能力的一部分

**为什么放在深爬之后**：
- 前面的环节只有标题信息，靠标题做关联容易误判
- 深爬后有实际的帖子内容、评论、情感倾向，聚类准确度最高
- 保持前面环节的简单性——每个话题独立走流程

**实现方式**：算法初筛 + LLM 精细研判
1. 算法初筛：关键词重叠、TF-IDF 相似度，对话题做粗分组
2. LLM 精筛：将每组话题及其爬取内容摘要交给 LLM，判断合并、归类、研判

**聚类结果的反馈**：
- 需要合并的话题 → 反馈给候选话题管理，合并为同一话题
- 发现新角度需要补充爬取 → 反馈触发深层采集
- 研判结论 → 写入 daily_topics

## 反馈环

```
反馈环 1：信号检测 → 候选话题管理
  新信号产出 → 候选话题纳入管理或状态升级

反馈环 2：候选话题管理 → 深层采集
  状态变化（rising/confirmed/exploded/再次爬取）→ 触发深层爬取

反馈环 3：深层采集 → 信号检测
  深层爬取发现大量讨论但话题未上热搜（水面下信号）
  → 创建新信号

反馈环 4：深层采集 → 候选话题管理
  用实际讨论量和情绪数据验证候选话题的爆发潜力
  → 加速或降级候选话题状态

反馈环 5：话题分析 → 候选话题管理
  LLM 研判某话题应升级或降级
  → 更新候选话题状态

反馈环 6：话题分析 → 深层采集
  LLM 判断某话题可能有二次爆发风险，或发现新角度
  → 触发补充爬取

反馈环 7：指纹库 → 信号检测（后续实现）
  历史传播模式积累后，动态调整检测阈值
  → 系统越跑越准
```

## 典型路径

系统不是单一路径，以下是几种典型的话题发现和处理路径：

### 路径 A：常规热点（最常见）

```
表层采集发现新话题
  → 信号检测：new_entry 信号
  → 候选管理：纳入 emerging
  → 信号检测：更多信号累积（velocity / cross_platform）
  → 候选管理：升级 rising
  → 候选管理 → 深层采集：验证性爬取
  → 深层采集 → 候选管理：验证通过
  → 候选管理：升级 confirmed → 全平台爬取
  → 话题分析：聚类 + 研判
  → 候选管理：进入 tracking
  → 持续追踪或关闭
```

### 路径 B：快速通道（突发重大事件）

```
表层采集发现极高热度话题
  → 信号检测：满足快速通道条件
  → 候选管理：直接 confirmed 或 exploded（跳过 emerging/rising）
  → 候选管理 → 深层采集：立即全平台爬取
  → 话题分析：紧急分析（不等晨报/晚报）
  → 输出报告 + 客户推送
  → 候选管理：进入 tracking，持续追踪
```

### 路径 C：持续发酵（多轮爬取 + 可能升级 exploded）

```
话题已 confirmed 并完成首轮深层爬取
  → 候选管理：进入 tracking
  → 信号检测：话题仍在榜，热度再次上升
  → 候选管理：再次触发深层爬取（增量）
  → 如果热度持续爆发性增长 → 候选管理：升级 exploded
  → exploded：最高频爬取 + 即时 LLM 分析
  → 爆发减弱 → 降回 tracking
  → 话题分析：聚类发现新角度
  → 话题分析 → 深层采集：补充爬取新角度
  → 多轮循环直到话题衰退
  → 候选管理：closed，提取传播指纹
```

### 路径 D：权威媒体领先（早期预警）

```
表层采集：央媒发布重大报道，社交平台尚未上榜
  → 信号检测：media_lead 信号
  → 候选管理：纳入 emerging
  → 表层采集：后续社交平台开始上榜
  → 信号检测：cross_platform + authority_boost
  → 候选管理：快速升级到 confirmed
  → 候选管理 → 深层采集：全平台爬取
```

### 路径 E：垂直破圈

```
表层采集：垂直社区（如掘金/36氪）出现热门话题
  → 信号检测：垂直榜内部 velocity
  → 候选管理：纳入 emerging
  → 表层采集：后续该话题出现在全国热搜
  → 信号检测：vertical_break
  → 候选管理：升级
  → 候选管理 → 深层采集
```

### 路径 F：深层采集反馈发现（Phase 3，品牌客户场景）

```
深层采集：品牌关键词定时巡检
  → 发现异常讨论量（话题未上任何热搜）
  → 深层采集 → 信号检测：反馈环 3，创建新信号
  → 信号检测 → 候选管理：反馈环 1，纳入管理
  → 后续可能上榜，也可能不上榜
  → 无论是否上榜，客户都已提前获得预警
```

## 数据存储

| 存储 | Collection/Table | 用途 |
|------|-----------------|------|
| MongoDB | `hot_national` / `aggregator` / `hot_vertical` / `media` / `hot_local` | 表层采集原始数据（已有） |
| MongoDB | `signals` | 信号记录（信号检测能力的输出） |
| MongoDB | `candidates` | 候选话题库（候选话题管理能力的核心数据） |
| MongoDB | `fingerprints` | 传播指纹库（历史话题模式，后续实现） |
| MySQL | `daily_topics` | 结构化话题输出（已有表结构） |
| MySQL | `crawling_tasks` | 深层爬取任务记录（已有表结构） |
| MySQL | 各平台内容表 | 深层爬取的帖子/评论数据（已有表结构） |

## 实现顺序

按依赖关系，分三个阶段：

### 阶段一：信号检测 + 候选管理核心闭环（本次重点）

| Step | 模块 | 对应能力 | 职责 |
|------|------|---------|------|
| 1 | `analyzer/data_reader.py` | 共享基础 | MongoDB 数据读取与预处理 |
| 2 | `analyzer/signal_detector.py` | 能力 2：信号检测 | 7 种信号检测算法，输出到 signals |
| 3 | `analyzer/candidate_manager.py` | 能力 3：候选管理 | 话题状态机、快速通道、持续追踪 |
| 4 | 调度器集成 | 编排层 | 注册定时任务 + 事件触发 |

阶段一完成后，系统可以自动发现信号、管理候选话题，但尚未触发深层爬取。

### 阶段二：打通深层爬取 + LLM 分析

| Step | 模块 | 对应能力 | 职责 |
|------|------|---------|------|
| 5 | 深层爬取触发对接 | 能力 4：深层采集 | 候选管理状态变化 → DeepSentimentCrawling |
| 6 | `analyzer/topic_analyzer.py` | 能力 5：话题分析 | LLM 话题分析（晨报/晚报 + 紧急分析） |
| 7 | `analyzer/cluster_analyzer.py` | 能力 5：话题分析 | 深爬后话题聚类（算法初筛 + LLM 研判） |

### 阶段三：反馈环 + 指纹库

| Step | 模块 | 职责 |
|------|------|------|
| 8 | 反馈环实现 | 打通反馈环 3-7 |
| 9 | `analyzer/fingerprint.py` | 传播指纹提取与存储 |
| 10 | 自适应阈值 | 指纹库驱动信号检测阈值动态调整 |

---

## 附录 A：信号检测算法详细规格

### Collection 角色分工

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
```

### 信号类型汇总

| signal_type | layer | 含义 | 触发条件 |
|------------|-------|------|---------|
| velocity | 1 | 热度飙升 | 增长率 > 50% |
| new_entry | 1 | 新上榜高位 | 首次出现即高热度/高排名 |
| position_jump | 1 | 排名跃升 | 排名上升 ≥ 10 位 |
| cross_platform | 2 | 跨平台共振 | ≥ 3 个平台同时在榜 |
| authority_boost | 2 | 权威背书 | 央媒报道 + 社交媒体在榜 |
| media_lead | 2 | 媒体先发 | 央媒报道但社交媒体尚未上榜 |
| vertical_break | 2 | 垂直破圈 | 行业话题进入全国热搜 |

## 附录 B：MongoDB Document Schema

### signals collection

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

### candidates collection

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

### fingerprints collection

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
    "timeline": []
  }
}
```

## 附录 C：LLM Prompt 策略（话题分析参考）

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

## 附录 D：数据预处理评分公式

```
base_score = (platform_count * 30) + (max_hot_value / 10000) + (50 - avg_position)

加权调整:
- authority_boost 信号命中 → score * 1.5
- vertical_break 信号命中 → score * 1.3
- media_lead 信号命中 → score + 50（尚未上榜但央媒已报道）
```

## 附录 E：关键依赖

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

## 附录 F：验证方案

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
