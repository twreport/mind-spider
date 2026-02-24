# 话题匹配 + 关键词扩展 设计讨论

## 问题背景

`POST /api/tasks` 接口存在两个问题：

1. **无去重** — 用户每次提交 `topic_title` 都创建新任务，即使系统已爬过语义相同的话题
2. **关键词单一** — `search_keywords` 默认等于 `[topic_title]`，社交媒体搜索会漏掉大量相关内容

需要在 API 层增加：话题匹配（避免重复爬取）+ 关键词扩展（提高爬取覆盖率）。

---

## 整体流程

```
POST /api/tasks { topic_title, ?search_keywords, ?force, ?platforms, ?max_notes }
      │
      ├─ force=true → 跳过匹配，直接进入关键词处理
      │
      ▼
 ① 精确去重：24h 内是否有相同 topic_title 的用户任务？→ 命中则返回已有任务信息
      │ 未命中
      ▼
 ② 候选匹配：jieba 预筛 + LLM 三分类判断
      │ duplicate → 返回 {"status": "matched", ...}
      │ development → 创建任务，关联已有 candidate
      │ different / None ↓
      ▼
 ③ 关键词处理：
      ├─ 用户已传 search_keywords → 直接使用，不调 LLM
      └─ 用户未传 → topic_title 作为第一个关键词
                   + LLM 生成最多 2 个补充关键词（总计最多 3 个）
      │
      ▼
 ④ 为每个平台创建爬取任务（同现有逻辑）
```

---

## 关键设计决策

1. **用户传了 search_keywords 就不生成** — 用户端可能更清楚自己需要什么
2. **topic_title 始终作为第一个 search_keyword** — 标题通常是最完整的线索
3. **LLM 最多补充 2 个关键词** — 总计最多 3 个，后续有 note 相关性过滤兜底
4. **`force` 键跳过匹配但不跳过关键词扩展** — force 是"我知道重复了但仍要爬"

---

## 三分类匹配（v2 优化）

初始版本采用二分类（匹配/不匹配），但实际存在三种场景：

| 类型 | 例子 | 正确行为 |
|---|---|---|
| **duplicate** | "王濛签生死状复出" vs "王濛说签生死状复出" | 返回已有数据，不爬 |
| **development** | "警方通报平顶山打人事件" vs "平顶山被打女孩半昏迷" | 需要爬，但关联到同一 candidate |
| **different** | 全新话题 | 新建任务 |

二分类会把 development 误判为 duplicate，导致新进展漏掉。v2 改为三分类：

```json
{
  "type": "duplicate|development|different",
  "matched_id": "candidate_id 或 null",
  "confidence": 0.0-1.0,
  "reason": "简短原因"
}
```

---

## LLM 策略

### 匹配 vs 关键词扩展分离

初始设计将两个任务合并成一次 LLM 调用，但存在问题：

1. **prompt 复杂度** — 匹配 + 扩展两个任务塞在一个 prompt 里，对 qwen-flash 等轻量模型不友好
2. **条件不适用** — 用户自带 search_keywords 时无需扩展，但仍要调匹配

v2 将两者拆分为独立调用：

- **匹配调用** — 输入简单（用户话题 + 候选列表），输出简单（三分类 + reason）
- **关键词调用** — 只在需要时触发（用户没传 `search_keywords` 且匹配结果不是 `duplicate`）

### LLM 容错

| 场景 | 行为 |
|------|------|
| 轻量 LLM API 不可用 | 匹配降级 jieba（overlap >= 0.6）；关键词不扩展，只用 `[topic_title]` |
| LLM 返回不可解析 | 同上 |
| MongoDB 查询失败 | 跳过匹配，正常建任务 |
| `force: true` | 跳过匹配，但仍做关键词扩展（如需要） |
| 用户传了 search_keywords | 不调 LLM 扩展，直接使用 |

---

## 实现文件

| 文件 | 作用 |
|------|------|
| `DeepSentimentCrawling/topic_matcher.py` | TopicMatcher 类（匹配 + 关键词扩展） |
| `DeepSentimentCrawling/login_console.py` | create_task() 插入匹配和关键词逻辑 |
| `DeepSentimentCrawling/start_deep_crawl.py` | 初始化 TopicMatcher |
| `ms_config.py` | 轻量 LLM 配置项 |

