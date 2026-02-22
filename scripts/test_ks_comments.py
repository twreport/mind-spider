# -*- coding: utf-8 -*-
"""快手评论获取诊断脚本

从 MongoDB 取 cookie，从 MySQL 取一个已爬取的视频 ID，
用多种方式尝试获取评论，找出可行方案。

方式:
  1. httpx 直接 POST（原始方式）
  2. httpx + 视频页 Referer
  3. Playwright page.evaluate(fetch) 从首页
  4. Playwright 导航到视频页后 page.evaluate(fetch)
  5. Playwright 导航到视频页后，拦截浏览器自己发出的评论请求
  6. Playwright context.request.post (带 Referer)

用法:
  cd /deploy/parallel-universe/mind-spider
  uv run python scripts/test_ks_comments.py
"""

import asyncio
import json
import os
import sys
import time

import httpx
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

GRAPHQL_URL = "https://www.kuaishou.com/graphql"

COMMENT_QUERY = """query commentListQuery($photoId: String, $pcursor: String) {
  visionCommentList(photoId: $photoId, pcursor: $pcursor) {
    commentCount
    pcursor
    rootComments {
      commentId
      authorId
      authorName
      content
      headurl
      timestamp
      likedCount
      realLikedCount
      liked
      status
      authorLiked
      subCommentCount
      subCommentsPcursor
      subComments {
        commentId
        authorId
        authorName
        content
        headurl
        timestamp
        likedCount
        realLikedCount
        liked
        status
        authorLiked
        replyToUserName
        replyTo
        __typename
      }
      __typename
    }
    __typename
  }
}"""

STEALTH_JS = os.path.join(
    os.path.dirname(__file__),
    "..",
    "DeepSentimentCrawling",
    "MediaCrawler",
    "libs",
    "stealth.min.js",
)


# ─── 工具函数 ──────────────────────────────────────────
def get_cookie_from_mongo():
    """从 MongoDB 获取快手 cookie"""
    client = MongoClient(MONGO_URI)
    db = client[MONGO_DB]
    doc = db.platform_cookies.find_one({"platform": "ks", "status": "active"})
    client.close()
    if not doc:
        print("❌ MongoDB 中没有找到 ks 的 active cookie")
        sys.exit(1)
    cookies = doc["cookies"]
    cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
    print(f"✅ 获取到 ks cookie，共 {len(cookies)} 个字段，字符串长度 {len(cookie_str)}")
    # 打印关键 cookie
    for key in ["passToken", "kuaishou.web.cp.api_ph", "did", "didv", "userId", "kuaishou.server.web_st"]:
        val = cookies.get(key, "")
        print(f"   {key}: {'YES' if val else 'NO'} ({len(val)} chars)" if val else f"   {key}: NO")
    return cookies, cookie_str


def get_video_id_from_mysql():
    """从 MySQL 获取一个有内容的快手视频 ID"""
    conn = pymysql.connect(
        host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER,
        password=MYSQL_PASS, database=MYSQL_DB, charset="utf8mb4",
    )
    cursor = conn.cursor()
    # 取最近的几个视频
    cursor.execute(
        "SELECT video_id, title, liked_count FROM kuaishou_video "
        "ORDER BY add_ts DESC LIMIT 5"
    )
    rows = cursor.fetchall()
    conn.close()
    if not rows:
        print("❌ MySQL 中没有找到快手视频")
        sys.exit(1)
    print(f"\n📹 最近的快手视频:")
    for vid, title, likes in rows:
        print(f"   {vid}  likes={likes}  {title[:40]}")
    video_id = rows[0][0]
    print(f"\n🎯 使用视频 ID: {video_id}")
    return video_id


def build_comment_payload(photo_id, pcursor=""):
    return {
        "operationName": "commentListQuery",
        "variables": {"photoId": photo_id, "pcursor": pcursor},
        "query": COMMENT_QUERY,
    }


def print_result(label, data):
    """打印评论结果"""
    if data is None:
        print(f"   [{label}] ❌ 请求失败")
        return
    if data.get("errors"):
        print(f"   [{label}] ❌ GraphQL errors: {data['errors']}")
        return
    vcl = data.get("data", {}).get("visionCommentList", {})
    if not vcl:
        print(f"   [{label}] ⚠️ 无 visionCommentList, keys={list(data.get('data', {}).keys())}")
        return
    comment_count = vcl.get("commentCount")
    pcursor = vcl.get("pcursor")
    root = vcl.get("rootComments", [])
    print(f"   [{label}] commentCount={comment_count}, pcursor={pcursor}, rootComments={len(root) if root else 0}")
    if root:
        for c in root[:3]:
            print(f"      💬 {c.get('authorName','?')}: {c.get('content','')[:50]}")
        if len(root) > 3:
            print(f"      ... 还有 {len(root)-3} 条")


