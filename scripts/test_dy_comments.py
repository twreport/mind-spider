# -*- coding: utf-8 -*-
"""抖音爬取诊断脚本

测试两种方案:
  方案A: API 搜索 (通过 DouYinClient + a_bogus 签名)
  方案B: 浏览器搜索 (导航到搜索页，从 DOM 提取结果)

评论也同时测试 API 和 DOM 两种方式。

用法:
  cd /deploy/parallel-universe/mind-spider/DeepSentimentCrawling/MediaCrawler
  uv run python ../../scripts/test_dy_comments.py
"""

import asyncio
import json
import os
import re
import sys
import urllib.parse

# 确保 MediaCrawler 目录在 PYTHONPATH 中
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
MC_DIR = os.path.join(PROJECT_ROOT, "DeepSentimentCrawling", "MediaCrawler")
sys.path.insert(0, MC_DIR)
os.chdir(MC_DIR)

from pymongo import MongoClient

# ─── 配置 ─────────────────────────────────────────────
MONGO_URI = "mongodb://10.168.1.80:27018"
MONGO_DB = "mindspider_signal"
KEYWORD = "短道速滑"
MAX_VIDEOS = 3
MAX_COMMENTS = 10

STEALTH_JS = os.path.join(MC_DIR, "libs", "stealth.min.js")


def get_cookie_from_mongo():
    client = MongoClient(MONGO_URI)
    db = client[MONGO_DB]
    doc = db.platform_cookies.find_one({"platform": "dy", "status": "active"})
    client.close()
    if not doc:
        print("MongoDB 中没有找到 dy 的 active cookie")
        sys.exit(1)
    cookies = doc["cookies"]
    cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
    print(f"cookie: {len(cookies)} fields, length={len(cookie_str)}")
    for key in ["sessionid", "sessionid_ss", "LOGIN_STATUS", "passport_csrf_token", "msToken"]:
        val = cookies.get(key, "")
        print(f"  {key}: {'YES' if val else 'NO'}" + (f" ({len(val)} chars)" if val else ""))
    return cookies, cookie_str


