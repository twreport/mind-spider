# 平台爬虫调试经验总结

## 背景

MindSpider 深层采集模块支持 7 个社交平台。2026-02-22 调试了快手(ks)和抖音(dy)的搜索+评论获取。

## 当前平台状态

| 平台 | 搜索 | 评论 | 状态 | 说明 |
|------|------|------|------|------|
| bili | API | API | ✅ | 正常 |
| wb | API | API | ✅ | 正常 |
| zhihu | API | API | ✅ | 正常 |
| tieba | curl | curl | ✅ | Python requests 被 TLS 指纹检测，改用 curl 子进程 |
| ks | API | DOM 提取 | ✅ | GraphQL commentListQuery 已废弃，改 DOM 提取 |
| dy | 搜索框+拦截 | API | ✅ | API 搜索被 verify_check，搜索框方式绕过 |
| xhs | ? | ? | ❓ | **未测试** |

## 调试方法论

### 1. 写独立测试脚本，不要反复重启全系统

**核心原则**: 在 `scripts/` 下写独立的诊断脚本，直接复用 MediaCrawler 模块，不依赖完整的调度系统。

好处：
- 快速迭代，改脚本比改核心代码+重启系统快得多
- 可以同时测试多种方案（API、fetch、DOM、curl 等）
- 输出详细诊断信息，不受系统日志格式限制

模板结构：
```python
# scripts/test_xxx_comments.py
import asyncio, os, sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
MC_DIR = os.path.join(PROJECT_ROOT, "DeepSentimentCrawling", "MediaCrawler")
sys.path.insert(0, MC_DIR)
os.chdir(MC_DIR)

# 从 MongoDB 获取 cookie
from pymongo import MongoClient
# cookie 存在 mindspider_signal.platform_cookies，字段: platform, status, cookies(dict)

# 用 Playwright 启动浏览器，注入 cookie + stealth.min.js
# 测试各种方案...
```

运行方式（服务器上）：
```bash
cd /deploy/parallel-universe/mind-spider/DeepSentimentCrawling/MediaCrawler
python -u ../../scripts/test_xxx_comments.py 2>&1
```

### 2. 多方案并行测试

当一个方案失败时，不要只盯着修那个方案。在测试脚本中同时测试多种方式：

- **httpx 直接请求**（原始 API 方式）
- **浏览器 fetch()**（从 page.evaluate 中发 fetch，复用浏览器 session）
- **导航到页面 + DOM 提取**（SSR 渲染的数据在 DOM 中）
- **响应拦截**（page.on("response", handler) 捕获浏览器自己发的请求）
- **curl 子进程**（绕过 Python TLS 指纹）
- **RENDER_DATA / __NEXT_DATA__**（SSR 注入到 script 标签中的数据）

### 3. 网络响应拦截是最强大的工具

```python
intercepted = []

async def handle_response(response):
    if "/target/api/" in response.url and response.status == 200:
        body = await response.json()
        intercepted.append(body)

page.on("response", handle_response)
# 然后做正常的浏览器操作（搜索、点击等）
# intercepted 中会自动收集到完整的 API 响应
```

这个方法的核心优势：浏览器自己的请求带有完整的签名和 cookie，绕过了所有反爬。

## 快手(ks)调试记录

### 问题
搜索正常（40+ 视频），但所有视频的评论返回 0 条。

### 排查过程
1. 写 `scripts/test_ks_comments.py`，测试了 8 种评论获取方式（httpx、curl、Playwright fetch 等）
2. **全部返回 commentCount=None, rootComments=0**
3. 关键发现：在浏览器拦截方法中，浏览器自己的 GraphQL commentListQuery 也返回空，但 **DOM 中有 181 个 comment 相关元素**，包含真实评论文本
4. 结论：**快手已废弃 GraphQL commentListQuery 接口**，评论通过 SSR 渲染在 DOM 中

### 解决方案
重写 `kuaishou/client.py`：
- 删除所有 GraphQL 评论方法
- 新增 `get_video_comments_from_dom(photo_id)` — 导航到视频页，用 CSS 选择器提取评论
- 选择器：`.comment-item.comment-list-item`，子元素 `.author-name`、`.comment-item-time`、`.comment-item-content`
- 递归 DOM 遍历提取 emoji alt 文本
- 过滤 < 4 字符的短评论（纯表情等）

重写 `kuaishou/core.py`：
- `batch_get_video_comments` 从并发改为顺序执行（DOM 提取需要逐个导航页面）

### 注意事项
- `KuaishouVideoComment.comment_id` 是 `BigInteger`（不是 varchar），生成 ID 用 `int(md5[:15], 16)`
- DOM 中只有相对时间（"2小时前"），用当前时间戳代替

## 抖音(dy)调试记录

### 问题
API 搜索（httpx + a_bogus 签名）返回 `search_nil_type: "verify_check"`，要求验证码。

### 排查过程
1. 写 `scripts/test_dy_comments.py`，测试 API 搜索 → verify_check
2. 直接导航到 `douyin.com/search/关键词` → 页面标题变成"验证码中间页"
3. **在首页用搜索框输入关键词** → 到达真正的搜索结果页！标题"发现更多精彩视频"
4. 但 DOM 提取找不到 `/video/` 链接（搜索结果是 JS 动态渲染的）
5. 用**响应拦截**捕获浏览器发出的 `/search/single/` 请求 → **data_len=10，真实数据！**
6. 评论测试：API (httpx + a_bogus) 和 browser fetch 都能获取评论，各返回 20 条/视频

