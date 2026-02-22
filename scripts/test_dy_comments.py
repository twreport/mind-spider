# -*- coding: utf-8 -*-
"""抖音爬取诊断脚本

直接复用 MediaCrawler 模块，测试抖音的搜索、视频详情、评论获取。
无需启动完整系统，独立运行。

用法:
  cd /deploy/parallel-universe/mind-spider/DeepSentimentCrawling/MediaCrawler
  uv run python ../../scripts/test_dy_comments.py
"""

import asyncio
import os
import sys

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


async def main():
    print("=" * 60)
    print("抖音爬取诊断脚本")
    print("=" * 60)

    cookie_dict, cookie_str = get_cookie_from_mongo()

    # ─── 设置 config ───────────────────────────────────
    import config
    config.PLATFORM = "dy"
    config.LOGIN_TYPE = "cookie"
    config.COOKIES = cookie_str
    config.HEADLESS = True
    config.SAVE_DATA_OPTION = "json"  # 不写数据库，只看日志
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

        # ─── 导航到抖音 ───────────────────────────────
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

        # ─── 检查登录状态 ─────────────────────────────
        print(f"\n2. 检查登录状态 ...")
        local_storage = await page.evaluate("() => window.localStorage")
        has_user_login = local_storage.get("HasUserLogin", "")
        print(f"   localStorage.HasUserLogin = '{has_user_login}'")

        _, browser_cookie_dict = utils.convert_cookies(await context.cookies())
        login_status = browser_cookie_dict.get("LOGIN_STATUS", "")
        print(f"   cookie LOGIN_STATUS = '{login_status}'")

        if has_user_login == "1" or login_status == "1":
            print("   ✅ 已登录")
        else:
            print("   ⚠️ 可能未登录，继续尝试...")

        # ─── 创建 client ─────────────────────────────
        print(f"\n3. 创建 DouYinClient ...")
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
        print(f"   Client created, UA={user_agent[:60]}...")

        # ─── 测试搜索 ─────────────────────────────────
        print(f"\n4. 搜索关键词: '{KEYWORD}' ...")
        from var import request_keyword_var
        request_keyword_var.set(KEYWORD)

        try:
            search_res = await dy_client.search_info_by_keyword(keyword=KEYWORD, offset=0)
            status_code = search_res.get("status_code", "?")
            data_list = search_res.get("data", [])
            print(f"   status_code={status_code}, 返回 {len(data_list)} 个结果")

            if status_code != 0:
                print(f"   ❌ 搜索失败: status_msg={search_res.get('status_msg')}")
                print(f"   完整响应 keys: {list(search_res.keys())}")
                # 输出前500字符帮助诊断
                import json
                print(f"   响应: {json.dumps(search_res, ensure_ascii=False)[:500]}")
                await browser.close()
                return

            video_ids = []
            for item in data_list[:MAX_VIDEOS]:
                aweme_info = item.get("aweme_info", {})
                if not aweme_info:
                    continue
                aweme_id = aweme_info.get("aweme_id", "")
                desc = aweme_info.get("desc", "")[:50]
                stats = aweme_info.get("statistics", {})
                comment_count = stats.get("comment_count", 0)
                digg_count = stats.get("digg_count", 0)
                video_ids.append(aweme_id)
                print(f"   📹 {aweme_id}  likes={digg_count}  comments={comment_count}  {desc}")

            if not video_ids:
                print("   ❌ 没有找到视频")
                await browser.close()
                return

        except Exception as e:
            print(f"   ❌ 搜索异常: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            await browser.close()
            return

        # ─── 测试视频详情 ──────────────────────────────
        print(f"\n5. 获取视频详情: {video_ids[0]} ...")
        try:
            detail = await dy_client.get_video_by_id(video_ids[0])
            if detail:
                desc = detail.get("desc", "")[:60]
                stats = detail.get("statistics", {})
                print(f"   ✅ 标题: {desc}")
                print(f"   点赞={stats.get('digg_count')}, 评论={stats.get('comment_count')}, 分享={stats.get('share_count')}")
            else:
                print(f"   ⚠️ 详情为空")
        except Exception as e:
            print(f"   ❌ 详情异常: {type(e).__name__}: {e}")

        # ─── 测试评论获取 ──────────────────────────────
        print(f"\n6. 获取评论 ...")
        total_comments = 0
        for vid in video_ids:
            print(f"\n   --- 视频 {vid} ---")
            try:
                comments_res = await dy_client.get_aweme_comments(vid, cursor=0)
                has_more = comments_res.get("has_more", 0)
                cursor = comments_res.get("cursor", 0)
                comments = comments_res.get("comments", [])
                status_code = comments_res.get("status_code", "?")

                print(f"   status={status_code}, 评论数={len(comments) if comments else 0}, has_more={has_more}")

                if comments:
                    for c in comments[:5]:
                        user = c.get("user", {})
                        text = c.get("text", "")[:60]
                        ip = c.get("ip_label", "")
                        likes = c.get("digg_count", 0)
                        print(f"      💬 {user.get('nickname','?')} ({ip}): {text}  [likes={likes}]")
                    if len(comments) > 5:
                        print(f"      ... 还有 {len(comments) - 5} 条")
                    total_comments += len(comments)
                else:
                    print(f"   ⚠️ 无评论, 完整响应 keys: {list(comments_res.keys())}")

            except Exception as e:
                print(f"   ❌ 评论异常: {type(e).__name__}: {e}")

            await asyncio.sleep(2)

        # ─── 汇总 ──────────────────────────────────────
        print(f"\n{'='*60}")
        print(f"汇总:")
        print(f"  搜索: {len(video_ids)} 个视频")
        print(f"  评论: {total_comments} 条")
        if total_comments > 0:
            print(f"  结论: ✅ 抖音搜索+评论均正常!")
        elif video_ids:
            print(f"  结论: ⚠️ 搜索正常但评论获取失败")
        else:
            print(f"  结论: ❌ 搜索和评论均失败")
        print(f"{'='*60}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
