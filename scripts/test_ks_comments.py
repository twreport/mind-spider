# -*- coding: utf-8 -*-
"""快手评论 DOM 提取诊断脚本

GraphQL commentListQuery 已废弃（所有方式均返回 commentCount=None）。
评论通过 SSR 渲染在页面 DOM 中。本脚本测试从 DOM 提取评论的方案。

用法:
  cd /deploy/parallel-universe/mind-spider
  uv run python scripts/test_ks_comments.py
"""

import asyncio
import json
import os
import sys

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
    client = MongoClient(MONGO_URI)
    db = client[MONGO_DB]
    doc = db.platform_cookies.find_one({"platform": "ks", "status": "active"})
    client.close()
    if not doc:
        print("MongoDB 中没有找到 ks 的 active cookie")
        sys.exit(1)
    cookies = doc["cookies"]
    print(f"cookie: {len(cookies)} fields")
    return cookies


def get_video_ids_from_mysql(limit=3):
    conn = pymysql.connect(
        host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER,
        password=MYSQL_PASS, database=MYSQL_DB, charset="utf8mb4",
    )
    cursor = conn.cursor()
    cursor.execute(
        "SELECT video_id, title, liked_count FROM kuaishou_video "
        "ORDER BY add_ts DESC LIMIT %s", (limit,)
    )
    rows = cursor.fetchall()
    conn.close()
    if not rows:
        print("MySQL 中没有找到快手视频")
        sys.exit(1)
    for vid, title, likes in rows:
        print(f"  {vid}  likes={likes}  {title[:50]}")
    return [r[0] for r in rows]