---

## API 响应格式

### 命中时（duplicate）HTTP 200

```json
{
  "status": "matched",
  "message": "该话题已有深度采集数据",
  "match": {
    "candidate_id": "cand_xxx",
    "canonical_title": "平顶山打人事件",
    "status": "tracking",
    "source_titles": ["警方通报平顶山打人事件", "平顶山被打女孩半昏迷"],
    "crawl_stats": {"total_tasks": 7, "completed": 7, "platforms": ["xhs","dy",...]},
    "match_method": "llm|jieba|exact",
    "confidence": 0.92,
    "reason": "用户话题与候选指向同一事件同一角度"
  }
}
```

### 事件进展（development）— 继续创建任务

HTTP 200，响应同正常创建：

```json
{
  "task_ids": ["ut_xhs_abc12345_1708000000", ...],
  "count": 7,
  "status": "ok",
  "search_keywords": ["平顶山被打女孩视力受损", "视力", "平顶山"]
}
```

但 task 的 `candidate_id` 会关联到已有候选而非 `user_api`。

### 未命中（different）— 正常创建

同 development。

---

## 验证方式

1. 提交已爬过话题的变体 → 验证匹配命中返回
2. 提交全新话题（不传 search_keywords）→ 验证 LLM 生成了补充关键词
3. 提交全新话题 + 自定义 search_keywords → 验证不调 LLM，直接使用
4. `force: true` + 已爬话题 → 验证跳过匹配但仍扩展关键词
5. 断开 LLM API → 验证 jieba fallback + 只用 topic_title 作为关键词

---

## asyncio vs threading 讨论

### 核心区别：谁来切换执行权

**threading.Thread** — 操作系统决定切换（抢占式）

```
线程A: ──运行──┃被OS挂起┃──────────┃恢复运行──
线程B: ────────┃恢复运行┃──运行────┃被OS挂起──
                ↑ OS 说了算，代码无法控制切换时机
```

**asyncio** — 代码自己决定切换（协作式）

```
协程A: ──运行──┃await──┃─────────────┃恢复运行──
协程B: ────────┃恢复───┃运行──await──┃
               ↑ 遇到 await 才让出，程序员控制切换点
```

### 对比

| | threading | asyncio |
|---|---|---|
| 并发单位 | OS 线程 | 协程（coroutine） |
| 切换方式 | OS 随时抢占 | 遇到 `await` 才切换 |
| 适合场景 | CPU 密集 / 阻塞型 C 库 | I/O 等待（网络、磁盘） |
| 资源开销 | 每线程 ~8MB 栈内存 | 每协程 ~几KB |
| 数据竞争 | 需要加锁（Lock/Queue） | 单线程运行，同步代码段内天然安全 |
| GIL 影响 | Python GIL 导致 CPU 密集型无法真并行 | 不受影响（本来就单线程） |

### 项目中的配合

```
start_deep_crawl.py
│
├─ 主线程（asyncio 事件循环）
│    └─ dispatcher.run()
│         └─ _dispatch_round()
│              ├─ asyncio.Task: xhs 任务   ← 遇到网络IO就await，让出
│              ├─ asyncio.Task: dy 任务    ← 同时可以执行
│              └─ asyncio.Task: bili 任务
│
└─ 后台 daemon 线程（threading.Thread）
     └─ uvicorn.run(login_app)  ← 阻塞调用，必须用线程隔离
```

**为什么 uvicorn 要用线程？** `uvicorn.Server.run()` 内部自带事件循环并且会阻塞。如果放在主线程，`dispatcher.run()` 就永远执行不到。

**为什么爬虫任务用 asyncio 而不是线程？** 爬取任务的耗时几乎全在等网络响应（HTTP 请求、Playwright 页面加载），asyncio 在 `await` 等待时自动切换到其他协程，一个线程就能同时推进 7 个平台的任务，既省内存又不需要处理线程间的锁竞争。
