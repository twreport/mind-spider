# -*- coding: utf-8 -*-
"""抖音爬取诊断脚本 v3

策略:
  1. 在首页通过搜索框输入关键词 (模拟真人)
  2. 从浏览器内部发 fetch 请求 (复用 session)
  3. 跳过搜索，直接用 MySQL 中已有的视频 ID 测试评论获取

用法:
  cd /deploy/parallel-universe/mind-spider/DeepSentimentCrawling/MediaCrawler
  python -u ../../scripts/test_dy_comments.py
"""

import asyncio
import json
import os
import sys
import urllib.parse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
MC_DIR = os.path.join(PROJECT_ROOT, "DeepSentimentCrawling", "MediaCrawler")
sys.path.insert(0, MC_DIR)
os.chdir(MC_DIR)

import pymysql
from pymongo import MongoClient

# ─── 配置 ─────────────────────────────────────────────
MONGO_URI = "mongodb://10.168.1.80:27018"
MONGO_DB = "mindspider_signal"

MYSQL_HOST = "10.168.1.80"
MYSQL_PORT = 3306
MYSQL_USER = "root"
MYSQL_PASS = "Tangwei7311Yeti."
MYSQL_DB = "fish"

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


def get_video_ids_from_mysql(limit=5):
    """从 MySQL 中获取已有的抖音视频 ID"""
    try:
        conn = pymysql.connect(
            host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER,
            password=MYSQL_PASS, database=MYSQL_DB, charset="utf8mb4",
        )
        cursor = conn.cursor()
        cursor.execute(
            "SELECT aweme_id, title, comment_count FROM douyin_aweme "
            "ORDER BY add_ts DESC LIMIT %s", (limit,)
        )
        rows = cursor.fetchall()
        conn.close()
        if rows:
            print(f"MySQL 中已有 {len(rows)} 个抖音视频:")
            for vid, title, comments in rows:
                t = (title or "")[:50]
                print(f"  {vid}  comments={comments}  {t}")
            return [r[0] for r in rows]
        else:
            print("MySQL 中没有已有的抖音视频")
            return []
    except Exception as e:
        print(f"MySQL 查询失败: {e}")
        return []


