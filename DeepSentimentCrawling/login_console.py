# -*- coding: utf-8 -*-
"""
LoginConsole — FastAPI 远程登录控制台

提供 Web 界面供运维人员远程扫码登录各平台，获取 cookie 后自动保存到 MongoDB。
通过 Server酱 告警中的链接访问。
"""

import asyncio
import base64
import os
import time
from typing import Optional

from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from loguru import logger
from playwright.async_api import async_playwright, Browser, BrowserContext

import sys
from pathlib import Path

_PROJECT_ROOT = str(Path(__file__).parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
from ms_config import settings

from DeepSentimentCrawling.cookie_manager import CookieManager

app = FastAPI(title="MindSpider Login Console", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^chrome-extension://.*$",
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# stealth 脚本路径
_STEALTH_JS = os.path.join(
    os.path.dirname(__file__), "MediaCrawler", "libs", "stealth.min.js"
)

# 全局状态
_cookie_manager: Optional[CookieManager] = None
_browser: Optional[Browser] = None
_active_sessions: dict[str, dict] = {}  # platform -> session info

# 平台登录配置
_PLATFORM_LOGIN = {
    "xhs": {
        "url": "https://www.xiaohongshu.com",
        "qr_selector": "xpath=//img[@class='qrcode-img']",
        "session_key": "web_session",
        "name": "小红书",
    },
    "dy": {
        "url": "https://www.douyin.com",
        "qr_selector": "xpath=//div[@id='animate_qrcode_container']//img",
        "session_key": "LOGIN_STATUS",
        "name": "抖音",
    },
    "bili": {
        "url": "https://passport.bilibili.com/login",
        "pre_click_selector": "//div[contains(@class,'tab--') and contains(text(),'扫码登录')]",
        "qr_selector": "//div[contains(@class,'qrcode-wrap')]//img | //canvas[contains(@class,'qrcode')]",
        "session_key": "SESSDATA",
        "name": "哔哩哔哩",
    },
    "wb": {
        "url": "https://passport.weibo.com/sso/signin?entry=miniblog&source=miniblog",
        "qr_selector": "xpath=//img[@class='w-full h-full']",
        "session_key": "SSOLoginState",
        "name": "微博",
    },
    "ks": {
        "url": "https://www.kuaishou.com?isHome=1",
        "login_click_selector": "xpath=//p[text()='登录']",
        "qr_selector": "//div[@class='qrcode-img']//img",
        "session_key": "passToken",
        "name": "快手",
    },
    "tieba": {
        "url": "https://www.baidu.com/",
        "qr_selector": "xpath=//img[@class='tang-pass-qrcode-img']",
        "session_key": "STOKEN",
        "name": "贴吧",
    },
    "zhihu": {
        "url": "https://www.zhihu.com/signin",
        "pre_click_selector": "//div[contains(text(),'扫码登录')]",
        "qr_selector": "//img[contains(@class,'Qrcode')]",
        "session_key": "z_c0",
        "name": "知乎",
    },
}


def _check_token(token: str):
    """校验访问令牌"""
    expected = settings.LOGIN_CONSOLE_TOKEN
    if not expected:
        return  # 未配置则不校验
    if token != expected:
        raise HTTPException(status_code=403, detail="Invalid token")


def init_cookie_manager(cm: CookieManager):
    """注入 CookieManager 实例"""
    global _cookie_manager
    _cookie_manager = cm


@app.get("/", response_class=HTMLResponse)
async def dashboard(token: str = Query("")):
    """仪表盘 — 显示所有平台 cookie 状态"""
    _check_token(token)

    if not _cookie_manager:
        return HTMLResponse("<h1>CookieManager 未初始化</h1>", status_code=500)

    statuses = _cookie_manager.get_all_status()

    rows = ""
    for s in statuses:
        status_class = {
            "active": "color: green;",
            "expired": "color: red;",
            "missing": "color: gray;",
        }.get(s["status"], "")

        saved_str = time.strftime("%Y-%m-%d %H:%M", time.localtime(s["saved_at"])) if s["saved_at"] else "-"
        platform_name = _PLATFORM_LOGIN.get(s["platform"], {}).get("name", s["platform"])
        login_link = f"/login/{s['platform']}?token={token}"

        rows += f"""
        <tr>
            <td>{platform_name} ({s['platform']})</td>
            <td style="{status_class} font-weight:bold;">{s['status']}</td>
            <td>{saved_str}</td>
            <td><a href="{login_link}">扫码登录</a></td>
        </tr>
        """

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>MindSpider 登录控制台</title>
        <style>
            body {{ font-family: -apple-system, sans-serif; max-width: 800px; margin: 40px auto; padding: 0 20px; }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
            th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #ddd; }}
            th {{ background: #f5f5f5; }}
            a {{ color: #1890ff; text-decoration: none; }}
            h1 {{ color: #333; }}
        </style>
    </head>
    <body>
        <h1>MindSpider 登录控制台</h1>
        <p>管理各平台 cookie 状态，点击"扫码登录"更新过期 cookie。</p>
        <table>
            <tr><th>平台</th><th>状态</th><th>保存时间</th><th>操作</th></tr>
            {rows}
        </table>
    </body>
    </html>
    """
    return HTMLResponse(html)


@app.get("/login/{platform}", response_class=HTMLResponse)
async def login_page(platform: str, token: str = Query("")):
    """登录页面 — 显示二维码"""
    _check_token(token)

    if platform not in _PLATFORM_LOGIN:
        raise HTTPException(status_code=404, detail=f"不支持的平台: {platform}")

    plat_conf = _PLATFORM_LOGIN[platform]
    platform_name = plat_conf["name"]

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>{platform_name} 扫码登录</title>
        <style>
            body {{ font-family: -apple-system, sans-serif; max-width: 600px; margin: 40px auto; padding: 0 20px; text-align: center; }}
            #qr-container {{ margin: 20px 0; min-height: 300px; display: flex; align-items: center; justify-content: center; }}
            #qr-container img {{ max-width: 300px; border: 1px solid #ddd; border-radius: 8px; }}
            #status {{ margin: 20px 0; padding: 15px; border-radius: 8px; }}
            .loading {{ color: #666; }}
            .success {{ background: #f6ffed; color: #52c41a; border: 1px solid #b7eb8f; }}
            .error {{ background: #fff2f0; color: #ff4d4f; border: 1px solid #ffa39e; }}
            button {{ padding: 10px 20px; border: none; border-radius: 4px; background: #1890ff; color: white; cursor: pointer; font-size: 16px; }}
            button:hover {{ background: #40a9ff; }}
            .tip {{ color: #999; font-size: 14px; margin-top: 10px; }}
        </style>
    </head>
    <body>
        <h1>{platform_name} 扫码登录</h1>

        <!-- 方式一：扫码登录 -->
        <h3>方式一：扫码登录</h3>
        <p>请用 {platform_name} APP 扫描下方二维码（长按图片可保存到相册）</p>
        <p class="tip" style="color: #e8a339; font-weight: bold;">扫码后请在手机上点击「确认登录」</p>

        <div id="qr-container">
            <p class="loading">正在获取二维码...</p>
        </div>
        <div id="status"></div>

        <button onclick="startLogin()">获取二维码</button>
        <button id="btn-confirm" onclick="confirmLogin()" style="display:none; background:#52c41a; margin-left:10px;">我已扫码并确认</button>
        <p class="tip">若扫码登录不成功（如抖音），请使用下方的 Cookie 粘贴方式</p>

        <!-- 方式二：Cookie 粘贴 -->
        <hr style="margin: 30px 0; border: none; border-top: 1px solid #ddd;">
        <h3>方式二：手动粘贴 Cookie</h3>
        <p style="text-align:left; font-size:14px; color:#666;">
            在电脑浏览器中登录 {platform_name}，按 F12 打开开发者工具 → Application → Cookies，<br>
            复制所有 cookie（格式：<code>name1=value1; name2=value2; ...</code>），粘贴到下方：
        </p>
        <textarea id="cookie-input" rows="4" style="width:100%; font-family:monospace; font-size:13px; padding:8px; border:1px solid #ddd; border-radius:4px;" placeholder="name1=value1; name2=value2; ..."></textarea>
        <br><br>
        <button onclick="saveCookie()" style="background:#fa8c16;">保存 Cookie</button>
        <div id="cookie-status" style="margin-top:10px;"></div>

        <script>
            const platform = "{platform}";
            const token = "{token}";
            let pollTimer = null;

            async function startLogin() {{
                document.getElementById("qr-container").innerHTML = '<p class="loading">正在启动浏览器...</p>';
                document.getElementById("status").innerHTML = '';
                document.getElementById("btn-confirm").style.display = 'none';

                try {{
                    const resp = await fetch(`/login/${{platform}}/qr?token=${{token}}`);
                    if (!resp.ok) {{
                        throw new Error(await resp.text());
                    }}
                    const data = await resp.json();

                    if (data.qr_base64) {{
                        document.getElementById("qr-container").innerHTML =
                            `<img src="data:image/png;base64,${{data.qr_base64}}" alt="QR Code">`;
                        document.getElementById("btn-confirm").style.display = 'inline-block';
                        startPolling();
                    }} else {{
                        document.getElementById("qr-container").innerHTML =
                            '<p class="error">无法获取二维码，请重试</p>';
                    }}
                }} catch (e) {{
                    document.getElementById("qr-container").innerHTML =
                        `<p class="error">错误: ${{e.message}}</p>`;
                }}
            }}

            function startPolling() {{
                if (pollTimer) clearInterval(pollTimer);
                document.getElementById("status").innerHTML = '<p class="loading">等待扫码...</p>';

                pollTimer = setInterval(async () => {{
                    try {{
                        const resp = await fetch(`/login/${{platform}}/poll?token=${{token}}`);
                        const data = await resp.json();

                        if (data.status === "success") {{
                            clearInterval(pollTimer);
                            document.getElementById("btn-confirm").style.display = 'none';
                            document.getElementById("status").innerHTML =
                                '<p class="success">✓ 登录成功！Cookie 已保存。</p>';
                        }} else if (data.status === "error") {{
                            clearInterval(pollTimer);
                            document.getElementById("status").innerHTML =
                                `<p class="error">登录失败: ${{data.message}}</p>`;
                        }}
                    }} catch (e) {{
                        // 网络错误，继续轮询
                    }}
                }}, 2000);
            }}

            async function confirmLogin() {{
                document.getElementById("status").innerHTML = '<p class="loading">正在检测登录状态...</p>';
                try {{
                    const resp = await fetch(`/login/${{platform}}/confirm?token=${{token}}`);
                    const data = await resp.json();
                    if (data.status === "success") {{
                        if (pollTimer) clearInterval(pollTimer);
                        document.getElementById("btn-confirm").style.display = 'none';
                        document.getElementById("status").innerHTML =
                            '<p class="success">✓ 登录成功！Cookie 已保存。</p>';
                    }} else {{
                        document.getElementById("status").innerHTML =
                            `<p class="error">${{data.message || '未检测到登录，请确认手机上已点击确认'}}</p>`;
                    }}
                }} catch (e) {{
                    document.getElementById("status").innerHTML =
                        `<p class="error">检测失败: ${{e.message}}</p>`;
                }}
            }}

            async function saveCookie() {{
                const cookieStr = document.getElementById("cookie-input").value.trim();
                if (!cookieStr) {{
                    document.getElementById("cookie-status").innerHTML =
                        '<p class="error">请先粘贴 Cookie 字符串</p>';
                    return;
                }}
                document.getElementById("cookie-status").innerHTML = '<p class="loading">正在保存...</p>';
                try {{
                    const resp = await fetch(`/login/${{platform}}/paste?token=${{token}}`, {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify({{cookie_str: cookieStr}})
                    }});
                    const data = await resp.json();
                    if (data.status === "success") {{
                        if (pollTimer) clearInterval(pollTimer);
                        document.getElementById("cookie-status").innerHTML =
                            `<p class="success">✓ 已保存 ${{data.count}} 个 Cookie！</p>`;
                    }} else {{
                        document.getElementById("cookie-status").innerHTML =
                            `<p class="error">${{data.message}}</p>`;
                    }}
                }} catch (e) {{
                    document.getElementById("cookie-status").innerHTML =
                        `<p class="error">保存失败: ${{e.message}}</p>`;
                }}
            }}

            // 页面加载时自动获取二维码
            startLogin();
        </script>
    </body>
    </html>
    """
    return HTMLResponse(html)


@app.get("/login/{platform}/qr")
async def get_qr(platform: str, token: str = Query("")):
    """启动浏览器、导航到登录页、截取二维码图片"""
    _check_token(token)

    if platform not in _PLATFORM_LOGIN:
        raise HTTPException(status_code=404, detail=f"不支持的平台: {platform}")

    plat_conf = _PLATFORM_LOGIN[platform]

    try:
        global _browser

        pw = await async_playwright().start()
        if _browser is None or not _browser.is_connected():
            _browser = await pw.chromium.launch(headless=True)

        context = await _browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
        )

        # 加载 stealth 脚本，降低自动化检测概率
        if os.path.exists(_STEALTH_JS):
            await context.add_init_script(path=_STEALTH_JS)

        page = await context.new_page()

        await page.goto(plat_conf["url"], wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)

        # 抖音特殊处理：先弹出登录对话框
        if platform == "dy":
            try:
                dialog = await page.wait_for_selector(
                    "xpath=//div[@id='login-panel-new']", timeout=10000
                )
            except Exception:
                # 对话框没有自动弹出，手动点击登录按钮
                try:
                    login_btn = page.locator("xpath=//p[text() = '登录']")
                    await login_btn.click()
                    await page.wait_for_timeout(2000)
                except Exception:
                    pass

            # 尝试关闭可能出现的验证弹窗（多种选择器覆盖不同样式）
            for _attempt in range(3):
                dismissed = False
                for captcha_sel in [
                    "xpath=//div[contains(@id,'captcha')]//div[contains(@class,'close') or contains(@class,'Close')]",
                    "xpath=//div[contains(@class,'captcha')]//div[contains(@class,'close') or contains(@class,'Close')]",
                    "xpath=//div[contains(@class,'verify')]//div[contains(@class,'close') or contains(@class,'Close')]",
                    "xpath=//div[contains(@id,'verify')]//span[contains(@class,'close')]",
                    "xpath=//*[contains(@class,'captcha_close') or contains(@class,'sc-jTzLTM')]",
                    "xpath=//div[contains(@class,'modal')]//span[contains(@class,'close') or contains(@class,'Close')]",
                ]:
                    try:
                        el = await page.wait_for_selector(captcha_sel, timeout=1500)
                        if el:
                            await el.click()
                            await page.wait_for_timeout(1000)
                            dismissed = True
                            logger.info(f"[LoginConsole] dy 关闭验证弹窗成功: {captcha_sel}")
                            break
                    except Exception:
                        continue
                if not dismissed:
                    break

        # 快手特殊处理：首页点击"登录"按钮弹出登录框
        elif platform == "ks":
            try:
                login_btn = page.locator("xpath=//p[text()='登录']")
                await login_btn.click()
                await page.wait_for_timeout(2000)
                logger.info("[LoginConsole] ks 已点击登录按钮")
            except Exception as e:
                logger.warning(f"[LoginConsole] ks 点击登录按钮失败: {e}")

        # 贴吧特殊处理：先访问 baidu.com，再跳转到贴吧（避免直接访问触发滑块验证）
        elif platform == "tieba":
            # 从百度首页点击"贴吧"链接跳转
            tieba_clicked = False
            tieba_selectors = [
                'a[href="http://tieba.baidu.com/"]',
                'a[href="https://tieba.baidu.com/"]',
                'a.mnav:has-text("贴吧")',
                'text=贴吧',
            ]
            for sel in tieba_selectors:
                try:
                    link = page.locator(sel).first
                    if await link.count() > 0:
                        # 检查是否在新标签打开
                        target = await link.get_attribute("target")
                        if target == "_blank":
                            async with page.context.expect_page() as new_page_info:
                                await link.click()
                            new_page = await new_page_info.value
                            await new_page.wait_for_load_state("domcontentloaded")
                            await page.close()
                            page = new_page
                        else:
                            await link.click()
                            await page.wait_for_load_state("domcontentloaded")
                        tieba_clicked = True
                        logger.info(f"[LoginConsole] tieba 通过百度首页跳转成功 (selector: {sel})")
                        break
                except Exception:
                    continue

            if not tieba_clicked:
                logger.warning("[LoginConsole] tieba 无法从百度首页跳转，直接访问 tieba.baidu.com")
                await page.goto("https://tieba.baidu.com", wait_until="domcontentloaded", timeout=30000)

            await page.wait_for_timeout(3000)

            # 注入反检测脚本（贴吧额外需要）
            try:
                await page.evaluate("""() => {
                    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                    window.navigator.chrome = { runtime: {} };
                }""")
            except Exception:
                pass

            # 尝试点击登录按钮
            try:
                login_btn = page.locator("xpath=//li[@class='u_login']")
                if await login_btn.count() > 0:
                    await login_btn.click()
                    await page.wait_for_timeout(2000)
                    logger.info("[LoginConsole] tieba 已点击登录按钮")
            except Exception as e:
                logger.warning(f"[LoginConsole] tieba 点击登录按钮失败: {e}")

        # 微博特殊处理：SSO 页面直接显示二维码，无需额外点击
        elif platform == "wb":
            # passport.weibo.com 的 SSO 页面直接展示二维码
            logger.info("[LoginConsole] wb SSO 页面已加载，等待二维码显示")

        # 部分平台需要先点击"扫码登录"切换到二维码模式
        pre_click = plat_conf.get("pre_click_selector")
        if pre_click:
            try:
                btn = await page.wait_for_selector(pre_click, timeout=5000)
                if btn:
                    await btn.click()
                    await page.wait_for_timeout(2000)
            except Exception:
                pass

        # 尝试获取二维码图片
        qr_base64 = None
        try:
            qr_el = await page.wait_for_selector(plat_conf["qr_selector"], timeout=15000)
            if qr_el:
                screenshot = await qr_el.screenshot()
                qr_base64 = base64.b64encode(screenshot).decode()
        except Exception as e:
            logger.warning(f"[LoginConsole] 无法通过选择器获取二维码: {e}")
            # fallback: 截取整个页面
            screenshot = await page.screenshot()
            qr_base64 = base64.b64encode(screenshot).decode()

        # 保存会话信息
        _active_sessions[platform] = {
            "context": context,
            "page": page,
            "started_at": time.time(),
            "session_key": plat_conf["session_key"],
        }

        return JSONResponse({"qr_base64": qr_base64})

    except Exception as e:
        logger.error(f"[LoginConsole] 获取 {platform} 二维码失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/login/{platform}/confirm")
async def confirm_login(platform: str, token: str = Query("")):
    """用户手动确认已扫码，系统导航到平台首页检查登录状态"""
    _check_token(token)

    session = _active_sessions.get(platform)
    if not session:
        return JSONResponse({"status": "error", "message": "无活跃登录会话"})

    context: BrowserContext = session["context"]
    page = session.get("page")

    plat_conf = _PLATFORM_LOGIN.get(platform, {})
    # 导航到平台首页，触发服务器写入登录 cookie
    check_url = {
        "dy": "https://www.douyin.com",
        "xhs": "https://www.xiaohongshu.com",
        "ks": "https://www.kuaishou.com",
        "bili": "https://www.bilibili.com",
        "wb": "https://m.weibo.cn",
        "tieba": "https://tieba.baidu.com",
        "zhihu": "https://www.zhihu.com",
    }.get(platform, plat_conf.get("url", ""))

    try:
        if page:
            logger.info(f"[LoginConsole] {platform} 用户确认已扫码，导航到 {check_url}")
            await page.goto(check_url, wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(5000)

        # 获取 cookies
        cookies = await context.cookies()
        cookie_dict = {c["name"]: c["value"] for c in cookies}
        logger.info(f"[LoginConsole] {platform} 确认后 cookie 数量: {len(cookie_dict)}，名称: {sorted(cookie_dict.keys())}")

        # 检查登录状态
        session_key = session["session_key"]
        logged_in = False

        if session_key in cookie_dict:
            logged_in = True
        # 抖音：也检查 LOGIN_STATUS=1 和 localStorage
        if not logged_in and platform == "dy":
            if cookie_dict.get("LOGIN_STATUS") == "1":
                logged_in = True
            else:
                for p in context.pages:
                    try:
                        ls = await p.evaluate("() => window.localStorage")
                        if ls.get("HasUserLogin", "") == "1":
                            logged_in = True
                            break
                    except Exception:
                        continue
        # 贴吧：STOKEN / PTOKEN / BDUSS
        if not logged_in and platform == "tieba":
            if cookie_dict.get("PTOKEN") or cookie_dict.get("BDUSS"):
                logged_in = True

        if logged_in:
            if _cookie_manager:
                _cookie_manager.save_cookies(platform, cookie_dict)

            if page:
                await page.close()
            await context.close()
            del _active_sessions[platform]

            logger.info(f"[LoginConsole] {platform} 确认登录成功，已保存 {len(cookie_dict)} 个 cookie")
            return JSONResponse({"status": "success"})
        else:
            return JSONResponse({"status": "error", "message": f"未检测到登录 cookie ({session_key})，请确认手机上已完成登录"})

    except Exception as e:
        logger.error(f"[LoginConsole] {platform} 确认检测异常: {e}")
        return JSONResponse({"status": "error", "message": str(e)})


@app.post("/login/{platform}/paste")
async def paste_cookies(platform: str, request: Request, token: str = Query("")):
    """用户手动粘贴 cookie 字符串保存"""
    _check_token(token)

    if platform not in _PLATFORM_LOGIN:
        raise HTTPException(status_code=404, detail=f"不支持的平台: {platform}")

    try:
        body = await request.json()
        cookie_str = body.get("cookie_str", "").strip()
        if not cookie_str:
            return JSONResponse({"status": "error", "message": "Cookie 字符串为空"})

        # 解析 "name1=value1; name2=value2; ..." 格式
        cookie_dict = {}
        for item in cookie_str.split(";"):
            item = item.strip()
            if "=" in item:
                key, value = item.split("=", 1)
                cookie_dict[key.strip()] = value.strip()

        if not cookie_dict:
            return JSONResponse({"status": "error", "message": "未能解析出任何 Cookie"})

        # 检查关键 session cookie 是否存在
        session_key = _PLATFORM_LOGIN[platform]["session_key"]
        if session_key not in cookie_dict:
            logger.warning(f"[LoginConsole] {platform} 粘贴的 Cookie 中缺少 {session_key}，仍然保存")

        # 保存
        if _cookie_manager:
            _cookie_manager.save_cookies(platform, cookie_dict)

        # 清理可能存在的活跃会话
        if platform in _active_sessions:
            session = _active_sessions.pop(platform)
            try:
                page = session.get("page")
                if page:
                    await page.close()
                ctx = session.get("context")
                if ctx:
                    await ctx.close()
            except Exception:
                pass

        logger.info(f"[LoginConsole] {platform} 手动粘贴 Cookie 成功，共 {len(cookie_dict)} 个")
        return JSONResponse({"status": "success", "count": len(cookie_dict)})

    except Exception as e:
        logger.error(f"[LoginConsole] {platform} Cookie 粘贴异常: {e}")
        return JSONResponse({"status": "error", "message": str(e)})


@app.get("/login/{platform}/poll")
async def poll_login(platform: str, token: str = Query("")):
    """轮询登录状态"""
    _check_token(token)

    session = _active_sessions.get(platform)
    if not session:
        return JSONResponse({"status": "error", "message": "无活跃登录会话"})

    context: BrowserContext = session["context"]
    session_key = session["session_key"]

    try:
        cookies = await context.cookies()
        cookie_dict = {c["name"]: c["value"] for c in cookies}

        logged_in = False

        # 抖音特殊处理：页面留在登录页不动，检查 localStorage + cookies
        # 注意：不能导航离开登录页，否则 QR 会话会断开
        if platform == "dy":
            for p in context.pages:
                try:
                    local_storage = await p.evaluate("() => window.localStorage")
                    if local_storage.get("HasUserLogin", "") == "1":
                        logger.info(f"[LoginConsole] {platform} localStorage HasUserLogin=1")
                        logged_in = True
                        break
                except Exception:
                    continue
            # 也检查 LOGIN_STATUS cookie 值是否为 "1"
            if not logged_in and cookie_dict.get("LOGIN_STATUS") == "1":
                logged_in = True
                logger.info(f"[LoginConsole] {platform} cookie LOGIN_STATUS=1")

        # 通用检查：session cookie 是否出现
        if not logged_in and session_key in cookie_dict:
            logged_in = True
            logger.info(f"[LoginConsole] {platform} 检测到 session cookie: {session_key}")

        # 贴吧额外检查：STOKEN 或 PTOKEN 任一出现即视为登录成功
        if not logged_in and platform == "tieba":
            if cookie_dict.get("PTOKEN") or cookie_dict.get("BDUSS"):
                logged_in = True
                logger.info(f"[LoginConsole] {platform} 检测到备选 cookie (PTOKEN/BDUSS)")

        if logged_in:
            # 抖音登录成功后导航到首页，确保所有 cookie 同步
            if platform == "dy":
                page = session.get("page")
                if page:
                    try:
                        await page.goto("https://www.douyin.com", wait_until="domcontentloaded", timeout=15000)
                        await page.wait_for_timeout(3000)
                        # 重新获取 cookies
                        cookies = await context.cookies()
                        cookie_dict = {c["name"]: c["value"] for c in cookies}
                    except Exception as e:
                        logger.warning(f"[LoginConsole] {platform} 导航到首页获取cookie失败: {e}")

            # 保存 cookie
            if _cookie_manager:
                _cookie_manager.save_cookies(platform, cookie_dict)

            # 清理会话
            page = session.get("page")
            if page:
                await page.close()
            await context.close()
            del _active_sessions[platform]

            logger.info(f"[LoginConsole] {platform} 登录成功，已保存 {len(cookie_dict)} 个 cookie")
            return JSONResponse({"status": "success"})

        # 调试日志
        elapsed = int(time.time() - session["started_at"])
        cookie_names = sorted(cookie_dict.keys())
        logger.debug(f"[LoginConsole] {platform} 轮询中 ({elapsed}s)，{len(cookie_dict)} cookie: {cookie_names}")

        # 检查超时（5 分钟）
        if elapsed > 300:
            page = session.get("page")
            if page:
                await page.close()
            await context.close()
            del _active_sessions[platform]
            return JSONResponse({"status": "error", "message": "登录超时"})

        return JSONResponse({"status": "waiting"})

    except Exception as e:
        logger.error(f"[LoginConsole] {platform} 轮询异常: {e}")
        return JSONResponse({"status": "error", "message": str(e)})


async def cleanup():
    """清理所有活跃会话和浏览器"""
    global _browser
    for platform, session in list(_active_sessions.items()):
        try:
            page = session.get("page")
            if page:
                await page.close()
            ctx = session.get("context")
            if ctx:
                await ctx.close()
        except Exception:
            pass
    _active_sessions.clear()

    if _browser and _browser.is_connected():
        await _browser.close()
        _browser = None
