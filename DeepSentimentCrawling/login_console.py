# -*- coding: utf-8 -*-
"""
LoginConsole — FastAPI 远程登录控制台

提供 Web 界面供运维人员远程扫码登录各平台，获取 cookie 后自动保存到 MongoDB。
通过 Server酱 告警中的链接访问。
"""

import asyncio
import base64
import time
from typing import Optional

from fastapi import FastAPI, Query, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from loguru import logger
from playwright.async_api import async_playwright, Browser, BrowserContext

import sys
from pathlib import Path

_PROJECT_ROOT = str(Path(__file__).parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
from config import settings

from DeepSentimentCrawling.cookie_manager import CookieManager

app = FastAPI(title="MindSpider Login Console", docs_url=None, redoc_url=None)

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
        "url": "https://www.bilibili.com",
        "qr_selector": "//div[@class='login-scan-box']//img",
        "session_key": "SESSDATA",
        "name": "哔哩哔哩",
    },
    "wb": {
        "url": "https://weibo.com",
        "qr_selector": "xpath=//img[@class='w-full h-full']",
        "session_key": "SSOLoginState",
        "name": "微博",
    },
    "ks": {
        "url": "https://www.kuaishou.com",
        "qr_selector": "//div[@class='qrcode-img']//img",
        "session_key": "passToken",
        "name": "快手",
    },
    "tieba": {
        "url": "https://tieba.baidu.com",
        "qr_selector": "//img[@id='QrcodeImg']",
        "session_key": "BDUSS",
        "name": "贴吧",
    },
    "zhihu": {
        "url": "https://www.zhihu.com/signin",
        "qr_selector": "//img[@class='Login-qrcode']",
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
        <p>请用 {platform_name} APP 扫描下方二维码（长按图片可保存到相册）</p>

        <div id="qr-container">
            <p class="loading">正在获取二维码...</p>
        </div>
        <div id="status"></div>

        <button onclick="startLogin()">获取二维码</button>
        <p class="tip">二维码获取后，页面将自动检测登录状态</p>

        <script>
            const platform = "{platform}";
            const token = "{token}";
            let pollTimer = null;

            async function startLogin() {{
                document.getElementById("qr-container").innerHTML = '<p class="loading">正在启动浏览器...</p>';
                document.getElementById("status").innerHTML = '';

                try {{
                    const resp = await fetch(`/login/${{platform}}/qr?token=${{token}}`);
                    if (!resp.ok) {{
                        throw new Error(await resp.text());
                    }}
                    const data = await resp.json();

                    if (data.qr_base64) {{
                        document.getElementById("qr-container").innerHTML =
                            `<img src="data:image/png;base64,${{data.qr_base64}}" alt="QR Code">`;
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
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        page = await context.new_page()

        await page.goto(plat_conf["url"], wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)

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

        # 检查 session cookie 是否出现
        if session_key in cookie_dict:
            # 登录成功，保存 cookie
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

        # 检查超时（5 分钟）
        if time.time() - session["started_at"] > 300:
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
