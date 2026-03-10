# Dashboard 开发经验总结

MindSpider 有两个监控面板，架构模式一致，数据源不同。

## 两个面板

| 面板 | 端口 | 代码位置 | 数据源 |
|------|------|---------|--------|
| 浅层采集 | 8778 | `BroadTopicExtraction/admin/` | MongoDB (crawl_runs, aggregator, hot_*) |
| 深层采集 | 8777 | `DeepSentimentCrawling/admin/` | MongoDB (crawl_tasks, candidates) + MySQL fish 库 |

## 统一架构

两个面板都遵循相同的文件结构和模式：

```
admin/
├── app.py          # FastAPI 入口 + 启动钩子（浅层独立启动；深层挂载到 login_console）
├── api.py          # APIRouter，所有端点 + token 校验
├── metrics.py      # 纯数据查询函数（MongoDB 聚合 / MySQL 查询）
├── templates.py    # 单个 f-string 生成完整 HTML（含 CSS + JS）
└── log_reader.py   # 解析日志文件提取 ERROR 级别条目
```

### 依赖注入

`api.py` 中用模块级全局变量存储共享实例，启动时通过 `api.init()` 注入：

```python
# api.py
_mongo = None
_cookie_manager = None

def init(mongo, cookie_manager=None, ...):
    global _mongo, _cookie_manager
    _mongo = mongo
    _cookie_manager = cookie_manager
```

- 浅层：`app.py` 的 `@app.on_event("startup")` 中初始化
- 深层：`start_deep_crawl.py` 中初始化后 `login_app.include_router(dashboard_api.router)`

## 前端约定

### HTML 模板

`templates.py` 导出一个 `get_dashboard_html(token)` 函数，返回完整 HTML 字符串。因为用 Python f-string，所有 JS 中的花括号必须双写 `{{` `}}`。

### CSS 配色（Ant Design）

```
主色: #1890ff    成功: #52c41a    警告: #faad14    错误: #ff4d4f
背景: #f0f2f5    卡片: #fff       边框: #f0f0f0    文字: #333/#666/#888
```

平台品牌色（趋势图）：
```
小红书: #ff2442  抖音: #000000  B站: #00a1d6  微博: #ff6600
快手: #ff5000    贴吧: #4e6ef2  知乎: #0066ff
```

### JS 数据流

```
页面加载 → refreshAll() → 并行调用 loadXxx() → fetchJSON(url) → renderXxx(data)
                ↑
        setInterval 60s 自动刷新（可通过 checkbox 关闭）
```

`fetchJSON()` 自动拼接 token 参数，遇到 403 弹出 token 输入框（深层面板）。

### Chart.js

- 版本: v4，从 CDN 加载
- 深层面板额外加载 `chartjs-plugin-annotation`（用于候选热度曲线的状态标注点和阈值线）
- 重要：创建新图表前必须 `if (chart) chart.destroy()` 防止内存泄漏

### 弹窗模式

```html
<div class="modal-overlay" id="xxx-modal" onclick="if(event.target===this)closeXxxModal()">
    <div class="modal-box">
        <button class="modal-close" onclick="closeXxxModal()">&times;</button>
        <!-- 内容 -->
    </div>
</div>
```

通过 `.active` class 控制显示，ESC 键关闭。

## Token 鉴权

- 浅层：`settings.ADMIN_DASHBOARD_TOKEN`
- 深层：`settings.LOGIN_CONSOLE_TOKEN`
- 前端通过 URL query param `?token=xxx` 传递，JS 中 `fetchJSON()` 自动附加

## 数据查询模式

### MongoDB 聚合（两个面板通用）

```python
pipeline = [
    {"$match": {"status": "completed", "completed_at": {"$gte": since}}},
    {"$addFields": {"dt": {"$toDate": {"$multiply": ["$completed_at", 1000]}}}},
    {"$group": {"_id": {"year": ..., "hour": ...}, "count": {"$sum": 1}}},
    {"$sort": {...}},
]
```

时间戳统一用 Unix epoch 秒，MongoDB 聚合中需 `$multiply` 1000 转毫秒。

### MySQL 查询（深层面板 — 爬取结果）

连接 `fish` 库，懒初始化独立 engine：

