# -*- coding: utf-8 -*-
"""抖音爬取诊断脚本 v4

上一轮发现:
  - API (httpx + a_bogus): verify_check
  - 浏览器 fetch: verify_check
  - 直接导航到 /search/ URL: 验证码中间页
  - 首页搜索框: 到达了真正的搜索页面! 但 DOM 提取为空

本轮重点: 深入分析搜索框到达的搜索结果页，提取视频数据。

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


# ─── 全面的页面探测 JS ───────────────────────────────
DEEP_PAGE_ANALYSIS_JS = """
() => {
    const r = {};

    // 1. 所有链接 (去重)
    const allLinks = {};
    document.querySelectorAll('a[href]').forEach(a => {
        const href = a.getAttribute('href') || '';
        if (href.length < 3 || href === '#' || href.startsWith('javascript:')) return;
        // 归类链接
        let category = 'other';
        if (href.includes('/video/')) category = 'video';
        else if (href.includes('/note/')) category = 'note';
        else if (href.includes('/user/')) category = 'user';
        else if (href.includes('/search')) category = 'search';
        else if (href.includes('/live/')) category = 'live';
        else if (href.includes('/discover')) category = 'discover';
        if (!allLinks[category]) allLinks[category] = [];
        if (allLinks[category].length < 5) {
            allLinks[category].push(href.slice(0, 120));
        } else if (allLinks[category].length === 5) {
            allLinks[category].push('...(more)');
        }
    });
    r.linkCategories = allLinks;
    r.totalLinks = document.querySelectorAll('a[href]').length;

    // 2. 页面中所有有 id 的 script 标签
    const scripts = [];
    document.querySelectorAll('script[id]').forEach(s => {
        scripts.push({
            id: s.id,
            type: s.type || '',
            contentLen: (s.textContent || '').length,
        });
    });
    r.namedScripts = scripts;

    // 3. RENDER_DATA 分析
    const rd = document.getElementById('RENDER_DATA');
    if (rd) {
        r.renderDataLen = (rd.textContent || '').length;
        try {
            const decoded = decodeURIComponent(rd.textContent);
            const d = JSON.parse(decoded);
            r.renderDataTopKeys = Object.keys(d).slice(0, 20);
            // 深度搜索有用的 key
            const interesting = {};
            const scan = (obj, path = '', depth = 0) => {
                if (!obj || typeof obj !== 'object' || depth > 6) return;
                for (const [k, v] of Object.entries(obj)) {
                    const p = path + '.' + k;
                    const kl = k.toLowerCase();
                    if (['aweme_id', 'aweme_list', 'video_list', 'feeds', 'data'].includes(k) && v) {
                        interesting[p] = {
                            type: typeof v,
                            isArray: Array.isArray(v),
                            length: Array.isArray(v) ? v.length : (typeof v === 'string' ? v.length : null),
                            sample: JSON.stringify(v).slice(0, 200),
                        };
                    }
                    if (typeof v === 'object' && v !== null) scan(v, p, depth + 1);
                }
            };
            scan(d);
            r.renderDataInteresting = interesting;
        } catch(e) {
            r.renderDataError = e.message;
        }
    }

    // 4. 检查全局变量
    const globals = {};
    for (const key of ['__NEXT_DATA__', '__RENDER_DATA__', 'RENDER_DATA', '__data', '__INITIAL_STATE__',
                        '__PRELOADED_STATE__', '__APP_DATA__', '__APOLLO_STATE__', '_SSR_DATA',
                        'INITIAL_STATE', 'pageData']) {
        if (window[key]) {
            globals[key] = typeof window[key] === 'object' ?
                `object(keys: ${Object.keys(window[key]).slice(0, 10).join(', ')})` :
                typeof window[key];
        }
    }
    r.globalVars = globals;

    // 5. 页面可见内容样本
    r.bodyTextSample = document.body?.innerText?.slice(0, 3000) || 'EMPTY';

    // 6. 所有 img 标签 (视频封面通常有图片)
    const images = [];
    document.querySelectorAll('img[src]').forEach(img => {
        const src = img.getAttribute('src') || '';
        if (src.includes('aweme') || src.includes('douyinpic') || src.includes('byte') || src.includes('tiktok')) {
            images.push(src.slice(0, 120));
        }
    });
    r.videoRelatedImages = images.slice(0, 10);
    r.totalImages = document.querySelectorAll('img[src]').length;

    // 7. 检查 iframe
    r.iframes = Array.from(document.querySelectorAll('iframe')).map(f => ({
        src: (f.src || '').slice(0, 100),
        id: f.id,
    })).slice(0, 5);

    // 8. 检查是否有 video 元素 (说明有视频内容)
    r.videoElements = document.querySelectorAll('video').length;

    return r;
}
"""


async def main():
    print("=" * 60)
    print("抖音爬取诊断脚本 v4 (深度分析搜索结果页)")
    print("=" * 60)

    cookie_dict, cookie_str = get_cookie_from_mongo()

    import config
    config.PLATFORM = "dy"
    config.LOGIN_TYPE = "cookie"
    config.COOKIES = cookie_str

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

        # 监听网络请求 (捕获搜索 API 响应)
        api_responses = []

        async def handle_response(response):
            url = response.url
            if "search" in url and ("aweme" in url or "general" in url):
                try:
                    body = await response.json()
                    api_responses.append({
                        "url": url[:200],
                        "status": response.status,
                        "data_len": len(body.get("data", [])) if isinstance(body.get("data"), list) else 0,
                        "keys": list(body.keys())[:10],
                        "body_sample": json.dumps(body, ensure_ascii=False)[:500],
                    })
                except Exception:
                    api_responses.append({"url": url[:200], "status": response.status, "error": "not json"})

        page.on("response", handle_response)

        # ─── 导航到首页 ──────────────────────────────────
        print(f"\n1. 导航到 douyin.com ...")
        await page.goto("https://www.douyin.com", wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(5)
        print(f"   URL: {page.url}")
        print(f"   Title: {await page.title()}")

        # ─── 检查登录 ────────────────────────────────────
        has_user_login = await page.evaluate("() => window.localStorage.getItem('HasUserLogin') || ''")
        print(f"   HasUserLogin = '{has_user_login}'")

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

        # 等待较长时间让搜索结果加载
        print("   等待搜索结果加载...")
        await asyncio.sleep(10)

        print(f"   URL: {page.url}")
        print(f"   Title: {await page.title()}")

        title = await page.title()
        if "验证" in title:
            print("   搜索触发了验证码!")
            await browser.close()
            return

        # ─── 检查拦截到的 API 响应 ───────────────────────
        print(f"\n3. 拦截到的搜索 API 响应: {len(api_responses)} 个")
        for i, resp in enumerate(api_responses):
            print(f"   [{i}] status={resp.get('status')} url={resp.get('url','')[:100]}")
            if resp.get("data_len"):
                print(f"       data_len={resp['data_len']}")
            if resp.get("body_sample"):
                print(f"       body: {resp['body_sample'][:300]}")

        # ─── 多次滚动加载 ────────────────────────────────
        print(f"\n4. 滚动页面加载更多...")
        for i in range(4):
            scroll_y = (i + 1) * 500
            await page.evaluate(f"window.scrollTo(0, {scroll_y})")
            await asyncio.sleep(2)
            print(f"   scroll {i+1}: y={scroll_y}")

        # 检查滚动后有没有新的 API 响应
        if len(api_responses) > 0:
            print(f"   滚动后共 {len(api_responses)} 个 API 响应")

        # ─── 全面分析页面 ────────────────────────────────
        print(f"\n5. 深度分析页面结构...")
        analysis = await page.evaluate(DEEP_PAGE_ANALYSIS_JS)

        print(f"\n   === 链接分析 ===")
        print(f"   总链接数: {analysis.get('totalLinks', 0)}")
        for cat, links in analysis.get("linkCategories", {}).items():
            print(f"   [{cat}] ({len(links)}):")
            for link in links[:3]:
                print(f"      {link}")

        print(f"\n   === 图片分析 ===")
        print(f"   总图片数: {analysis.get('totalImages', 0)}")
        print(f"   视频相关图片: {len(analysis.get('videoRelatedImages', []))}")
        for img in analysis.get("videoRelatedImages", [])[:3]:
            print(f"      {img}")

        print(f"\n   === video 元素: {analysis.get('videoElements', 0)} ===")

        print(f"\n   === 命名 script 标签 ===")
        for s in analysis.get("namedScripts", []):
            print(f"   #{s['id']} type={s['type']} len={s['contentLen']}")

        print(f"\n   === RENDER_DATA ===")
        if analysis.get("renderDataLen"):
            print(f"   长度: {analysis['renderDataLen']}")
            if analysis.get("renderDataTopKeys"):
                print(f"   顶层 keys: {analysis['renderDataTopKeys']}")
            if analysis.get("renderDataInteresting"):
                print(f"   有用的 keys:")
                for path, info in analysis["renderDataInteresting"].items():
                    print(f"      {path}: type={info['type']} isArray={info['isArray']} len={info.get('length')}")
                    if info.get("sample"):
                        print(f"         sample: {info['sample'][:200]}")
        else:
            print("   不存在")

        print(f"\n   === 全局变量 ===")
        for k, v in analysis.get("globalVars", {}).items():
            print(f"   {k}: {v}")

        print(f"\n   === iframe ===")
        for f in analysis.get("iframes", []):
            print(f"   #{f.get('id','')} src={f['src']}")
        if not analysis.get("iframes"):
            print("   无")

        # ─── 页面文本 ────────────────────────────────────
        print(f"\n6. 页面可见文本 (前2000字符):")
        body_text = analysis.get("bodyTextSample", "")
        # 压缩多余空行
        lines = [l.strip() for l in body_text.split("\n") if l.strip()]
        print("   " + "\n   ".join(lines[:60]))

        # ─── 尝试从拦截到的 API 响应中提取 video ids ─────
        video_ids = []
        for resp in api_responses:
            sample = resp.get("body_sample", "")
            # 尝试解析
            try:
                body = json.loads(sample) if len(sample) < 500 else None
            except Exception:
                body = None
            if body and body.get("data"):
                for item in body["data"]:
                    aid = item.get("aweme_info", {}).get("aweme_id", "")
                    if aid:
                        video_ids.append(aid)

        if video_ids:
            print(f"\n从拦截的 API 中提取到 {len(video_ids)} 个视频!")
            for vid in video_ids[:5]:
                print(f"   {vid}")

        # ─── 截图 ────────────────────────────────────────
        screenshot_path = os.path.join(SCRIPT_DIR, "dy_search_v4.png")
        await page.screenshot(path=screenshot_path, full_page=False)
        print(f"\n截图已保存: {screenshot_path}")

        print(f"\n{'='*60}")
        if video_ids:
            print(f"结论: 从拦截的 API 响应中找到 {len(video_ids)} 个视频")
        elif analysis.get("linkCategories", {}).get("video"):
            print(f"结论: DOM 中有 video 链接但未解析")
        else:
            print(f"结论: 搜索结果页面没有视频数据，可能需要等待更长时间或交互")
        print(f"{'='*60}")

        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