def build_headers(cookie_str, referer="https://www.kuaishou.com"):
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Cookie": cookie_str,
        "Origin": "https://www.kuaishou.com",
        "Referer": referer,
        "Content-Type": "application/json;charset=UTF-8",
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }


# ─── 测试方式 ──────────────────────────────────────────
async def test_httpx_basic(video_id, cookie_str):
    """方式1: httpx 直接 POST（和原始代码一致）"""
    print("\n━━━ 方式1: httpx 直接 POST (Referer=首页) ━━━")
    payload = build_comment_payload(video_id)
    headers = build_headers(cookie_str)
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(GRAPHQL_URL, content=body, headers=headers, timeout=15)
        print(f"   HTTP {resp.status_code}, 长度 {len(resp.text)}")
        data = resp.json()
        print_result("httpx基础", data)
        return data
    except Exception as e:
        print(f"   ❌ 异常: {type(e).__name__}: {e}")
        return None


async def test_httpx_video_referer(video_id, cookie_str):
    """方式2: httpx + 视频页 Referer"""
    print("\n━━━ 方式2: httpx + 视频页 Referer ━━━")
    payload = build_comment_payload(video_id)
    referer = f"https://www.kuaishou.com/short-video/{video_id}"
    headers = build_headers(cookie_str, referer=referer)
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(GRAPHQL_URL, content=body, headers=headers, timeout=15)
        print(f"   HTTP {resp.status_code}, 长度 {len(resp.text)}")
        data = resp.json()
        print_result("httpx+视频Referer", data)
        return data
    except Exception as e:
        print(f"   ❌ 异常: {type(e).__name__}: {e}")
        return None


async def test_httpx_no_cookie(video_id):
    """方式2b: httpx 无 cookie（对照组）"""
    print("\n━━━ 方式2b: httpx 无 cookie（对照组） ━━━")
    payload = build_comment_payload(video_id)
    headers = build_headers("", referer=f"https://www.kuaishou.com/short-video/{video_id}")
    del headers["Cookie"]
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(GRAPHQL_URL, content=body, headers=headers, timeout=15)
        print(f"   HTTP {resp.status_code}, 长度 {len(resp.text)}")
        data = resp.json()
        print_result("httpx无cookie", data)
        return data
    except Exception as e:
        print(f"   ❌ 异常: {type(e).__name__}: {e}")
        return None