# ─── DOM 评论提取 JS ───────────────────────────────────
# 这段 JS 在浏览器中执行，探测评论区的 DOM 结构并提取评论
EXTRACT_COMMENTS_JS = """
() => {
    const results = { comments: [], debug: {} };

    // ─── 策略1: 查找包含评论内容的容器 ───
    // 快手视频页评论区的常见 class 名
    const selectors = [
        // 评论列表容器
        '[class*="comment-list"]',
        '[class*="commentList"]',
        '[class*="CommentList"]',
        '[class*="comment-item"]',
        '[class*="commentItem"]',
        '[class*="CommentItem"]',
        // 评论内容
        '[class*="comment-content"]',
        '[class*="commentContent"]',
        // 通用
        '[data-testid*="comment"]',
    ];

    results.debug.selectorCounts = {};
    for (const sel of selectors) {
        const els = document.querySelectorAll(sel);
        if (els.length > 0) {
            results.debug.selectorCounts[sel] = els.length;
        }
    }

    // ─── 策略2: 遍历所有 class 含 comment 的元素，找结构 ───
    const allCommentEls = document.querySelectorAll('[class*="comment"]');
    results.debug.totalCommentElements = allCommentEls.length;

    // 收集所有 class 名（去重）
    const classNames = new Set();
    allCommentEls.forEach(el => {
        el.classList.forEach(cls => {
            if (cls.toLowerCase().includes('comment')) {
                classNames.add(cls);
            }
        });
    });
    results.debug.commentClassNames = Array.from(classNames).sort();

    // ─── 策略3: 找评论项的重复结构 ───
    // 找 class 列表中出现次数 >= 2 的（说明是重复的评论项）
    const classCounts = {};
    allCommentEls.forEach(el => {
        const key = el.className;
        classCounts[key] = (classCounts[key] || 0) + 1;
    });

    // 找出重复出现的 class 组合（可能是评论项容器）
    const repeatedClasses = Object.entries(classCounts)
        .filter(([k, v]) => v >= 2)
        .sort((a, b) => b[1] - a[1])
        .slice(0, 10);
    results.debug.repeatedCommentClasses = repeatedClasses.map(([cls, count]) => ({
        className: cls.slice(0, 100),
        count: count,
    }));

    // ─── 策略4: 找最可能的评论项容器，提取内容 ───
    for (const [cls, count] of repeatedClasses) {
        if (count < 2) continue;
        const items = document.querySelectorAll('.' + cls.split(' ').join('.'));
        if (items.length < 2) continue;

        // 检查是否包含文本内容（评论通常有文字）
        const sample = items[0];
        const text = sample.textContent?.trim();
        if (!text || text.length < 5) continue;

        // 找到了可能的评论项容器
        results.debug.selectedContainer = {
            className: cls.slice(0, 100),
            count: items.length,
            sampleHTML: sample.innerHTML.slice(0, 500),
            sampleText: text.slice(0, 200),
        };

        // 提取每个评论项的内容
        items.forEach((item, idx) => {
            if (idx >= 30) return; // 最多30条

            // 尝试提取结构化数据
            const comment = {
                index: idx,
                fullText: item.textContent?.trim().slice(0, 300) || '',
                innerHTML: item.innerHTML.slice(0, 800),
                childCount: item.children.length,
            };

            // 尝试找作者名（通常是第一个链接或特定 class）
            const authorEl = item.querySelector('[class*="name"], [class*="author"], [class*="nick"], a[href*="profile"]');
            if (authorEl) {
                comment.author = authorEl.textContent?.trim();
                comment.authorHref = authorEl.getAttribute('href') || '';
            }

            // 尝试找评论内容
            const contentEl = item.querySelector('[class*="content"], [class*="text"]');
            if (contentEl) {
                comment.content = contentEl.textContent?.trim();
            }

            // 尝试找时间
            const timeEl = item.querySelector('[class*="time"], [class*="date"], [class*="ago"]');
            if (timeEl) {
                comment.time = timeEl.textContent?.trim();
            }

            // 尝试找点赞数
            const likeEl = item.querySelector('[class*="like"], [class*="count"]');
            if (likeEl) {
                comment.likes = likeEl.textContent?.trim();
            }

            results.comments.push(comment);
        });

        break; // 找到第一个合适的容器就停止
    }

    // ─── 策略5: 如果上面没找到，直接 dump 页面 body 的结构 ───
    if (results.comments.length === 0) {
        // 找所有 div/section 层级，看看有没有评论相关的
        const body = document.body;
        const sections = body.querySelectorAll('div, section');
        const commentSections = [];
        sections.forEach(s => {
            const text = s.textContent?.trim() || '';
            const cls = s.className || '';
            // 找包含时间格式（X小时前、X分钟前）的 section
            if (/\\d+[小时分钟天]前/.test(text) && text.length < 500 && text.length > 10) {
                commentSections.push({
                    tag: s.tagName,
                    className: cls.slice(0, 80),
                    text: text.slice(0, 200),
                    childCount: s.children.length,
                });
            }
        });
        results.debug.timeBasedSections = commentSections.slice(0, 20);
    }

    return results;
}
"""


