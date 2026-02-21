# -*- coding: utf-8 -*-
"""
Cookie 管理器 — MongoDB 持久化 + 健康检查 + Playwright 注入

管理 7 个平台的 cookie 生命周期：保存 → 加载 → 检测过期 → 告警 → 重新登录。
"""

import time
from typing import Optional

import httpx
from loguru import logger
from playwright.async_api import BrowserContext

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import settings

from BroadTopicExtraction.pipeline.mongo_writer import MongoWriter
from DeepSentimentCrawling.alert import alert_cookie_expired

COLLECTION = "platform_cookies"

# 各平台健康检查端点
_HEALTH_CHECK_URLS: dict[str, str] = {
    "xhs": "https://edith.xiaohongshu.com/api/sns/web/v1/user/selfinfo",
    "dy": "https://www.douyin.com/aweme/v1/web/user/profile/other/",
    "bili": "https://api.bilibili.com/x/web-interface/nav",
    "wb": "https://weibo.com/ajax/side/cards",
    "ks": "https://www.kuaishou.com/graphql",
    "tieba": "https://tieba.baidu.com/f/user/json_userinfo",
    "zhihu": "https://www.zhihu.com/api/v4/me",
}

# 各平台的 session cookie 关键字段（用于判断登录态）
_SESSION_COOKIE_KEYS: dict[str, str] = {
    "xhs": "web_session",
    "dy": "LOGIN_STATUS",
    "bili": "SESSDATA",
    "wb": "SSOLoginState",
    "ks": "passToken",
    "tieba": "BDUSS",
    "zhihu": "z_c0",
}


class CookieManager:
    """Cookie 持久化管理器"""

    def __init__(self, mongo_writer: Optional[MongoWriter] = None):
        self.mongo = mongo_writer or MongoWriter(
            db_name=settings.MONGO_SIGNAL_DB_NAME
        )

    def _ensure_connected(self):
        self.mongo.connect()

    def ensure_indexes(self):
        """创建 platform_cookies 索引"""
        self._ensure_connected()
        self.mongo.create_indexes(COLLECTION, [
            {"keys": [("platform", 1)], "options": {"unique": True}},
        ])

    def save_cookies(self, platform: str, cookie_dict: dict) -> None:
        """保存 cookie 到 MongoDB（upsert）"""
        self._ensure_connected()
        now = int(time.time())
        doc = {
            "platform": platform,
            "cookies": cookie_dict,
            "saved_at": now,
            "expires_hint": now + 7 * 86400,  # 默认 7 天过期提示
            "status": "active",
        }
        col = self.mongo.get_collection(COLLECTION)
        col.update_one(
            {"platform": platform},
            {"$set": doc},
            upsert=True,
        )
        logger.info(f"[CookieManager] 已保存 {platform} cookie ({len(cookie_dict)} 项)")

    def load_cookies(self, platform: str) -> Optional[dict]:
        """加载平台 cookie，仅返回 active 状态的"""
        self._ensure_connected()
        doc = self.mongo.find_one(COLLECTION, {
            "platform": platform,
            "status": "active",
        })
        if doc:
            return doc.get("cookies")
        return None

    def mark_expired(self, platform: str) -> None:
        """标记 cookie 为已过期，并发送告警"""
        self._ensure_connected()
        col = self.mongo.get_collection(COLLECTION)
        col.update_one(
            {"platform": platform},
            {"$set": {"status": "expired", "expired_at": int(time.time())}},
        )
        logger.warning(f"[CookieManager] {platform} cookie 已标记为过期")
        alert_cookie_expired(platform)

    def get_all_status(self) -> list[dict]:
        """获取所有平台的 cookie 状态"""
        self._ensure_connected()
        docs = self.mongo.find(COLLECTION, {})
        result = []
        for doc in docs:
            result.append({
                "platform": doc["platform"],
                "status": doc.get("status", "unknown"),
                "saved_at": doc.get("saved_at"),
                "expires_hint": doc.get("expires_hint"),
            })
        # 补充未注册平台
        registered = {d["platform"] for d in result}
        for plat in _SESSION_COOKIE_KEYS:
            if plat not in registered:
                result.append({
                    "platform": plat,
                    "status": "missing",
                    "saved_at": None,
                    "expires_hint": None,
                })
        return sorted(result, key=lambda x: x["platform"])

    def check_health(self, platform: str) -> bool:
        """通过轻量 API 调用测试 cookie 是否有效"""
        cookies = self.load_cookies(platform)
        if not cookies:
            return False

        url = _HEALTH_CHECK_URLS.get(platform)
        if not url:
            logger.warning(f"[CookieManager] 平台 {platform} 无健康检查端点")
            return True  # 无法检查则假设有效

        cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Cookie": cookie_str,
        }

        try:
            resp = httpx.get(url, headers=headers, timeout=10, follow_redirects=True)
            # 各平台判断逻辑
            if platform == "bili":
                data = resp.json()
                return data.get("code") == 0 and data.get("data", {}).get("isLogin", False)
            elif platform == "zhihu":
                return resp.status_code == 200 and "id" in resp.text
            else:
                # 通用判断：200 且未跳转到登录页
                return resp.status_code == 200 and "login" not in resp.url.path.lower()
        except Exception as e:
            logger.warning(f"[CookieManager] {platform} 健康检查异常: {e}")
            return False

    async def inject_cookies(self, browser_context: BrowserContext, platform: str) -> bool:
        """将 cookie 注入到 Playwright BrowserContext"""
        cookies = self.load_cookies(platform)
        if not cookies:
            return False

        # 平台对应的域名
        domain_map = {
            "xhs": ".xiaohongshu.com",
            "dy": ".douyin.com",
            "bili": ".bilibili.com",
            "wb": ".weibo.com",
            "ks": ".kuaishou.com",
            "tieba": ".baidu.com",
            "zhihu": ".zhihu.com",
        }
        domain = domain_map.get(platform, f".{platform}.com")

        cookie_list = []
        for name, value in cookies.items():
            cookie_list.append({
                "name": name,
                "value": str(value),
                "domain": domain,
                "path": "/",
            })

        if cookie_list:
            await browser_context.add_cookies(cookie_list)
            logger.info(f"[CookieManager] 已注入 {len(cookie_list)} 个 cookie 到 {platform}")
            return True
        return False

    @staticmethod
    def format_cookies_for_config(cookie_dict: dict) -> str:
        """将 cookie dict 格式化为 MediaCrawler config.COOKIES 需要的字符串格式"""
        return "; ".join(f"{k}={v}" for k, v in cookie_dict.items())

    @staticmethod
    def get_session_cookie_key(platform: str) -> str:
        """获取平台的 session cookie 关键字段名"""
        return _SESSION_COOKIE_KEYS.get(platform, "")