async def test_playwright_homepage(video_id, cookie_dict):
    """方式3: Playwright 从首页 page.evaluate(fetch)"""
    print("\n━━━ 方式3: Playwright 首页 page.evaluate(fetch) ━━━")
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        )
        # 注入 stealth
        if os.path.exists(STEALTH_JS):
            await context.add_init_script(path=STEALTH_JS)

        # 注入 cookie
        for k, v in cookie_dict.items():
            if not k or not v:
                continue
            await context.add_cookies([{"name": k, "value": str(v), "domain": ".kuaishou.com", "path": "/"}])

        page = await context.new_page()
        await page.goto("https://www.kuaishou.com/?isHome=1", wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(3)
        print(f"   页面 URL: {page.url}")
        print(f"   页面 title: {await page.title()}")

        payload = build_comment_payload(video_id)
        try:
            data = await page.evaluate("""
                async (params) => {
                    const response = await fetch(params.url, {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json;charset=UTF-8'},
                        body: JSON.stringify(params.data),
                        credentials: 'include',
                    });
                    return await response.json();
                }
            """, {"url": GRAPHQL_URL, "data": payload})
            print_result("Playwright首页fetch", data)
        except Exception as e:
            print(f"   ❌ 异常: {type(e).__name__}: {e}")
            data = None

        await browser.close()
        return data


async def test_playwright_video_page(video_id, cookie_dict):
    """方式4: Playwright 导航到视频页后 page.evaluate(fetch)"""
    print("\n━━━ 方式4: Playwright 视频页 page.evaluate(fetch) ━━━")
    from playwright.async_api import async_playwright

    video_url = f"https://www.kuaishou.com/short-video/{video_id}"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        )
        if os.path.exists(STEALTH_JS):
            await context.add_init_script(path=STEALTH_JS)

        for k, v in cookie_dict.items():
            if not k or not v:
                continue
            await context.add_cookies([{"name": k, "value": str(v), "domain": ".kuaishou.com", "path": "/"}])

        page = await context.new_page()
        print(f"   导航到: {video_url}")
        await page.goto(video_url, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(5)
        print(f"   页面 URL: {page.url}")
        print(f"   页面 title: {await page.title()}")

        payload = build_comment_payload(video_id)
        try:
            data = await page.evaluate("""
                async (params) => {
                    const response = await fetch(params.url, {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json;charset=UTF-8'},
                        body: JSON.stringify(params.data),
                        credentials: 'include',
                    });
                    return await response.json();
                }
            """, {"url": GRAPHQL_URL, "data": payload})
            print_result("Playwright视频页fetch", data)
        except Exception as e:
            print(f"   ❌ 异常: {type(e).__name__}: {e}")
            data = None

        await browser.close()
        return data


async def test_playwright_intercept(video_id, cookie_dict):
    """方式5: 导航到视频页，拦截浏览器自身发出的评论 GraphQL 请求"""
    print("\n━━━ 方式5: Playwright 拦截浏览器自身的评论请求 ━━━")
    from playwright.async_api import async_playwright

    video_url = f"https://www.kuaishou.com/short-video/{video_id}"
    captured = {"data": None}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        )
        if os.path.exists(STEALTH_JS):
            await context.add_init_script(path=STEALTH_JS)

        for k, v in cookie_dict.items():
            if not k or not v:
                continue
            await context.add_cookies([{"name": k, "value": str(v), "domain": ".kuaishou.com", "path": "/"}])

        page = await context.new_page()

        # 拦截 GraphQL 请求
        graphql_requests = []

        async def handle_response(response):
            if "/graphql" in response.url:
                try:
                    body = await response.json()
                    op = "unknown"
                    # 从请求体中获取 operationName
                    req = response.request
                    if req.post_data:
                        try:
                            req_body = json.loads(req.post_data)
                            op = req_body.get("operationName", "unknown")
                        except Exception:
                            pass
                    graphql_requests.append({"op": op, "data": body})
                    if "commentList" in op.lower() or "comment" in op.lower():
                        captured["data"] = body
                        print(f"   🎯 捕获到评论请求: op={op}")
                        print_result("浏览器自身请求", body)
                except Exception as e:
                    pass

        page.on("response", handle_response)

        print(f"   导航到: {video_url}")
        await page.goto(video_url, wait_until="domcontentloaded", timeout=20000)
        # 等待页面加载评论
        await asyncio.sleep(8)

        print(f"   页面 URL: {page.url}")
        print(f"   页面 title: {await page.title()}")
        print(f"   捕获到 {len(graphql_requests)} 个 GraphQL 请求:")
        for req in graphql_requests:
            vcl = req["data"].get("data", {}).get("visionCommentList")
            extra = ""
            if vcl:
                extra = f" commentCount={vcl.get('commentCount')}, rootComments={len(vcl.get('rootComments') or [])}"
            print(f"      op={req['op']}{extra}")

        # 尝试滚动到评论区
        if not captured["data"] or not captured["data"].get("data", {}).get("visionCommentList", {}).get("rootComments"):
            print("\n   📜 尝试滚动页面触发评论加载...")
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
            await asyncio.sleep(3)
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(5)
            print(f"   滚动后共捕获 {len(graphql_requests)} 个 GraphQL 请求:")
            for req in graphql_requests:
                vcl = req["data"].get("data", {}).get("visionCommentList")
                extra = ""
                if vcl:
                    extra = f" commentCount={vcl.get('commentCount')}, rootComments={len(vcl.get('rootComments') or [])}"
                print(f"      op={req['op']}{extra}")

        # 如果还是没有捕获到评论，检查 DOM 中是否有评论
        comment_dom = await page.evaluate("""
            () => {
                const comments = document.querySelectorAll('[class*="comment"]');
                return {
                    count: comments.length,
                    texts: Array.from(comments).slice(0, 3).map(c => c.textContent?.slice(0, 80) || ''),
                };
            }
        """)
        print(f"\n   DOM 中 class 含 'comment' 的元素: {comment_dom['count']} 个")
        for t in comment_dom["texts"]:
            if t.strip():
                print(f"      {t.strip()[:60]}")

        await browser.close()
        return captured["data"]