async def main():
    print("=" * 60)
    print("抖音爬取诊断脚本 v3")
    print("=" * 60)

    cookie_dict, cookie_str = get_cookie_from_mongo()
    mysql_video_ids = get_video_ids_from_mysql()

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

        if os.path.exists(STEALTH_JS):
            await context.add_init_script(path=STEALTH_JS)
            print("stealth.min.js injected")

        for k, v in cookie_dict.items():
            if not k or not v:
                continue
            await context.add_cookies([{
                "name": k, "value": str(v),
                "domain": ".douyin.com", "path": "/"
            }])

        if "sessionid" in cookie_dict and "LOGIN_STATUS" not in cookie_dict:
            await context.add_cookies([{
                "name": "LOGIN_STATUS", "value": "1",
                "domain": ".douyin.com", "path": "/"
            }])
            print("AUTO-INJECTED LOGIN_STATUS=1")

        page = await context.new_page()

        # ─── 导航到首页 ──────────────────────────────────
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

        # ─── 检查登录 ────────────────────────────────────
        print(f"\n2. 检查登录状态 ...")
        local_storage = await page.evaluate("() => window.localStorage")
        has_user_login = local_storage.get("HasUserLogin", "")
        print(f"   HasUserLogin = '{has_user_login}'")
        if has_user_login == "1":
            print("   login: OK")
        else:
            print("   login: MAYBE NOT LOGGED IN")

        # ═══════════════════════════════════════════════════
        # 策略1: 通过搜索框搜索 (模拟真人)
        # ═══════════════════════════════════════════════════
        print(f"\n{'='*60}")
        print(f"策略1: 首页搜索框输入关键词")
        print(f"{'='*60}")

        search_video_ids = []
        try:
            # 查找搜索框
            search_input = await page.query_selector('input[data-e2e="searchbar-input"], input[placeholder*="搜索"], #search-content-input, input[type="search"]')
            if not search_input:
                # 尝试更宽泛的选择器
                search_input = await page.query_selector('input[class*="search"], input[class*="Search"]')

            if search_input:
                print(f"   找到搜索框，输入关键词: {KEYWORD}")
                await search_input.click()
                await asyncio.sleep(0.5)
                await search_input.fill(KEYWORD)
                await asyncio.sleep(0.5)
                await page.keyboard.press("Enter")
                await asyncio.sleep(8)

                print(f"   搜索后 URL: {page.url}")
                print(f"   搜索后 Title: {await page.title()}")

                # 检查是否到了验证码页
                title = await page.title()
                if "验证" in title:
                    print("   搜索触发了验证码页面!")
                else:
                    # 提取搜索结果
                    await page.evaluate("window.scrollTo(0, 600)")
                    await asyncio.sleep(2)

                    # 从 DOM 提取视频链接
                    dom_videos = await page.evaluate("""
                        () => {
                            const results = [];
                            const links = document.querySelectorAll('a[href*="/video/"]');
                            const seen = new Set();
                            links.forEach(a => {
                                const href = a.getAttribute('href') || '';
                                const match = href.match(/\\/video\\/(\\d+)/);
                                if (!match || seen.has(match[1])) return;
                                seen.add(match[1]);

                                // 向上找容器获取描述
                                let container = a;
                                for (let i = 0; i < 3; i++) {
                                    if (container.parentElement) container = container.parentElement;
                                }
                                results.push({
                                    videoId: match[1],
                                    desc: container.textContent?.trim().slice(0, 100) || '',
                                });
                            });
                            return results;
                        }
                    """)

                    # 也从 RENDER_DATA 提取
                    render_ids = await page.evaluate("""
                        () => {
                            const el = document.getElementById('RENDER_DATA');
                            if (!el) return [];
                            try {
                                const d = JSON.parse(decodeURIComponent(el.textContent));
                                const ids = [];
                                const find = (obj, depth = 0) => {
                                    if (!obj || typeof obj !== 'object' || depth > 8) return;
                                    for (const [k, v] of Object.entries(obj)) {
                                        if (k === 'aweme_id' && typeof v === 'string' && v.length > 5) {
                                            ids.push(v);
                                        }
                                        if (typeof v === 'object' && v !== null) find(v, depth + 1);
                                    }
                                };
                                find(d);
                                return [...new Set(ids)];
                            } catch(e) { return []; }
                        }
                    """)

                    print(f"   DOM 视频链接: {len(dom_videos)}")
                    for v in dom_videos[:5]:
                        print(f"      {v['videoId']}  {v['desc'][:60]}")

                    print(f"   RENDER_DATA aweme_ids: {len(render_ids)}")
                    for rid in render_ids[:5]:
                        print(f"      {rid}")

                    search_video_ids = render_ids or [v["videoId"] for v in dom_videos]
            else:
                print("   未找到搜索框!")
                # dump 页面可交互元素
                inputs = await page.evaluate("""
                    () => Array.from(document.querySelectorAll('input')).map(i => ({
                        type: i.type, placeholder: i.placeholder, className: i.className?.slice(0, 60),
                        id: i.id, name: i.name
                    })).slice(0, 10)
                """)
                print(f"   页面 input 元素: {json.dumps(inputs, ensure_ascii=False, indent=2)}")

        except Exception as e:
            print(f"   策略1 异常: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()

        # ═══════════════════════════════════════════════════
        # 策略2: 从浏览器内发 fetch 请求
        # ═══════════════════════════════════════════════════
        print(f"\n{'='*60}")
        print(f"策略2: 浏览器 fetch 搜索 API")
        print(f"{'='*60}")

        fetch_video_ids = []
        try:
            # 先导航回首页 (如果在验证码页上的话)
            current_title = await page.title()
            if "验证" in current_title:
                print("   当前在验证码页面，先导航回首页...")
                await page.goto("https://www.douyin.com", wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(5)

            # 用 fetch 调用搜索 API
            fetch_result = await page.evaluate("""
                async (keyword) => {
                    try {
                        const params = new URLSearchParams({
                            keyword: keyword,
                            offset: '0',
                            count: '10',
                            search_channel: 'aweme_general',
                            sort_type: '0',
                            publish_time: '0',
                            search_source: 'normal_search',
                            cookie_enabled: 'true',
                            platform: 'PC',
                            aid: '6383',
                            channel: 'channel_pc_web',
                            version_code: '170400',
                            version_name: '17.4.0',
                        });
                        const url = '/aweme/v1/web/general/search/single/?' + params.toString();
                        const resp = await fetch(url, {
                            method: 'GET',
                            headers: {
                                'Accept': 'application/json',
                                'Referer': 'https://www.douyin.com/search/' + encodeURIComponent(keyword),
                            },
                            credentials: 'include',
                        });
                        const data = await resp.json();
                        return {
                            status: resp.status,
                            status_code: data.status_code,
                            dataLen: data.data?.length || 0,
                            search_nil_type: data.search_nil_info?.search_nil_type || '',
                            aweme_ids: (data.data || []).map(d =>
                                d.aweme_info?.aweme_id || d.aweme_mix_info?.mix_items?.[0]?.aweme_id || ''
                            ).filter(Boolean).slice(0, 10),
                            descs: (data.data || []).map(d =>
                                (d.aweme_info?.desc || '').slice(0, 60)
                            ).filter(Boolean).slice(0, 10),
                            keys: Object.keys(data),
                        };
                    } catch(e) {
                        return { error: e.message };
                    }
                }
            """, KEYWORD)

            print(f"   fetch 结果: {json.dumps(fetch_result, ensure_ascii=False, indent=2)}")

            if fetch_result.get("aweme_ids"):
                fetch_video_ids = fetch_result["aweme_ids"]
                print(f"   fetch 搜索成功! {len(fetch_video_ids)} 个视频")

        except Exception as e:
            print(f"   策略2 异常: {type(e).__name__}: {e}")

        # ═══════════════════════════════════════════════════
        # 策略3: API 搜索 (DouYinClient + a_bogus)
        # ═══════════════════════════════════════════════════
        print(f"\n{'='*60}")
        print(f"策略3: API 搜索 (httpx + a_bogus)")
        print(f"{'='*60}")

        api_video_ids = []
        try:
            from media_platform.douyin.client import DouYinClient
            from var import request_keyword_var

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

            request_keyword_var.set(KEYWORD)
            search_res = await dy_client.search_info_by_keyword(keyword=KEYWORD, offset=0)
            data_list = search_res.get("data", [])
            nil_type = search_res.get("search_nil_info", {}).get("search_nil_type", "")

            if data_list:
                api_video_ids = [
                    item.get("aweme_info", {}).get("aweme_id", "")
                    for item in data_list[:MAX_VIDEOS]
                    if item.get("aweme_info", {}).get("aweme_id")
                ]
                print(f"   API 搜索成功! {len(api_video_ids)} 个视频")
            else:
                print(f"   API 搜索失败: search_nil_type={nil_type}")

        except Exception as e:
            print(f"   策略3 异常: {type(e).__name__}: {e}")

        # ─── 选择视频 IDs ────────────────────────────────
        video_ids = search_video_ids or fetch_video_ids or api_video_ids or mysql_video_ids
        if not video_ids:
            print("\n所有方式均未找到视频 ID!")
            await browser.close()
            return

        source = (
            "搜索框" if search_video_ids else
            "fetch" if fetch_video_ids else
            "API" if api_video_ids else
            "MySQL"
        )
        video_ids = video_ids[:MAX_VIDEOS]
        print(f"\n使用 {source} 来源的视频: {video_ids}")

        # ═══════════════════════════════════════════════════
        # 测试评论: 多种方式
        # ═══════════════════════════════════════════════════
        print(f"\n{'='*60}")
        print(f"测试评论获取")
        print(f"{'='*60}")

        # 确保 dy_client 存在
        if "dy_client" not in dir():
            from media_platform.douyin.client import DouYinClient
            from var import request_keyword_var

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
            request_keyword_var.set(KEYWORD)

        total_api = 0
        total_fetch = 0
        total_dom = 0

        for vid in video_ids:
            print(f"\n--- 视频 {vid} ---")

            # 方式A: API 评论 (httpx + a_bogus)
            print(f"  [A: API] ...")
            try:
                res = await dy_client.get_aweme_comments(vid, cursor=0)
                comments = res.get("comments", [])
                status = res.get("status_code", "?")
                print(f"  [A: API] status={status}, 评论={len(comments) if comments else 0}")
                if comments:
                    for c in comments[:3]:
                        u = c.get("user", {}).get("nickname", "?")
                        t = c.get("text", "")[:60]
                        print(f"    {u}: {t}")
                    total_api += len(comments)
            except Exception as e:
                print(f"  [A: API] 异常: {type(e).__name__}: {e}")

            await asyncio.sleep(1)

            # 方式B: 浏览器 fetch 评论 API
            print(f"  [B: fetch] ...")
            try:
                # 先确保在抖音域名下
                current_url = page.url
                if "douyin.com" not in current_url:
                    await page.goto("https://www.douyin.com", wait_until="domcontentloaded", timeout=15000)
                    await asyncio.sleep(3)

                fetch_comments = await page.evaluate("""
                    async (awemeId) => {
                        try {
                            const params = new URLSearchParams({
                                aweme_id: awemeId,
                                cursor: '0',
                                count: '20',
                                item_type: '0',
                                cookie_enabled: 'true',
                                platform: 'PC',
                                aid: '6383',
                                channel: 'channel_pc_web',
                                version_code: '170400',
                                version_name: '17.4.0',
                            });
                            const url = '/aweme/v1/web/comment/list/?' + params.toString();
                            const resp = await fetch(url, {
                                method: 'GET',
                                headers: { 'Accept': 'application/json' },
                                credentials: 'include',
                            });
                            const data = await resp.json();
                            const comments = data.comments || [];
                            return {
                                status: data.status_code,
                                total: comments.length,
                                has_more: data.has_more,
                                samples: comments.slice(0, 3).map(c => ({
                                    user: c.user?.nickname || '?',
                                    text: (c.text || '').slice(0, 60),
                                    ip: c.ip_label || '',
                                    likes: c.digg_count || 0,
                                })),
                                keys: Object.keys(data),
                            };
                        } catch(e) {
                            return { error: e.message };
                        }
                    }
                """, vid)

                print(f"  [B: fetch] {json.dumps(fetch_comments, ensure_ascii=False)[:300]}")
                if fetch_comments.get("total", 0) > 0:
                    total_fetch += fetch_comments["total"]
                    for s in fetch_comments.get("samples", []):
                        print(f"    {s['user']} ({s['ip']}): {s['text']}")

            except Exception as e:
                print(f"  [B: fetch] 异常: {type(e).__name__}: {e}")

            await asyncio.sleep(1)

            # 方式C: 导航到视频页，从 DOM 提取评论
            print(f"  [C: DOM] ...")
            try:
                video_url = f"https://www.douyin.com/video/{vid}"
                await page.goto(video_url, wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(5)

                title = await page.title()
                if "验证" in title:
                    print(f"  [C: DOM] 视频页触发验证码!")
                else:
                    await page.evaluate("window.scrollTo(0, 500)")
                    await asyncio.sleep(2)

                    # 提取评论
                    dom_comments = await page.evaluate("""
                        () => {
                            const results = [];
                            // 尝试各种评论选择器
                            const allEls = document.querySelectorAll('[class*="comment"], [class*="Comment"]');
                            const classNames = new Set();
                            allEls.forEach(el => el.classList.forEach(cls => {
                                if (cls.toLowerCase().includes('comment')) classNames.add(cls);
                            }));

                            // 找重复 class (评论项)
                            const classCounts = {};
                            allEls.forEach(el => {
                                classCounts[el.className] = (classCounts[el.className] || 0) + 1;
                            });

                            const repeated = Object.entries(classCounts)
                                .filter(([k, v]) => v >= 2)
                                .sort((a, b) => b[1] - a[1]);

                            for (const [cls, count] of repeated) {
                                if (count < 2) continue;
                                const sel = '.' + cls.split(' ').filter(c => c).join('.');
                                let items;
                                try { items = document.querySelectorAll(sel); } catch(e) { continue; }
                                if (items.length < 2) continue;
                                const text = items[0].textContent?.trim();
                                if (!text || text.length < 5) continue;

                                items.forEach((item, idx) => {
                                    if (idx >= 20) return;
                                    results.push({
                                        author: (item.querySelector('[class*="name"], [class*="author"], [class*="nick"]') || {}).textContent?.trim() || '?',
                                        content: (item.querySelector('[class*="content"], [class*="text"]') || {}).textContent?.trim() || item.textContent?.trim().slice(0, 100),
                                    });
                                });
                                break;
                            }

                            // 也检查 RENDER_DATA
                            let renderCommentCount = 0;
                            const rd = document.getElementById('RENDER_DATA');
                            if (rd) {
                                try {
                                    const d = JSON.parse(decodeURIComponent(rd.textContent));
                                    const find = (obj, depth = 0) => {
                                        if (!obj || typeof obj !== 'object' || depth > 8) return;
                                        for (const [k, v] of Object.entries(obj)) {
                                            if (k === 'comments' && Array.isArray(v)) {
                                                renderCommentCount += v.length;
                                            }
                                            if (typeof v === 'object' && v !== null) find(v, depth + 1);
                                        }
                                    };
                                    find(d);
                                } catch(e) {}
                            }

                            return {
                                totalCommentEls: allEls.length,
                                classNames: Array.from(classNames).sort().slice(0, 10),
                                domComments: results,
                                renderCommentCount: renderCommentCount,
                            };
                        }
                    """)

                    print(f"  [C: DOM] comment 元素: {dom_comments.get('totalCommentEls', 0)}, "
                          f"RENDER_DATA 评论: {dom_comments.get('renderCommentCount', 0)}")
                    if dom_comments.get("classNames"):
                        print(f"  [C: DOM] class 名: {dom_comments['classNames'][:5]}")
                    dc = dom_comments.get("domComments", [])
                    if dc:
                        print(f"  [C: DOM] 提取到 {len(dc)} 条:")
                        for c in dc[:3]:
                            print(f"    {c['author']}: {c['content'][:60]}")
                        total_dom += len(dc)

            except Exception as e:
                print(f"  [C: DOM] 异常: {type(e).__name__}: {e}")

            await asyncio.sleep(2)

        # ─── 汇总 ────────────────────────────────────────
        print(f"\n{'='*60}")
        print(f"汇总:")
        print(f"  视频来源: {source}, 共 {len(video_ids)} 个")
        print(f"  评论: API={total_api}, fetch={total_fetch}, DOM={total_dom}")
        best = max(total_api, total_fetch, total_dom)
        if best > 0:
            winner = "API" if total_api == best else ("fetch" if total_fetch == best else "DOM")
            print(f"  结论: 评论获取有效! 最佳方式: {winner}")
        else:
            print(f"  结论: 所有评论获取方式均失败")
        print(f"{'='*60}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