async def test_dom_extraction(video_id, cookie_dict):
    """从视频页 DOM 提取评论"""
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

        print(f"\n1. 导航到: {video_url}")
        await page.goto(video_url, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(5)
        print(f"   URL: {page.url}")
        print(f"   Title: {await page.title()}")

        # 滚动到评论区
        print("\n2. 滚动页面...")
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 3)")
        await asyncio.sleep(2)
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
        await asyncio.sleep(2)

        # 提取评论
        print("\n3. 提取 DOM 评论结构...")
        result = await page.evaluate(EXTRACT_COMMENTS_JS)

        debug = result.get("debug", {})
        comments = result.get("comments", [])

        print(f"\n─── 调试信息 ───")
        print(f"  总 comment 相关元素: {debug.get('totalCommentElements', 0)}")

        if debug.get("selectorCounts"):
            print(f"  选择器匹配:")
            for sel, cnt in debug["selectorCounts"].items():
                print(f"    {sel}: {cnt}")

        if debug.get("commentClassNames"):
            print(f"  comment 相关 class 名:")
            for cls in debug["commentClassNames"][:15]:
                print(f"    {cls}")

        if debug.get("repeatedCommentClasses"):
            print(f"  重复出现的 class 组合 (可能是评论项):")
            for item in debug["repeatedCommentClasses"]:
                print(f"    [{item['count']}x] {item['className'][:80]}")

        if debug.get("selectedContainer"):
            sc = debug["selectedContainer"]
            print(f"\n─── 选中的评论容器 ───")
            print(f"  class: {sc['className'][:80]}")
            print(f"  数量: {sc['count']}")
            print(f"  样本文本: {sc['sampleText'][:150]}")
            print(f"  样本HTML: {sc['sampleHTML'][:300]}")

        if debug.get("timeBasedSections"):
            print(f"\n─── 基于时间格式找到的区块 ───")
            for s in debug["timeBasedSections"][:10]:
                print(f"  [{s['tag']}.{s['className'][:40]}] children={s['childCount']}")
                print(f"    {s['text'][:120]}")

        print(f"\n─── 提取到的评论 ({len(comments)} 条) ───")
        for c in comments[:10]:
            author = c.get("author", "?")
            content = c.get("content", c.get("fullText", "")[:60])
            time_str = c.get("time", "")
            likes = c.get("likes", "")
            print(f"  [{c['index']}] {author} ({time_str}) likes={likes}")
            print(f"       {content[:80]}")
            if c.get("authorHref"):
                print(f"       href={c['authorHref'][:60]}")

        if len(comments) > 10:
            print(f"  ... 还有 {len(comments) - 10} 条")

        # ─── 额外: dump 第一个评论项的完整 innerHTML ───
        if comments:
            print(f"\n─── 第一个评论项的完整 innerHTML ───")
            print(comments[0].get("innerHTML", "")[:1000])

        # ─── 额外: 尝试从 SSR 数据中提取 ───
        print(f"\n─── 检查 SSR __NEXT_DATA__ / window.__data ───")
        ssr_data = await page.evaluate("""
            () => {
                // Next.js SSR
                const nextData = document.getElementById('__NEXT_DATA__');
                if (nextData) {
                    try {
                        const d = JSON.parse(nextData.textContent);
                        // 查找 comment 相关的 key
                        const find = (obj, path = '') => {
                            if (!obj || typeof obj !== 'object') return [];
                            let results = [];
                            for (const [k, v] of Object.entries(obj)) {
                                const p = path + '.' + k;
                                if (k.toLowerCase().includes('comment')) {
                                    results.push({ path: p, type: typeof v, isArray: Array.isArray(v), length: Array.isArray(v) ? v.length : null });
                                }
                                if (typeof v === 'object' && v !== null && path.split('.').length < 6) {
                                    results = results.concat(find(v, p));
                                }
                            }
                            return results;
                        };
                        return { found: true, commentPaths: find(d).slice(0, 20) };
                    } catch(e) {
                        return { found: true, parseError: e.message };
                    }
                }

                // 检查其他全局变量
                const globals = {};
                for (const key of ['__data', '__INITIAL_STATE__', '__PRELOADED_STATE__', '__APP_DATA__']) {
                    if (window[key]) {
                        globals[key] = typeof window[key];
                    }
                }

                // 检查 Apollo cache (GraphQL)
                if (window.__APOLLO_STATE__) {
                    const keys = Object.keys(window.__APOLLO_STATE__);
                    const commentKeys = keys.filter(k => k.toLowerCase().includes('comment'));
                    globals['__APOLLO_STATE__'] = { totalKeys: keys.length, commentKeys: commentKeys.slice(0, 10) };
                }

                return { found: false, globals };
            }
        """)
        print(f"  SSR 数据: {json.dumps(ssr_data, ensure_ascii=False, indent=2)}")

        await browser.close()
        return comments


async def main():
    print("=" * 60)
    print("快手评论 DOM 提取诊断")
    print("=" * 60)

    cookie_dict = get_cookie_from_mongo()
    video_ids = get_video_ids_from_mysql(limit=3)

    # 只测第一个视频
    video_id = video_ids[0]

    print(f"\n测试视频: {video_id}")
    comments = await test_dom_extraction(video_id, cookie_dict)

    print(f"\n{'='*60}")
    print(f"结论: 提取到 {len(comments)} 条评论")
    if comments:
        print("DOM 提取方案可行!")
    else:
        print("DOM 提取也失败，需要进一步排查")
    print(f"{'='*60}")


if __name__ == "__main__":
    asyncio.run(main())