# ─── DOM 搜索结果提取 JS ─────────────────────────────
EXTRACT_SEARCH_RESULTS_JS = """
() => {
    const results = { videos: [], debug: {} };

    // 抖音搜索结果页的视频卡片
    // 尝试多种选择器
    const selectors = [
        // 搜索结果列表
        '[class*="search-result"] a[href*="/video/"]',
        'a[href*="/video/"]',
        '[class*="card"] a[href*="/video/"]',
        '[class*="result"] a[href*="/video/"]',
    ];

    results.debug.url = window.location.href;
    results.debug.title = document.title;

    // 收集所有 /video/ 链接
    const videoLinks = new Set();
    const allLinks = document.querySelectorAll('a[href*="/video/"]');
    results.debug.totalVideoLinks = allLinks.length;

    allLinks.forEach(a => {
        const href = a.getAttribute('href') || '';
        const match = href.match(/\\/video\\/(\\d+)/);
        if (!match) return;

        const videoId = match[1];
        if (videoLinks.has(videoId)) return;
        videoLinks.add(videoId);

        // 尝试从卡片中提取信息
        // 向上查找最近的容器
        let container = a;
        for (let i = 0; i < 5; i++) {
            if (container.parentElement) container = container.parentElement;
        }

        const text = container.textContent || '';
        // 提取描述 (通常在视频标题/描述中)
        const desc = text.trim().slice(0, 200);

        results.videos.push({
            videoId: videoId,
            href: href,
            desc: desc,
        });
    });

    // 额外: 从 SSR 数据中提取
    const nextData = document.getElementById('__NEXT_DATA__');
    if (nextData) {
        try {
            const d = JSON.parse(nextData.textContent);
            results.debug.hasNextData = true;
            // 深度搜索 aweme_id
            const findAwemeIds = (obj, path = '', depth = 0) => {
                if (!obj || typeof obj !== 'object' || depth > 8) return [];
                let found = [];
                for (const [k, v] of Object.entries(obj)) {
                    if (k === 'aweme_id' && typeof v === 'string' && v.length > 5) {
                        found.push({ path: path + '.' + k, value: v });
                    }
                    if (typeof v === 'object' && v !== null) {
                        found = found.concat(findAwemeIds(v, path + '.' + k, depth + 1));
                    }
                }
                return found;
            };
            const awemeIds = findAwemeIds(d);
            results.debug.ssrAwemeIds = awemeIds.slice(0, 20);
        } catch(e) {
            results.debug.nextDataError = e.message;
        }
    }

    // 检查 RENDER_DATA (抖音常用)
    const renderData = document.getElementById('RENDER_DATA');
    if (renderData) {
        try {
            const decoded = decodeURIComponent(renderData.textContent);
            const d = JSON.parse(decoded);
            results.debug.hasRenderData = true;
            // 搜索 aweme_id
            const findAwemeIds = (obj, path = '', depth = 0) => {
                if (!obj || typeof obj !== 'object' || depth > 8) return [];
                let found = [];
                for (const [k, v] of Object.entries(obj)) {
                    if (k === 'aweme_id' && typeof v === 'string' && v.length > 5) {
                        found.push({ path: path + '.' + k, value: v });
                    }
                    if (typeof v === 'object' && v !== null) {
                        found = found.concat(findAwemeIds(v, path + '.' + k, depth + 1));
                    }
                }
                return found;
            };
            const awemeIds = findAwemeIds(d);
            results.debug.renderDataAwemeIds = awemeIds.slice(0, 20);

            // 也找 desc
            const findDescs = (obj, path = '', depth = 0) => {
                if (!obj || typeof obj !== 'object' || depth > 8) return [];
                let found = [];
                for (const [k, v] of Object.entries(obj)) {
                    if (k === 'desc' && typeof v === 'string' && v.length > 5) {
                        found.push({ path: path + '.' + k, value: v.slice(0, 100) });
                    }
                    if (typeof v === 'object' && v !== null) {
                        found = found.concat(findDescs(v, path + '.' + k, depth + 1));
                    }
                }
                return found;
            };
            results.debug.renderDataDescs = findDescs(d).slice(0, 20);
        } catch(e) {
            results.debug.renderDataError = e.message;
        }
    }

    return results;
}
"""

