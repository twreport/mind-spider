# -*- coding: utf-8 -*-
"""小红书爬取诊断脚本 v1

测试 XHS 的搜索、笔记详情、评论获取，验证 cookie 和签名是否正常。

按 PLATFORM_DEBUG_NOTES.md 的方法论，同时测试多种方案：
  [A] API 搜索 (POST + xhshow 签名)
  [B] 笔记详情 (API + HTML 解析 双路)
  [C] 评论获取 (API 分页)
  [D] 响应拦截 (浏览器搜索 + 拦截 API 响应)

用法:
  cd /deploy/parallel-universe/mind-spider/DeepSentimentCrawling/MediaCrawler
  python -u ../../scripts/test_xhs_search.py
"""

import asyncio
import json
import os
import sys
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
MC_DIR = os.path.join(PROJECT_ROOT, "DeepSentimentCrawling", "MediaCrawler")
sys.path.insert(0, MC_DIR)
os.chdir(MC_DIR)

from pymongo import MongoClient

# ─── 配置 ─────────────────────────────────────────────
MONGO_URI = "mongodb://10.168.1.80:27018"
MONGO_DB = "mindspider_signal"
KEYWORD = "咖啡推荐"
MAX_NOTES = 5
MAX_COMMENTS = 10
STEALTH_JS = os.path.join(MC_DIR, "libs", "stealth.min.js")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


def get_cookie_from_mongo():
    client = MongoClient(MONGO_URI)
    db = client[MONGO_DB]
    doc = db.platform_cookies.find_one({"platform": "xhs", "status": "active"})
    client.close()
    if not doc:
        print("MongoDB 中没有找到 xhs 的 active cookie")
        print("请先运行系统登录流程或手动插入 cookie 到 MongoDB")
        sys.exit(1)
    cookies = doc["cookies"]  # Dict 格式
    cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
    print(f"cookie: {len(cookies)} fields, length={len(cookie_str)}")
    # 检查关键 cookie 字段
    key_fields = ["a1", "web_session", "webId"]
    for f in key_fields:
        val = cookies.get(f, "")
        print(f"  {f}: {'OK' if val else 'MISSING'} ({val[:20]}...)" if val else f"  {f}: MISSING")
    return cookies, cookie_str


