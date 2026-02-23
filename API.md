# MindSpider API 文档

服务地址: `http://<host>:8777`（由 `start_deep_crawl.py --port 8777` 启动）

所有接口均需传 `token` 查询参数（对应 `.env` 中的 `LOGIN_CONSOLE_TOKEN`，未配置则不校验）。

---

## 深层爬取任务

### POST `/api/tasks` — 创建任务

提交用户深层爬取任务，写入 MongoDB + 推送 Redis 队列，dispatcher 下一轮轮询自动执行。

**请求:**

```bash
# 全平台爬取（不传 platforms 则 7 个平台各生成一个任务）
curl -X POST "http://localhost:8777/api/tasks?token=xxx" \
  -H "Content-Type: application/json" \
  -d '{"topic_title": "新能源汽车舆情"}'

# 指定平台 + 自定义关键词
curl -X POST "http://localhost:8777/api/tasks?token=xxx" \
  -H "Content-Type: application/json" \
  -d '{
    "topic_title": "新能源汽车舆情",
    "platforms": ["wb", "bili"],
    "search_keywords": ["新能源汽车", "电动车"],
    "max_notes": 100
  }'
```

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `topic_title` | string | **是** | 话题标题 |
| `platforms` | string[] | 否 | 平台代码数组: `xhs` `dy` `bili` `wb` `ks` `tieba` `zhihu`。不传则全部 7 个平台 |
| `search_keywords` | string[] | 否 | 搜索关键词数组。不传则等于 `[topic_title]` |
| `max_notes` | int | 否 | 每个平台最大采集数量，默认 50 |

**响应:**

```json
{
  "task_ids": [
    "ut_bili_a1b2c3d4_1740000000",
    "ut_dy_e5f6a7b8_1740000000",
    "ut_ks_c9d0e1f2_1740000000",
    "..."
  ],
  "count": 7,
  "status": "ok"
}
```

---

### GET `/api/tasks` — 列出任务

按条件查询任务列表，按创建时间倒序。

**请求:**

```bash
# 列出所有任务（默认最多 50 条）
curl "http://localhost:8777/api/tasks?token=xxx"

# 按平台 + 状态过滤
curl "http://localhost:8777/api/tasks?token=xxx&platform=xhs&status=completed&limit=20"
```

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `platform` | string | 否 | 按平台过滤 |
| `status` | string | 否 | 按状态过滤: `pending` `running` `completed` `failed` `cancelled` |
| `limit` | int | 否 | 返回条数，1-200，默认 50 |

**响应:**

```json
{
  "total": 2,
  "tasks": [
    {
      "task_id": "ut_xhs_a1b2c3d4_1740000000",
      "platform": "xhs",
      "search_keywords": ["新能源汽车"],
      "status": "completed",
      "created_at": 1740000000,
      "priority": 100,
      ...
    }
  ]
}
```

---

### GET `/api/tasks/{task_id}` — 查询单个任务

**请求:**

```bash
curl "http://localhost:8777/api/tasks/ut_xhs_a1b2c3d4_1740000000?token=xxx"
```

**响应:** 返回完整任务文档（MongoDB crawl_tasks）。

**错误:** `404` 任务不存在。

---

### DELETE `/api/tasks/{task_id}` — 取消任务

只能取消 `pending` 状态的任务，同时从 Redis 队列移除。

**请求:**

```bash
curl -X DELETE "http://localhost:8777/api/tasks/ut_xhs_a1b2c3d4_1740000000?token=xxx"
```

**响应:**

```json
{
  "task_id": "ut_xhs_a1b2c3d4_1740000000",
  "status": "cancelled"
}
```

**错误:** `404` 任务不存在 | `409` 任务非 pending 状态。

---

## 登录控制台

### GET `/` — 仪表盘

显示所有平台 cookie 状态的 HTML 页面。

```
http://localhost:8777/?token=xxx
```

---

### GET `/login/{platform}` — 登录页

平台扫码登录页面（HTML），支持扫码登录和手动粘贴 Cookie 两种方式。

```
http://localhost:8777/login/xhs?token=xxx
```

`platform`: `xhs` `dy` `bili` `wb` `ks` `tieba` `zhihu`

---

### GET `/login/{platform}/qr` — 获取二维码

启动 Playwright 浏览器，导航到平台登录页，截取二维码图片返回 base64。

**响应:**

```json
{
  "qr_base64": "iVBORw0KGgo..."
}
```

---

### GET `/login/{platform}/poll` — 轮询登录状态

前端每 2s 调用一次，检测 cookie 中是否出现登录态。超时 5 分钟自动关闭会话。

**响应:**

```json
{"status": "waiting"}
{"status": "success"}
{"status": "error", "message": "登录超时"}
```

---

### GET `/login/{platform}/confirm` — 确认已扫码

用户手动点击"我已扫码"后调用，导航到平台首页检查登录状态。

**响应:** 同 poll。

---

### POST `/login/{platform}/paste` — 粘贴 Cookie

手动粘贴 cookie 字符串保存。

**请求:**

```json
{
  "cookie_str": "name1=value1; name2=value2; ..."
}
```

**响应:**

```json
{
  "status": "success",
  "count": 15
}
```

---

## 任务状态流转

```
pending → running → completed
                  → failed (自动重试最多 3 次)
pending → cancelled (用户手动取消)
running → pending (cookie 缺失阻塞，退回等待)
```

## 任务来源与优先级

| 来源 | task_id 前缀 | priority | Redis score |
|------|-------------|----------|-------------|
| 用户 API | `ut_` | 100 | `0 × 1e10 + ts`（最高） |
| 候选状态触发 | `ct_` | 1-5 | `1 × 1e10 + ts` |

Dispatcher 每 10s 轮询一次，`zpopmin` 弹出 score 最小的任务优先执行。