# ─── DOM 评论提取 JS ─────────────────────────────────
EXTRACT_COMMENTS_JS = """
() => {
    const results = { comments: [], debug: {} };

    // 抖音视频页评论区的常见选择器
    const selectors = [
        '[class*="comment-item"]',
        '[class*="CommentItem"]',
        '[class*="commentItem"]',
        '[class*="comment-list"]',
        '[class*="CommentList"]',
    ];

    results.debug.url = window.location.href;

    // 检查有哪些 comment 相关元素
    const allCommentEls = document.querySelectorAll('[class*="comment"], [class*="Comment"]');
    results.debug.totalCommentElements = allCommentEls.length;

    const classNames = new Set();
    allCommentEls.forEach(el => {
        el.classList.forEach(cls => {
            if (cls.toLowerCase().includes('comment')) {
                classNames.add(cls);
            }
        });
    });
    results.debug.commentClassNames = Array.from(classNames).sort();

    // 查找选择器
    results.debug.selectorCounts = {};
    for (const sel of selectors) {
        const els = document.querySelectorAll(sel);
        if (els.length > 0) {
            results.debug.selectorCounts[sel] = els.length;
        }
    }

    // 找重复出现的 class (可能是评论项)
    const classCounts = {};
    allCommentEls.forEach(el => {
        const key = el.className;
        classCounts[key] = (classCounts[key] || 0) + 1;
    });
    const repeatedClasses = Object.entries(classCounts)
        .filter(([k, v]) => v >= 2)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 10);
    results.debug.repeatedCommentClasses = repeatedClasses.map(([cls, count]) => ({
        className: cls.slice(0, 120),
        count: count,
    }));

    // 从重复结构中提取评论
    for (const [cls, count] of repeatedClasses) {
        if (count < 2) continue;
        const selector = '.' + cls.split(' ').filter(c => c).join('.');
        let items;
        try {
            items = document.querySelectorAll(selector);
        } catch(e) {
            continue;
        }
        if (items.length < 2) continue;

        const sample = items[0];
        const text = sample.textContent?.trim();
        if (!text || text.length < 5) continue;

        results.debug.selectedContainer = {
            className: cls.slice(0, 120),
            count: items.length,
            sampleText: text.slice(0, 200),
        };

        items.forEach((item, idx) => {
            if (idx >= 30) return;
            const comment = {
                index: idx,
                fullText: item.textContent?.trim().slice(0, 300) || '',
                childCount: item.children.length,
            };

            // 找作者名
            const authorEl = item.querySelector('[class*="name"], [class*="author"], [class*="nick"], [class*="user"]');
            if (authorEl) {
                comment.author = authorEl.textContent?.trim();
            }

            // 找评论内容
            const contentEl = item.querySelector('[class*="content"], [class*="text"]');
            if (contentEl) {
                comment.content = contentEl.textContent?.trim();
            }

            // 找时间
            const timeEl = item.querySelector('[class*="time"], [class*="date"]');
            if (timeEl) {
                comment.time = timeEl.textContent?.trim();
            }

            results.comments.push(comment);
        });

        break;
    }

    // 检查 RENDER_DATA 中的评论数据
    const renderData = document.getElementById('RENDER_DATA');
    if (renderData) {
        try {
            const decoded = decodeURIComponent(renderData.textContent);
            const d = JSON.parse(decoded);
            const findComments = (obj, path = '', depth = 0) => {
                if (!obj || typeof obj !== 'object' || depth > 8) return [];
                let found = [];
                for (const [k, v] of Object.entries(obj)) {
                    const p = path + '.' + k;
                    if (k === 'comments' && Array.isArray(v)) {
                        found.push({ path: p, count: v.length });
                    }
                    if (typeof v === 'object' && v !== null) {
                        found = found.concat(findComments(v, p, depth + 1));
                    }
                }
                return found;
            };
            results.debug.renderDataComments = findComments(d).slice(0, 10);
        } catch(e) {}
    }

    return results;
}
"""