```python
from sqlalchemy import create_engine, text
_fish_engine = None

def _get_fish_engine():
    global _fish_engine
    if _fish_engine is None:
        _fish_engine = create_engine(
            f"mysql+pymysql://{settings.DB_USER}:{quote_plus(settings.DB_PASSWORD)}"
            f"@{settings.DB_HOST}:{settings.DB_PORT}/fish?charset={settings.DB_CHARSET}",
            pool_recycle=3600, pool_pre_ping=True,
        )
    return _fish_engine
```

7 张平台内容表字段名各不相同，通过 `PLATFORM_TABLES` 和 `PLATFORM_FIELD_MAP` 做统一映射：

| 语义 | xhs | douyin | kuaishou | bilibili | weibo | tieba | zhihu |
|------|-----|--------|----------|----------|-------|-------|-------|
| 内容ID | note_id | aweme_id | video_id | video_id | note_id | note_id | content_id |
| 作者 | nickname | nickname | nickname | nickname | nickname | user_nickname | user_nickname |
| 标题 | title | title | title | title | *(无)* | title | title |
| 正文 | desc | desc | desc | desc | content | desc | content_text |
| 点赞 | liked_count | liked_count | liked_count | liked_count | liked_count | *(无)* | voteup_count |
| 评论数 | comment_count | comment_count | *(无)* | video_comment | comments_count | total_replay_num | comment_count |
| 转发 | share_count | share_count | *(无)* | video_share_count | shared_count | *(无)* | *(无)* |
| 时间 | time | create_time | create_time | create_time | create_time | publish_time | created_time |

关联方式：内容表 `crawling_task_id` → `crawling_tasks.task_id` → `topic_id` 聚合到话题维度。

## 日志解析

两个面板的 `log_reader.py` 模式一致：

- 读取今天 + 昨天的日志文件
- 正则匹配 `{time} | {level} | {message}` 格式
- 只提取 ERROR 级别
- 通过关键词提取来源/平台名
- 倒序返回（最新在前）

日志路径：
- 浅层：`BroadTopicExtraction/logs/scheduler_{YYYY-MM-DD}.log`
- 深层：`logs/deep_crawl_{YYYY-MM-DD}.log`

## 分页模式

两个面板中多处使用相同的分页模式：

### 后端（metrics.py + api.py）

`metrics.py` 查询函数接收 `limit` + `offset`，返回 `{"total": N, "items": [...]}` 格式：

```python
def get_xxx(mongo, limit=10, offset=0, ...):
    # ... 查询/排序全量数据 ...
    total = len(all_items)
    return {"total": total, "items": all_items[offset:offset + limit]}
```

`api.py` 端点透传 `offset: int = Query(0, ge=0)` 参数。

### 前端（templates.py）

```javascript
let _xxxPage = 0;
const XXX_PAGE_SIZE = 10;

async function loadXxx() {
    const offset = _xxxPage * XXX_PAGE_SIZE;
    const data = await fetchJSON(`/api/xxx?limit=${XXX_PAGE_SIZE}&offset=${offset}`);
    renderXxx(data);  // data.items + data.total
}

function prevXxxPage() {
    if (_xxxPage > 0) { _xxxPage--; loadXxx(); }
}
function nextXxxPage() {
    _xxxPage++;
    loadXxx();
}
```

HTML 翻页组件复用 `.pagination` CSS class：

```html
<div class="pagination">
    <button onclick="prevXxxPage()">上一页</button>
    <span id="xxx-page-info">第 1 页</span>
    <button onclick="nextXxxPage()">下一页</button>
</div>
```

排名序号需基于 offset 计算：`rank = _xxxPage * XXX_PAGE_SIZE + i + 1`

已使用此模式的区域：任务列表、24h 热门候选。

## 开发注意事项

1. **f-string 转义**：templates.py 中所有 JS 花括号必须写成 `{{` `}}`，漏写会导致 Python 格式化报错
2. **MySQL 保留字**：weibo 的 `content` 列名是 MySQL 保留字，SQL 中必须用反引号 `` `content` `` 包裹
3. **单引号注入**：onclick 中传递话题名时，需要 `.replace(/'/g, "\\'")`  转义单引号
4. **响应式布局**：summary 卡片 4 列 → 移动端 2 列 (`@media max-width: 768px`)
5. **连接池**：MySQL engine 设置 `pool_recycle=3600, pool_pre_ping=True` 防止长连接超时
6. **时间格式**：各平台时间字段类型不一致（bigint/varchar），前端 `formatPubTime()` 需同时处理 Unix 时间戳和字符串