async def test_playwright_context_request(video_id, cookie_dict):
    """方式6: Playwright context.request.post (API 请求，带自定义 Referer)"""
    print("\n━━━ 方式6: Playwright context.request.post ━━━")
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        )

        for k, v in cookie_dict.items():
            if not k or not v:
                continue
            await context.add_cookies([{"name": k, "value": str(v), "domain": ".kuaishou.com", "path": "/"}])

        payload = build_comment_payload(video_id)
        referer = f"https://www.kuaishou.com/short-video/{video_id}"

        try:
            resp = await context.request.post(
                GRAPHQL_URL,
                headers={
                    "Content-Type": "application/json;charset=UTF-8",
                    "Origin": "https://www.kuaishou.com",
                    "Referer": referer,
                },
                data=payload,
            )
            print(f"   HTTP {resp.status}, 长度 {len(await resp.text())}")
            data = await resp.json()
            print_result("context.request", data)
        except Exception as e:
            print(f"   ❌ 异常: {type(e).__name__}: {e}")
            data = None

        await browser.close()
        return data


async def test_curl(video_id, cookie_str):
    """方式7: curl 子进程（参考 tieba 成功案例）"""
    print("\n━━━ 方式7: curl 子进程 ━━━")
    import subprocess

    payload = build_comment_payload(video_id)
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    referer = f"https://www.kuaishou.com/short-video/{video_id}"

    cmd = [
        "curl", "-sS",
        "--max-time", "15",
        "-X", "POST",
        "-H", "Content-Type: application/json;charset=UTF-8",
        "-H", f"Cookie: {cookie_str}",
        "-H", f"Referer: {referer}",
        "-H", "Origin: https://www.kuaishou.com",
        "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "-d", body,
        GRAPHQL_URL,
    ]
    try:
        result = await asyncio.to_thread(subprocess.run, cmd, capture_output=True, text=True, timeout=20)
        if result.returncode != 0:
            print(f"   ❌ curl 失败 (rc={result.returncode}): {result.stderr}")
            return None
        print(f"   响应长度: {len(result.stdout)}")
        data = json.loads(result.stdout)
        print_result("curl", data)
        return data
    except Exception as e:
        print(f"   ❌ 异常: {type(e).__name__}: {e}")
        return None


# ─── 主函数 ────────────────────────────────────────────
async def main():
    print("=" * 60)
    print("快手评论获取诊断脚本")
    print("=" * 60)

    cookie_dict, cookie_str = get_cookie_from_mongo()
    video_id = get_video_id_from_mysql()

    print("\n" + "=" * 60)
    print("开始测试各种方式")
    print("=" * 60)

    results = {}

    # 1. httpx 基础
    results["httpx基础"] = await test_httpx_basic(video_id, cookie_str)

    # 2. httpx + 视频 Referer
    results["httpx+Referer"] = await test_httpx_video_referer(video_id, cookie_str)

    # 2b. httpx 无 cookie
    results["httpx无cookie"] = await test_httpx_no_cookie(video_id)

    # 7. curl (不需要 Playwright，先测)
    results["curl"] = await test_curl(video_id, cookie_str)

    # 3. Playwright 首页 fetch
    results["PW首页"] = await test_playwright_homepage(video_id, cookie_dict)

    # 4. Playwright 视频页 fetch
    results["PW视频页"] = await test_playwright_video_page(video_id, cookie_dict)

    # 5. Playwright 拦截浏览器自身请求
    results["PW拦截"] = await test_playwright_intercept(video_id, cookie_dict)

    # 6. Playwright context.request
    results["PW_context"] = await test_playwright_context_request(video_id, cookie_dict)

    # ─── 汇总 ──────────────────────────────────────────
    print("\n" + "=" * 60)
    print("汇总结果")
    print("=" * 60)
    for name, data in results.items():
        if data is None:
            status = "❌ 失败"
        elif data.get("errors"):
            status = "❌ GraphQL错误"
        else:
            vcl = data.get("data", {}).get("visionCommentList", {})
            count = vcl.get("commentCount")
            root = vcl.get("rootComments", [])
            n = len(root) if root else 0
            if n > 0:
                status = f"✅ 成功! {n} 条评论 (commentCount={count})"
            elif count and count > 0:
                status = f"⚠️ commentCount={count} 但 rootComments=0"
            else:
                status = f"⛔ commentCount={count}, rootComments=0"
        print(f"  {name:20s} → {status}")


if __name__ == "__main__":
    asyncio.run(main())