async def main():
    print("=" * 60)
    print("抖音爬取诊断脚本 v2 (API + 浏览器双模式)")
    print("=" * 60)

    cookie_dict, cookie_str = get_cookie_from_mongo()

    # ─── 设置 config ───────────────────────────────────
    import config
    config.PLATFORM = "dy"
    config.LOGIN_TYPE = "cookie"
    config.COOKIES = cookie_str
    config.HEADLESS = True
    config.SAVE_DATA_OPTION = "json"
    config.ENABLE_GET_COMMENTS = True
    config.CRAWLER_MAX_NOTES_COUNT = MAX_VIDEOS
    config.CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES = MAX_COMMENTS
    config.CRAWLER_MAX_SLEEP_SEC = 2
    config.ENABLE_GET_SUB_COMMENTS = False
    config.KEYWORDS = KEYWORD

    from playwright.async_api import async_playwright
    from tools import utils

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        )

        # 注入 stealth
        if os.path.exists(STEALTH_JS):
            await context.add_init_script(path=STEALTH_JS)
            print("stealth.min.js injected")

        # 注入 cookie
        for k, v in cookie_dict.items():
            if not k or not v:
                continue
            await context.add_cookies([{
                "name": k,
                "value": str(v),
                "domain": ".douyin.com",
                "path": "/"
            }])

        # 自动注入 LOGIN_STATUS
        if "sessionid" in cookie_dict and "LOGIN_STATUS" not in cookie_dict:
            await context.add_cookies([{
                "name": "LOGIN_STATUS",
                "value": "1",
                "domain": ".douyin.com",
                "path": "/"
            }])
            print("AUTO-INJECTED LOGIN_STATUS=1")

        page = await context.new_page()

        # ─── 导航到抖音首页 ──────────────────────────────
        print(f"\n1. 导航到 douyin.com ...")
        try:
            await page.goto("https://www.douyin.com", wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(5)
            print(f"   URL: {page.url}")
            print(f"   Title: {await page.title()}")
        except Exception as e:
            print(f"   导航失败: {e}")
            await browser.close()
            return

        # ─── 检查登录状态 ─────────────────────────────────
        print(f"\n2. 检查登录状态 ...")
        local_storage = await page.evaluate("() => window.localStorage")
        has_user_login = local_storage.get("HasUserLogin", "")
        print(f"   localStorage.HasUserLogin = '{has_user_login}'")

        _, browser_cookie_dict = utils.convert_cookies(await context.cookies())
        login_status = browser_cookie_dict.get("LOGIN_STATUS", "")
        print(f"   cookie LOGIN_STATUS = '{login_status}'")

        if has_user_login == "1" or login_status == "1":
            print("   login: OK")
        else:
            print("   login: MAYBE NOT LOGGED IN, continuing...")

        # ═══════════════════════════════════════════════════
        # 方案A: API 搜索
        # ═══════════════════════════════════════════════════
        print(f"\n{'='*60}")
        print(f"方案A: API 搜索 (DouYinClient + a_bogus)")
        print(f"{'='*60}")

        from media_platform.douyin.client import DouYinClient

        cookie_str_browser, cookie_dict_browser = utils.convert_cookies(await context.cookies())
        user_agent = await page.evaluate("() => navigator.userAgent")

        dy_client = DouYinClient(
            headers={
                "User-Agent": user_agent,
                "Cookie": cookie_str_browser,
                "Host": "www.douyin.com",
                "Origin": "https://www.douyin.com/",
                "Referer": "https://www.douyin.com/",
                "Content-Type": "application/json;charset=UTF-8",
            },
            playwright_page=page,
            cookie_dict=cookie_dict_browser,
        )

        from var import request_keyword_var
        request_keyword_var.set(KEYWORD)

        api_video_ids = []
        try:
            search_res = await dy_client.search_info_by_keyword(keyword=KEYWORD, offset=0)
            status_code = search_res.get("status_code", "?")
            data_list = search_res.get("data", [])
            print(f"   status_code={status_code}, 返回 {len(data_list) if data_list else 0} 个结果")

            if not data_list:
                # 检查是否有验证码要求
                search_nil = search_res.get("search_nil_info", {})
                nil_type = search_nil.get("search_nil_type", "")
                print(f"   search_nil_type: {nil_type}")
                if nil_type == "verify_check":
                    print("   API 搜索被抖音验证码拦截 (verify_check)")
                else:
                    resp_str = json.dumps(search_res, ensure_ascii=False, indent=2)
                    print(f"   响应: {resp_str[:800]}")
            else:
                for item in data_list[:MAX_VIDEOS]:
                    aweme_info = item.get("aweme_info", {})
                    if not aweme_info:
                        continue
                    aweme_id = aweme_info.get("aweme_id", "")
                    desc = aweme_info.get("desc", "")[:50]
                    stats = aweme_info.get("statistics", {})
                    api_video_ids.append(aweme_id)
                    print(f"   {aweme_id}  likes={stats.get('digg_count',0)}  comments={stats.get('comment_count',0)}  {desc}")

        except Exception as e:
            print(f"   API 搜索异常: {type(e).__name__}: {e}")

        if api_video_ids:
            print(f"\n   API 搜索成功! 找到 {len(api_video_ids)} 个视频")
        else:
            print(f"\n   API 搜索失败，切换到方案B...")

        # ═══════════════════════════════════════════════════
        # 方案B: 浏览器搜索 (DOM 提取)
        # ═══════════════════════════════════════════════════
        print(f"\n{'='*60}")
        print(f"方案B: 浏览器搜索 (导航到搜索页 + DOM 提取)")
        print(f"{'='*60}")

        search_url = f"https://www.douyin.com/search/{urllib.parse.quote(KEYWORD)}?type=video"
        print(f"   导航到: {search_url}")

        try:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(6)
            print(f"   URL: {page.url}")
            print(f"   Title: {await page.title()}")

            # 检查是否有验证码弹窗
            captcha_check = await page.evaluate("""
                () => {
                    // 常见验证码容器
                    const captchaSelectors = [
                        '[class*="captcha"]',
                        '[class*="verify"]',
                        '[class*="Captcha"]',
                        '[class*="Verify"]',
                        '#captcha-verify-image',
                        '[id*="captcha"]',
                    ];
                    for (const sel of captchaSelectors) {
                        const el = document.querySelector(sel);
                        if (el && el.offsetHeight > 0) {
                            return { hasCaptcha: true, selector: sel, text: el.textContent?.slice(0, 100) };
                        }
                    }
                    return { hasCaptcha: false };
                }
            """)
            if captcha_check.get("hasCaptcha"):
                print(f"   CAPTCHA detected: {captcha_check}")
            else:
                print(f"   No CAPTCHA popup detected")

            # 滚动加载更多
            await page.evaluate("window.scrollTo(0, 800)")
            await asyncio.sleep(2)

            # 提取搜索结果
            search_result = await page.evaluate(EXTRACT_SEARCH_RESULTS_JS)
            debug = search_result.get("debug", {})
            videos = search_result.get("videos", [])

            print(f"\n   --- DOM 搜索结果 ---")
            print(f"   总 /video/ 链接数: {debug.get('totalVideoLinks', 0)}")

            if debug.get("hasRenderData"):
                print(f"   RENDER_DATA: 存在")
                if debug.get("renderDataAwemeIds"):
                    print(f"   RENDER_DATA aweme_ids ({len(debug['renderDataAwemeIds'])}):")
                    for item in debug["renderDataAwemeIds"][:10]:
                        print(f"      {item['path']}: {item['value']}")
                if debug.get("renderDataDescs"):
                    print(f"   RENDER_DATA descs ({len(debug['renderDataDescs'])}):")
                    for item in debug["renderDataDescs"][:5]:
                        print(f"      {item['value'][:80]}")

            if debug.get("hasNextData"):
                print(f"   __NEXT_DATA__: 存在")
                if debug.get("ssrAwemeIds"):
                    print(f"   SSR aweme_ids:")
                    for item in debug["ssrAwemeIds"][:10]:
                        print(f"      {item['path']}: {item['value']}")

            print(f"\n   DOM 提取到 {len(videos)} 个视频链接:")
            browser_video_ids = []
            for v in videos[:10]:
                print(f"      {v['videoId']}  {v['desc'][:60]}")
                browser_video_ids.append(v["videoId"])

        except Exception as e:
            print(f"   浏览器搜索异常: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            browser_video_ids = []

        # ─── 选择可用的 video_ids ─────────────────────────
        # 优先用 RENDER_DATA 中的 aweme_ids (更可靠)
        render_ids = []
        if "debug" in search_result and search_result["debug"].get("renderDataAwemeIds"):
            render_ids = [item["value"] for item in search_result["debug"]["renderDataAwemeIds"]]

        # 汇总所有来源的 video_ids
        all_video_ids = api_video_ids or render_ids or browser_video_ids
        if not all_video_ids:
            print("\n   所有搜索方式均未找到视频!")

            # 最后手段: 截图看看页面长什么样
            screenshot_path = os.path.join(SCRIPT_DIR, "dy_search_debug.png")
            await page.screenshot(path=screenshot_path, full_page=False)
            print(f"   截图已保存: {screenshot_path}")

            # dump 页面 HTML 片段
            body_text = await page.evaluate("() => document.body?.innerText?.slice(0, 2000) || 'EMPTY'")
            print(f"\n   页面文本 (前2000字符):")
            print(f"   {body_text[:2000]}")

            await browser.close()
            return

        video_ids = all_video_ids[:MAX_VIDEOS]
        source = "API" if api_video_ids else ("RENDER_DATA" if render_ids else "DOM")
        print(f"\n   使用 {source} 来源的视频 IDs: {video_ids}")

        # ═══════════════════════════════════════════════════
        # 测试评论获取
        # ═══════════════════════════════════════════════════
        print(f"\n{'='*60}")
        print(f"测试评论获取")
        print(f"{'='*60}")

        total_api_comments = 0
        total_dom_comments = 0

        for vid in video_ids:
            print(f"\n--- 视频 {vid} ---")

            # 方式1: API 评论
            print(f"  [API] 获取评论...")
            try:
                comments_res = await dy_client.get_aweme_comments(vid, cursor=0)
                has_more = comments_res.get("has_more", 0)
                comments = comments_res.get("comments", [])
                status_code = comments_res.get("status_code", "?")

                print(f"  [API] status={status_code}, 评论数={len(comments) if comments else 0}, has_more={has_more}")

                if comments:
                    for c in comments[:3]:
                        user = c.get("user", {})
                        text = c.get("text", "")[:60]
                        ip = c.get("ip_label", "")
                        print(f"    {user.get('nickname','?')} ({ip}): {text}")
                    total_api_comments += len(comments)
                else:
                    # 输出响应帮助诊断
                    resp_keys = list(comments_res.keys())
                    print(f"  [API] 无评论, keys: {resp_keys}")
            except Exception as e:
                print(f"  [API] 异常: {type(e).__name__}: {e}")

            await asyncio.sleep(1)

            # 方式2: 浏览器 DOM 评论
            print(f"  [DOM] 导航到视频页提取评论...")
            try:
                video_url = f"https://www.douyin.com/video/{vid}"
                await page.goto(video_url, wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(5)

                # 滚动到评论区
                await page.evaluate("window.scrollTo(0, 600)")
                await asyncio.sleep(2)

                # 尝试点击评论区展开按钮 (如果有)
                try:
                    await page.evaluate("""
                        () => {
                            const btns = document.querySelectorAll('[class*="comment"], [class*="Comment"]');
                            for (const btn of btns) {
                                if (btn.textContent?.includes('评论') && btn.offsetHeight > 0 && btn.offsetHeight < 60) {
                                    btn.click();
                                    return true;
                                }
                            }
                            return false;
                        }
                    """)
                    await asyncio.sleep(2)
                except Exception:
                    pass

                dom_result = await page.evaluate(EXTRACT_COMMENTS_JS)
                dom_comments = dom_result.get("comments", [])
                dom_debug = dom_result.get("debug", {})

                print(f"  [DOM] comment 相关元素: {dom_debug.get('totalCommentElements', 0)}")
                if dom_debug.get("commentClassNames"):
                    print(f"  [DOM] class 名: {dom_debug['commentClassNames'][:8]}")
                if dom_debug.get("repeatedCommentClasses"):
                    for item in dom_debug["repeatedCommentClasses"][:3]:
                        print(f"  [DOM] [{item['count']}x] {item['className'][:80]}")
                if dom_debug.get("selectedContainer"):
                    sc = dom_debug["selectedContainer"]
                    print(f"  [DOM] 选中容器: {sc['className'][:60]} ({sc['count']} items)")
                if dom_debug.get("renderDataComments"):
                    print(f"  [DOM] RENDER_DATA 评论: {dom_debug['renderDataComments']}")

                print(f"  [DOM] 提取到 {len(dom_comments)} 条评论:")
                for c in dom_comments[:3]:
                    author = c.get("author", "?")
                    content = c.get("content", c.get("fullText", "")[:60])
                    print(f"    {author}: {content[:60]}")
                total_dom_comments += len(dom_comments)

            except Exception as e:
                print(f"  [DOM] 异常: {type(e).__name__}: {e}")

            await asyncio.sleep(2)

        # ─── 汇总 ────────────────────────────────────────
        print(f"\n{'='*60}")
        print(f"汇总:")
        print(f"  搜索: API={'OK' if api_video_ids else 'FAIL'}, "
              f"RENDER_DATA={len(render_ids)}个, DOM={len(browser_video_ids)}个")
        print(f"  评论: API={total_api_comments}条, DOM={total_dom_comments}条")
        print(f"  视频来源: {source}, 共 {len(video_ids)} 个")

        if total_api_comments > 0 or total_dom_comments > 0:
            winner = "API" if total_api_comments >= total_dom_comments else "DOM"
            print(f"  结论: 评论获取有效 ({winner} 方式更好)")
        elif video_ids:
            print(f"  结论: 搜索正常但评论获取都失败")
        else:
            print(f"  结论: 搜索和评论均失败")
        print(f"{'='*60}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