async def main():
    print("=" * 60)
    print("小红书爬取诊断脚本 v1")
    print("=" * 60)

    cookie_dict, cookie_str = get_cookie_from_mongo()

    # 设置 config
    import config
    config.PLATFORM = "xhs"
    config.LOGIN_TYPE = "cookie"
    config.COOKIES = cookie_str
    config.KEYWORDS = KEYWORD
    config.ENABLE_GET_COMMENTS = True
    config.ENABLE_GET_SUB_COMMENTS = False
    config.CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES = MAX_COMMENTS

    from playwright.async_api import async_playwright
    from tools import utils

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=USER_AGENT,
        )

        # 注入 stealth.min.js
        if os.path.exists(STEALTH_JS):
            await context.add_init_script(path=STEALTH_JS)
            print("stealth.min.js injected")
        else:
            print(f"WARNING: stealth.min.js not found at {STEALTH_JS}")

        # 注入 cookie
        for k, v in cookie_dict.items():
            if not k or not v:
                continue
            await context.add_cookies([{
                "name": k, "value": str(v),
                "domain": ".xiaohongshu.com", "path": "/"
            }])
        print(f"injected {len(cookie_dict)} cookies")

        page = await context.new_page()

        # ═══════════════════════════════════════════════════
        # 阶段 1: 导航到小红书首页，检查登录态
        # ═══════════════════════════════════════════════════
        print(f"\n{'='*60}")
        print("1. 导航到 xiaohongshu.com，检查登录态")
        print(f"{'='*60}")

        await page.goto("https://www.xiaohongshu.com", wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(3)  # 等待 JS 初始化

        title = await page.title()
        url = page.url
        print(f"  URL: {url}")
        print(f"  Title: {title}")

        if "验证" in title or "登录" in title:
            print("  WARNING: 可能需要验证或登录!")

        # 检查是否能看到用户头像（登录态标志）
        login_check = await page.evaluate("""() => {
            const avatar = document.querySelector('.user-avatar, .side-bar .user, [class*="avatar"]');
            const loginBtn = document.querySelector('[class*="login"], .login-btn');
            return {
                hasAvatar: !!avatar,
                hasLoginBtn: !!loginBtn,
                cookies: document.cookie.length,
            };
        }""")
        print(f"  avatar: {login_check['hasAvatar']}, loginBtn: {login_check['hasLoginBtn']}, cookie_len: {login_check['cookies']}")

        # ═══════════════════════════════════════════════════
        # 阶段 2: API 搜索测试 (POST + xhshow 签名)
        # ═══════════════════════════════════════════════════
        print(f"\n{'='*60}")
        print(f"2. API 搜索测试: '{KEYWORD}'")
        print(f"{'='*60}")

        # 创建 XHS 客户端
        cookie_str_browser, cookie_dict_browser = utils.convert_cookies(await context.cookies())
        from media_platform.xhs.client import XiaoHongShuClient
        from media_platform.xhs.help import get_search_id

        xhs_client = XiaoHongShuClient(
            headers={
                "User-Agent": USER_AGENT,
                "Cookie": cookie_str_browser,
                "Origin": "https://www.xiaohongshu.com",
                "Referer": "https://www.xiaohongshu.com/",
                "Content-Type": "application/json;charset=UTF-8",
            },
            playwright_page=page,
            cookie_dict=cookie_dict_browser,
        )

        # --- 方式A: API 搜索 ---
        note_list = []  # (note_id, xsec_source, xsec_token)
        print(f"\n  [A: API 搜索]")
        try:
            search_res = await xhs_client.get_note_by_keyword(
                keyword=KEYWORD,
                search_id=get_search_id(),
                page=1,
                page_size=20,
            )
            items = search_res.get("items", [])
            print(f"  [A] 返回 {len(items)} 条结果")
            for i, item in enumerate(items[:MAX_NOTES]):
                note_card = item.get("note_card", {})
                note_id = item.get("id", "")
                xsec_token = item.get("xsec_token", "")
                title = note_card.get("display_title", "")[:50]
                user = note_card.get("user", {}).get("nickname", "?")
                liked = note_card.get("interact_info", {}).get("liked_count", "?")
                note_type = note_card.get("type", "?")
                print(f"    [{i+1}] {note_id}  @{user}  likes={liked}  type={note_type}  {title}")
                note_list.append((note_id, "pc_search", xsec_token))

            if not items:
                print("  [A] 搜索返回空! 原始响应:")
                print(f"      {json.dumps(search_res, ensure_ascii=False)[:500]}")
        except Exception as e:
            print(f"  [A] API 搜索异常: {type(e).__name__}: {e}")

        # --- 方式D: 响应拦截 (浏览器搜索) ---
        print(f"\n  [D: 响应拦截搜索]")
        intercepted_notes = []

        async def handle_search_response(response):
            try:
                if "/api/sns/web/v1/search/notes" in response.url and response.status == 200:
                    body = await response.json()
                    if body.get("success") and body.get("data", {}).get("items"):
                        for item in body["data"]["items"]:
                            intercepted_notes.append(item)
            except Exception:
                pass

        page.on("response", handle_search_response)

        try:
            # 导航到搜索页
            search_url = f"https://www.xiaohongshu.com/search_result?keyword={KEYWORD}&source=web_search_result_notes"
            await page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(5)

            search_title = await page.title()
            print(f"  [D] 搜索页 title: {search_title}")

            if "验证" in search_title:
                print("  [D] 搜索页触发验证码!")
            else:
                # 滚动加载更多
                for i in range(2):
                    await page.evaluate(f"window.scrollTo(0, {(i+1)*800})")
                    await asyncio.sleep(2)

                print(f"  [D] 拦截到 {len(intercepted_notes)} 条笔记")
                for i, item in enumerate(intercepted_notes[:5]):
                    nc = item.get("note_card", {})
                    nid = item.get("id", "")
                    title = nc.get("display_title", "")[:50]
                    user = nc.get("user", {}).get("nickname", "?")
                    print(f"    [{i+1}] {nid}  @{user}  {title}")

                    # 如果 API 搜索失败，用拦截到的数据补充
                    if not note_list:
                        xsec_token = item.get("xsec_token", "")
                        note_list.append((nid, "pc_search", xsec_token))
        except Exception as e:
            print(f"  [D] 拦截搜索异常: {type(e).__name__}: {e}")

        page.remove_listener("response", handle_search_response)

        if not note_list:
            print("\n  搜索全部失败，无法继续测试笔记详情和评论")
            await browser.close()
            return

        # ═══════════════════════════════════════════════════
        # 阶段 3: 笔记详情测试
        # ═══════════════════════════════════════════════════
        print(f"\n{'='*60}")
        print(f"3. 笔记详情测试 (前 {min(MAX_NOTES, len(note_list))} 条)")
        print(f"{'='*60}")

        detail_success = 0
        detail_html_success = 0

        for i, (note_id, xsec_source, xsec_token) in enumerate(note_list[:MAX_NOTES]):
            print(f"\n  --- 笔记 {i+1}: {note_id} ---")

            # 方式B-1: API 获取详情
            print(f"  [B1: API detail]")
            try:
                detail = await xhs_client.get_note_by_id(note_id, xsec_source, xsec_token)
                if detail:
                    title = detail.get("display_title", detail.get("title", ""))[:60]
                    desc = detail.get("desc", "")[:80]
                    user = detail.get("user", {}).get("nickname", "?")
                    liked = detail.get("interact_info", {}).get("liked_count", "?")
                    comment_count = detail.get("interact_info", {}).get("comment_count", "?")
                    print(f"  [B1] OK  @{user}  likes={liked}  comments={comment_count}")
                    print(f"  [B1] title: {title}")
                    print(f"  [B1] desc: {desc}")
                    detail_success += 1
                else:
                    print(f"  [B1] 返回空 dict")
            except Exception as e:
                print(f"  [B1] 异常: {type(e).__name__}: {e}")

            await asyncio.sleep(1)

            # 方式B-2: HTML 解析获取详情
            print(f"  [B2: HTML detail]")
            try:
                html_detail = await xhs_client.get_note_by_id_from_html(note_id, xsec_source, xsec_token)
                if html_detail:
                    title = html_detail.get("display_title", html_detail.get("title", ""))[:60]
                    user = html_detail.get("user", {}).get("nickname", "?")
                    print(f"  [B2] OK  @{user}  title: {title}")
                    detail_html_success += 1
                else:
                    print(f"  [B2] 解析返回 None")
            except Exception as e:
                print(f"  [B2] 异常: {type(e).__name__}: {e}")

            await asyncio.sleep(1)

        # ═══════════════════════════════════════════════════
        # 阶段 4: 评论获取测试
        # ═══════════════════════════════════════════════════
        print(f"\n{'='*60}")
        print(f"4. 评论获取测试")
        print(f"{'='*60}")

        total_comments = 0
        notes_with_comments = 0

        for i, (note_id, xsec_source, xsec_token) in enumerate(note_list[:MAX_NOTES]):
            print(f"\n  --- 笔记 {i+1}: {note_id} ---")

            # 方式C: API 评论
            print(f"  [C: API comments]")
            try:
                comments_res = await xhs_client.get_note_comments(
                    note_id=note_id,
                    xsec_token=xsec_token,
                    cursor="",
                )
                comments = comments_res.get("comments", [])
                has_more = comments_res.get("has_more", False)
                cursor = comments_res.get("cursor", "")
                ct = len(comments)
                print(f"  [C] 获取 {ct} 条评论, has_more={has_more}")

                if ct > 0:
                    total_comments += ct
                    notes_with_comments += 1
                    for c in comments[:5]:
                        user = c.get("user_info", {}).get("nickname", "?")
                        content = c.get("content", "")[:60]
                        like_count = c.get("like_count", 0)
                        sub_count = c.get("sub_comment_count", 0)
                        ip = c.get("ip_location", "")
                        print(f"    @{user} ({ip}): {content}  [likes={like_count}, replies={sub_count}]")
                else:
                    # 打印原始响应帮助调试
                    keys = list(comments_res.keys()) if isinstance(comments_res, dict) else type(comments_res).__name__
                    print(f"  [C] 无评论, 响应 keys: {keys}")

            except Exception as e:
                print(f"  [C] 异常: {type(e).__name__}: {e}")

            await asyncio.sleep(2)

        # ═══════════════════════════════════════════════════
        # 汇总
        # ═══════════════════════════════════════════════════
        print(f"\n{'='*60}")
        print(f"汇总:")
        print(f"  搜索:")
        print(f"    API 搜索: {len(note_list)} 条 {'OK' if note_list else 'FAIL'}")
        print(f"    响应拦截: {len(intercepted_notes)} 条 {'OK' if intercepted_notes else 'FAIL'}")
        print(f"  笔记详情:")
        print(f"    API: {detail_success}/{min(MAX_NOTES, len(note_list))} 成功")
        print(f"    HTML: {detail_html_success}/{min(MAX_NOTES, len(note_list))} 成功")
        print(f"  评论:")
        print(f"    共 {total_comments} 条 (来自 {notes_with_comments} 篇笔记)")

        # 给出结论
        issues = []
        if not note_list and not intercepted_notes:
            issues.append("搜索完全失败，检查 cookie 是否有效")
        if detail_success == 0 and detail_html_success == 0:
            issues.append("笔记详情获取全部失败")
        if total_comments == 0:
            issues.append("评论获取全部为空")

        if not issues:
            print(f"\n  结论: XHS 爬取功能正常!")
        else:
            print(f"\n  结论: 存在问题:")
            for issue in issues:
                print(f"    - {issue}")

        print(f"{'='*60}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
