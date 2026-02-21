# -*- coding: utf-8 -*-
"""
告警服务 — 通过 Server酱3 推送告警消息

支持 cookie 过期告警和熔断器告警，带平台级速率限制（每平台 5 分钟最多 1 条）。
"""

import time
from typing import Optional

from loguru import logger

import sys
from pathlib import Path

_PROJECT_ROOT = str(Path(__file__).parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
from ms_config import settings

# 每平台最近一次告警时间戳
_last_alert_ts: dict[str, float] = {}
_RATE_LIMIT_SEC = 300  # 5 minutes


def _should_rate_limit(platform: str) -> bool:
    """检查是否应跳过告警（速率限制）"""
    now = time.time()
    last = _last_alert_ts.get(platform, 0)
    if now - last < _RATE_LIMIT_SEC:
        return True
    _last_alert_ts[platform] = now
    return False


def send_alert(title: str, content: str, platform: Optional[str] = None) -> bool:
    """
    发送 Server酱3 告警

    Args:
        title: 告警标题
        content: 告警正文（支持 Markdown）
        platform: 可选平台名，用于速率限制

    Returns:
        是否发送成功
    """
    key = settings.SERVERCHAN_KEY
    if not key:
        logger.warning("[Alert] SERVERCHAN_KEY 未配置，跳过告警推送")
        return False

    if platform and _should_rate_limit(platform):
        logger.debug(f"[Alert] 平台 {platform} 告警速率限制中，跳过")
        return False

    try:
        from serverchan_sdk import sc_send
        resp = sc_send(key, title, content, {"tags": "MindSpider|深层采集"})
        if resp.get("code") == 0:
            logger.info(f"[Alert] 告警发送成功: {title}")
            return True
        else:
            logger.warning(f"[Alert] 告警发送失败: {resp}")
            return False
    except ImportError:
        logger.error("[Alert] serverchan-sdk 未安装，请运行: pip install serverchan-sdk")
        return False
    except Exception as e:
        logger.error(f"[Alert] 告警发送异常: {e}")
        return False


def alert_cookie_expired(platform: str) -> bool:
    """Cookie 过期告警"""
    port = settings.LOGIN_CONSOLE_PORT
    token = settings.LOGIN_CONSOLE_TOKEN
    login_url = f"http://YOUR_SERVER:{port}/login/{platform}?token={token}"

    title = f"MindSpider {platform} Cookie过期"
    content = (
        f"## 平台 `{platform}` Cookie 已过期\n\n"
        f"深层采集服务已暂停该平台的爬取任务。\n\n"
        f"请点击下方链接扫码重新登录：\n\n"
        f"[登录控制台]({login_url})\n\n"
        f"---\n"
        f"*MindSpider Deep Crawl Service*"
    )
    return send_alert(title, content, platform=platform)


def alert_circuit_open(platform: str, reason: str) -> bool:
    """熔断器打开告警"""
    title = f"MindSpider {platform} 熔断"
    content = (
        f"## 平台 `{platform}` 熔断器已触发\n\n"
        f"**原因:** {reason}\n\n"
        f"熔断器将在 30 分钟后自动重置。"
        f"如需手动排查，请检查平台状态。\n\n"
        f"---\n"
        f"*MindSpider Deep Crawl Service*"
    )
    return send_alert(title, content, platform=platform)
