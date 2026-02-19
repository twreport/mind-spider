# MindSpider 部署指南

## 一、服务器配置

### 当前服务器
- CPU: E5 双路 40 核
- 内存: 96 GB
- 评估: **完全够用**，可同时运行爬虫、MongoDB、AI 分析、Playwright 浏览器自动化

### 资源消耗估算

| 组件 | 内存占用 | CPU 占用 |
|------|----------|----------|
| MongoDB | 4-8 GB（热数据缓存）| 1-2 核 |
| Scrapy 爬虫（20+源）| 1-2 GB | 2-4 核 |
| Playwright 单实例 | 300-500 MB | 0.5-1 核 |
| Playwright 10 并发 | 3-5 GB | 5-10 核 |
| AI API 调用 | 极小 | 极小 |

### 真正的瓶颈
- **带宽**: 建议 10 Mbps+，浏览器自动化比 API 请求更耗带宽
- **平台风控**: 并发太高容易被封，需要主动限速

## 二、跨平台部署

代码已验证跨平台兼容，从 Windows 迁移到 Linux **无需改动代码**。

### Linux 部署步骤

```bash
# 1. 安装系统依赖（lxml 解析器需要）
apt-get install libxml2-dev libxslt-dev

# 2. 安装 Python 依赖
uv sync

# 3. 安装 Playwright 浏览器
uv run playwright install chromium

# 4. 配置环境变量
cp .env.example .env
# 编辑 .env 设置 MongoDB 连接、API Key 等

# 5. 设置时区
timedatectl set-timezone Asia/Shanghai
```

## 三、反爬配置

### 已有措施（MediaCrawler）

| 措施 | 状态 | 说明 |
|------|------|------|
| Stealth.js | ✅ | 所有平台注入反检测脚本 |
| User-Agent | ✅ | 各平台独立 UA 配置 |
| Cookie 持久化 | ✅ | 支持 QR/手机/Cookie 登录 |
| 代理支持 | ✅ | 支持快代理、豌豆代理 |
| CDP 模式 | ✅ | 可用真实浏览器降低检测 |
| 平台签名 | ✅ | 抖音 a_bogus、小红书 SecSign、B站 WBI |

### 需要改进

| 问题 | 现状 | 建议 |
|------|------|------|
| 延迟太规律 | 固定 2 秒 | 改为随机 2-8 秒 |
| 代理池太小 | 默认 2 个 IP | 建议 10+ 个 |
| 并发太低 | MAX_CONCURRENCY_NUM = 1 | 可提高到 3-5 |
| 无登录态检测 | 过期后才发现 | 添加定期检查 |
| 无验证码处理 | 遇到就卡住 | 集成打码平台 |

### 推荐配置

修改 `DeepSentimentCrawling/MediaCrawler/config/base_config.py`:

```python
# 启用代理
ENABLE_IP_PROXY = True
IP_PROXY_POOL_COUNT = 10

# 随机延迟（需改代码支持）
CRAWLER_MIN_SLEEP_SEC = 2
CRAWLER_MAX_SLEEP_SEC = 8

# 适当提高并发
MAX_CONCURRENCY_NUM = 3
```

### 各平台风控特点

| 平台 | 风控强度 | 注意事项 |
|------|----------|----------|
| 小红书 | 🔴 严格 | 设备指纹检测，建议真机/模拟器 |
| 抖音 | 🔴 严格 | 设备指纹 + a_bogus 签名 |
| 微博 | 🟡 中等 | 登录态检测，需稳定 Cookie |
| B站 | 🟢 宽松 | WBI 签名验证，频率适中即可 |
| 知乎 | 🟡 中等 | 频率敏感，需低速爬取 |
| 快手 | 🟡 中等 | 需要登录态 |
| 贴吧 | 🟢 宽松 | 相对简单 |

### 反爬关键点

1. **浏览器指纹**: 用 `playwright-stealth` 或 `undetected-playwright`
2. **行为模式**: 随机延迟 + 滚动/停留等人类行为
3. **Cookie 管理**: 持久化存储，定期轮换账号
4. **TLS 指纹**: 用 `curl_cffi` 或真实浏览器
5. **请求频率**: 同一 IP 限制 1-2 QPS

## 四、数据量估算

```
热搜类: ~50条/源 × 20源 × 6次/小时 × 24小时 ≈ 14.4万条/天
媒体类: ~100条/源 × 10源 × 1次/天 ≈ 1000条/天

单条数据: 1-5 KB
日增量: ~150 MB（含索引）
月增量: ~5 GB
```

## 五、待接入配置

### IP 代理池
- [ ] 接入自有 IP 池
- [ ] 配置轮换策略

### User-Agent 池
- [ ] 接入自有 Agent 池
- [ ] 配置随机选择

### AI API（阿里百炼）
- [ ] 配置 API Key
- [ ] 设置调用频率限制

## 六、爬虫状态汇总

### 表层采集（已完成）

**Hot National (10 个)**:
- ✅ weibo_hot, douyin_hot, baidu_hot, zhihu_hot, bilibili_hot
- ✅ hupu_hot, toutiao_hot, tencent_hot, netease_hot, sina_hot

**Hot Local (1 个)**:
- ❌ tianyan_hot (DNS 解析失败，已禁用)

**Hot Vertical (6 个)**:
- ✅ ithome, huxiu, kr36, cls, xueqiu, juejin

**Media (10 个)**:
- ✅ rmrb, xinhua, thepaper, cctv, gmrb
- ✅ jjrb, mrdx, xwlb, xwzbj
- ❌ zgqnb (URL 结构变化，已禁用)

**Aggregators (5 个)**:
- ✅ tophub, official, anyknew, rebang, jiucai
- ❌ mofish (DNS 失效，已废弃)

### 实际测试结果
- 总计: 32 个数据源
- 正常工作: 29 个
- 已禁用: 3 个
