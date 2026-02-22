# -*- coding: utf-8 -*-
"""抖音爬取诊断脚本 v5

已验证: 搜索框方式可以绕过 verify_check，浏览器自动发出的
/search/single/ 请求返回了 data_len=10 的真实数据。

本轮: 完整拦截 API 响应 → 提取 aweme_ids → 测试评论获取。

用法:
  cd /deploy/parallel-universe/mind-spider/DeepSentimentCrawling/MediaCrawler
  python -u ../../scripts/test_dy_comments.py
"""

import asyncio
import json
import os
import sys

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
    return cookies, cookie_str


async def main():
    print("=" * 60)
    print("抖音爬取诊断脚本 v5 (拦截 + 评论测试)")
    print("=" * 60)

    cookie_dict, cookie_str = get_cookie_from_mongo()

    import config
    config.PLATFORM = "dy"
    config.LOGIN_TYPE = "cookie"
    config.COOKIES = cookie_str
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

        # ─── 拦截搜索 API 响应 (完整 body) ───────────────
        search_results = []  # 存储完整的搜索数据
        comment_results = []  # 存储评论数据

        async def handle_response(response):
            url = response.url
            try:
                # 拦截搜索结果
                if "/search/single/" in url and response.status == 200:
                    body = await response.json()
                    if body.get("data"):
                        for item in body["data"]:
                            aweme_info = item.get("aweme_info")
                            if aweme_info:
                                search_results.append(aweme_info)

                # 拦截评论
                if "/comment/list/" in url and "reply" not in url and response.status == 200:
                    body = await response.json()
                    comments = body.get("comments", [])
                    if comments:
                        comment_results.extend(comments)
            except Exception:
                pass

        page.on("response", handle_response)

        # ─── 导航到首页 ──────────────────────────────────
        print(f"\n1. 导航到 douyin.com ...")
        await page.goto("https://www.douyin.com", wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(5)
        print(f"   URL: {page.url}")

        has_user_login = await page.evaluate("() => window.localStorage.getItem('HasUserLogin') || ''")
        print(f"   login: {'OK' if has_user_login == '1' else 'NOT LOGGED IN'}")

        # ─── 搜索框搜索 ─────────────────────────────────
        print(f"\n2. 搜索: '{KEYWORD}' ...")
        search_input = await page.query_selector(
            'input[data-e2e="searchbar-input"], input[placeholder*="搜索"], '
            '#search-content-input, input[type="search"], '
            'input[class*="search"], input[class*="Search"]'
        )
        if not search_input:
            print("   未找到搜索框!")
            await browser.close()
            return

        await search_input.click()
        await asyncio.sleep(0.5)
        await search_input.fill(KEYWORD)
        await asyncio.sleep(0.5)
        await page.keyboard.press("Enter")
        print("   等待搜索结果...")
        await asyncio.sleep(10)

        title = await page.title()
        print(f"   Title: {title}")
        if "验证" in title:
            print("   搜索触发验证码!")
            await browser.close()
            return

        # 滚动触发更多加载
        for i in range(3):
            await page.evaluate(f"window.scrollTo(0, {(i+1)*800})")
            await asyncio.sleep(2)

        # ─── 解析拦截到的搜索数据 ────────────────────────
        print(f"\n3. 拦截到 {len(search_results)} 个视频:")
        video_ids = []
        for info in search_results[:10]:
            aweme_id = info.get("aweme_id", "")
            desc = info.get("desc", "")[:60]
            stats = info.get("statistics", {})
            comment_count = stats.get("comment_count", 0)
            digg_count = stats.get("digg_count", 0)
            author = info.get("author", {}).get("nickname", "?")
            video_ids.append(aweme_id)
            print(f"   {aweme_id}  likes={digg_count}  comments={comment_count}  @{author}  {desc}")

        if not video_ids:
            print("   未拦截到视频数据!")
            await browser.close()
            return

        # ═══════════════════════════════════════════════════
        # 测试评论获取
        # ═══════════════════════════════════════════════════
        print(f"\n{'='*60}")
        print(f"4. 测试评论获取 (3种方式)")
        print(f"{'='*60}")

        test_ids = video_ids[:MAX_VIDEOS]
        total_fetch = 0
        total_navigate = 0
        total_api = 0

        # 创建 DouYinClient (用于 API 方式)
        from media_platform.douyin.client import DouYinClient
        from var import request_keyword_var
        request_keyword_var.set(KEYWORD)

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

        for vid in test_ids:
            print(f"\n--- 视频 {vid} ---")

            # ─── 方式A: 浏览器 fetch (从搜索页发) ─────────
            print(f"  [A: fetch]")
            try:
                fetch_res = await page.evaluate("""
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
                                credentials: 'include',
                            });
                            const data = await resp.json();
                            return {
                                status_code: data.status_code,
                                total: (data.comments || []).length,
                                has_more: data.has_more,
                                samples: (data.comments || []).slice(0, 5).map(c => ({
                                    user: c.user?.nickname || '?',
                                    text: (c.text || '').slice(0, 60),
                                    ip: c.ip_label || '',
                                    likes: c.digg_count || 0,
                                })),
                            };
                        } catch(e) {
                            return { error: e.message };
                        }
                    }
                """, vid)

                ct = fetch_res.get("total", 0)
                print(f"  [A: fetch] status={fetch_res.get('status_code')}, 评论={ct}, has_more={fetch_res.get('has_more')}")
                if ct > 0:
                    total_fetch += ct
                    for s in fetch_res.get("samples", []):
                        print(f"    {s['user']} ({s['ip']}): {s['text']}  [likes={s['likes']}]")
                elif fetch_res.get("error"):
                    print(f"  [A: fetch] error: {fetch_res['error']}")
            except Exception as e:
                print(f"  [A: fetch] 异常: {e}")

            await asyncio.sleep(1)

            # ─── 方式B: 导航到视频页 + 拦截评论 API ───────
            print(f"  [B: navigate+intercept]")
            try:
                comment_results.clear()
                video_url = f"https://www.douyin.com/video/{vid}"
                await page.goto(video_url, wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(6)

                cur_title = await page.title()
                if "验证" in cur_title:
                    print(f"  [B] 视频页触发验证码!")
                else:
                    # 滚动到评论区
                    await page.evaluate("window.scrollTo(0, 600)")
                    await asyncio.sleep(3)

                    ct = len(comment_results)
                    print(f"  [B: navigate] 拦截到 {ct} 条评论")
                    if ct > 0:
                        total_navigate += ct
                        for c in comment_results[:5]:
                            u = c.get("user", {}).get("nickname", "?")
                            t = c.get("text", "")[:60]
                            ip = c.get("ip_label", "")
                            likes = c.get("digg_count", 0)
                            print(f"    {u} ({ip}): {t}  [likes={likes}]")
            except Exception as e:
                print(f"  [B: navigate] 异常: {e}")

            await asyncio.sleep(1)

            # ─── 方式C: API (httpx + a_bogus) ─────────────
            print(f"  [C: API]")
            try:
                res = await dy_client.get_aweme_comments(vid, cursor=0)
                comments = res.get("comments", [])
                status = res.get("status_code", "?")
                ct = len(comments) if comments else 0
                print(f"  [C: API] status={status}, 评论={ct}")
                if ct > 0:
                    total_api += ct
                    for c in comments[:3]:
                        u = c.get("user", {}).get("nickname", "?")
                        t = c.get("text", "")[:60]
                        print(f"    {u}: {t}")
            except Exception as e:
                print(f"  [C: API] 异常: {type(e).__name__}: {e}")

            await asyncio.sleep(2)

        # ─── 汇总 ────────────────────────────────────────
        print(f"\n{'='*60}")
        print(f"汇总:")
        print(f"  搜索: 拦截到 {len(video_ids)} 个视频 (搜索框方式)")
        print(f"  评论: fetch={total_fetch}, navigate+intercept={total_navigate}, API={total_api}")
        best = max(total_fetch, total_navigate, total_api)
        if best > 0:
            methods = []
            if total_fetch > 0: methods.append(f"fetch({total_fetch})")
            if total_navigate > 0: methods.append(f"navigate({total_navigate})")
            if total_api > 0: methods.append(f"API({total_api})")
            print(f"  结论: 评论获取有效! 可用方式: {', '.join(methods)}")
        else:
            print(f"  结论: 搜索成功但评论获取全部失败")
        print(f"{'='*60}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