### 解决方案
重写 `douyin/core.py` 的 `search()` 方法：
- 导航到首页，找到搜索框（`wait_for_selector` 等待最多 10s）
- 输入关键词，按回车
- 通过 `page.on("response", handler)` 拦截 `/search/single/` 响应
- 滚动页面触发更多结果加载
- 评论保持原有 API 方式不变（a_bogus 签名对评论接口有效）

### 关键坑
- **必须设置真实 user_agent**！`launch_browser` 传 `user_agent=None` 时 Playwright 默认 UA 包含 HeadlessChrome，抖音检测后渲染不同的页面（没有搜索框）
- 测试脚本能工作但正式代码不行，就是因为这个 UA 差异
- 修复：在 `DouYinCrawler.__init__` 中设置 `self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"`

## 贴吧(tieba)调试记录

### 问题
Python 的 httpx/requests 库发出的 HTTPS 请求被百度 TLS 指纹检测（JA3 fingerprinting）拦截。
Playwright 无头浏览器直接访问贴吧首页也会触发验证码，污染 cookie。

### 排查过程
1. httpx 请求超时或被拒绝
2. 换 requests 库 — 同样失败
3. Playwright `page.evaluate(fetch())` — 仍然失败
4. **系统 curl 命令** — 成功！curl 用的是 OpenSSL 的 TLS 指纹，和 Python 完全不同

### 解决方案
重写 `tieba/client.py`，所有 HTTP 请求改用 curl 子进程：

核心方法 `_curl_get(url)`：
```python
async def _curl_get(self, url: str) -> str:
    cmd = [
        "curl", "-sS", "-L", "--max-time", "30", "--compressed",
        "-D", "/dev/stderr",
        "-H", f"User-Agent: {ua}",
        "-H", f"Cookie: {cookie_str}",
        "-H", "Referer: https://tieba.baidu.com/",
        url,
    ]
    result = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, timeout=35)
```

关键设计：
- `asyncio.to_thread(subprocess.run, ...)` — 不阻塞异步事件循环
- **用原始 config.COOKIES**（从 MongoDB 加载的），不用浏览器 cookie — 因为浏览器访问首页会触发验证码，污染 cookie
- 自动检测编码：搜索页用 GBK，详情页用 UTF-8，通过 HTTP 响应头和 HTML meta 标签判断
- `-D /dev/stderr` 把响应头输出到 stderr 用于编码检测

已转换为 curl 的方法：
- `get_notes_by_keyword()` — 搜索
- `get_note_by_id()` — 帖子详情
- `get_note_all_comments()` — 评论
- `get_comments_all_sub_comments()` — 子评论
- `get_notes_by_tieba_name()` — 吧内帖子
- `get_creator_info_by_url()` — 用户信息

`tieba/core.py` 的反检测措施：
- 注入 JS 覆盖 `navigator.webdriver`、伪造 `navigator.chrome`、清除 ChromeDriver 痕迹
- 不直接访问 tieba.baidu.com，先访问 baidu.com 再点击贴吧链接（模拟真人导航路径）
- cookie 注入在导航之前完成，避免自动跳转到验证码页

### 为什么 curl 有效
系统 curl 使用 OpenSSL 的 TLS 实现，其 JA3 指纹与 Python 的 ssl/urllib3 完全不同。
百度的 JA3 指纹检测无法将 curl 识别为爬虫，请求正常通过（200, 52KB, 1.3s）。

## 小红书(xhs)待测试

### 建议测试步骤

1. 先写 `scripts/test_xhs_comments.py` 独立测试脚本
2. 从 MongoDB 获取 xhs 的 cookie：
   ```python
   doc = db.platform_cookies.find_one({"platform": "xhs", "status": "active"})
   ```
3. 参考已有的 xhs client: `media_platform/xhs/client.py`
4. 测试搜索 → 评论 → 检查 MySQL 数据（表名 `xhs_note`、`xhs_note_comment`）
5. 如果 API 方式失败，按上面的方法论逐步尝试其他方案

### 关键文件
- `media_platform/xhs/client.py` — XHS API 客户端
- `media_platform/xhs/core.py` — XHS 爬虫核心逻辑
- `store/xhs/__init__.py` — XHS 数据存储
- `database/models.py` — ORM 模型（XhsNote, XhsNoteComment）

## 服务器信息

- 地址: 10.168.1.80, 用户 myroot, 密码 tw7311
- 部署路径: `/deploy/parallel-universe/mind-spider`
- Python 环境: conda, 环境名 `mind-spider`
- MySQL: 10.168.1.80:3306, root/Tangwei7311Yeti., 数据库 fish
- MongoDB: 10.168.1.80:27018, 数据库 mindspider_signal
- 启动深层采集: `python DeepSentimentCrawling/start_deep_crawl.py --port 8777`
- 插入测试任务: `python scripts/insert_test_task.py`（修改 PLATFORMS 列表）

## Git 工作流

代码在本地 `D:\dev\mind-spider` 修改，commit + push 后，服务器上 `git pull` 同步。
服务器上不直接改代码（权限是 root，myroot 用户无法写入）。
