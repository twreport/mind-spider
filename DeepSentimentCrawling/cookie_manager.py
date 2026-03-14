# -*- coding: utf-8 -*-
"""
Cookie 管理器 — MongoDB 持久化 + 健康检查 + Playwright 注入

管理 7 个平台的 cookie 生命周期：保存 → 加载 → 检测过期 → 告警 → 重新登录。
支持 cookie 池：每个平台可有多个 cookie，按 session cookie 值的 SHA256 自动去重。
"""

import hashlib
import random
import time
from typing import Optional

import httpx
from loguru import logger
from playwright.async_api import BrowserContext

import sys
from pathlib import Path

_PROJECT_ROOT = str(Path(__file__).parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
from ms_config import settings

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
    """Cookie 持久化管理器（支持 cookie 池）"""

    def __init__(self, mongo_writer: Optional[MongoWriter] = None):
        self.mongo = mongo_writer or MongoWriter(
            db_name=settings.MONGO_SIGNAL_DB_NAME
        )

    def _ensure_connected(self):
        self.mongo.connect()

    @staticmethod
    def _generate_cookie_id(platform: str, cookie_dict: dict) -> str:
        """基于 session cookie 值的 SHA256 前 8 位生成 cookie_id"""
        session_key = _SESSION_COOKIE_KEYS.get(platform, "")
        session_value = str(cookie_dict.get(session_key, ""))
        if not session_value:
            # fallback: 用整个 cookie dict 的排序字符串
            session_value = str(sorted(cookie_dict.items()))
        hash_prefix = hashlib.sha256(session_value.encode()).hexdigest()[:8]
        return f"{platform}_{hash_prefix}"

    def ensure_indexes(self):
        """创建 platform_cookies 索引，并自动迁移旧格式文档"""
        self._ensure_connected()
        col = self.mongo.get_collection(COLLECTION)

        # 删除旧的 platform unique 索引（如果存在）
        try:
            existing_indexes = col.index_information()
            for idx_name, idx_info in existing_indexes.items():
                keys = idx_info.get("key", [])
                # 查找 platform 字段的 unique 索引
                if (
                    keys == [("platform", 1)]
                    and idx_info.get("unique", False)
                ):
                    col.drop_index(idx_name)
                    logger.info(f"[CookieManager] 已删除旧 platform unique 索引: {idx_name}")
                    break
        except Exception as e:
            logger.warning(f"[CookieManager] 检查旧索引时异常: {e}")

        # 创建新索引
        self.mongo.create_indexes(COLLECTION, [
            {"keys": [("cookie_id", 1)], "options": {"unique": True}},
            {"keys": [("platform", 1), ("status", 1)]},
        ])

        # 自动迁移：为没有 cookie_id 的旧文档补充 cookie_id
        try:
            old_docs = list(col.find({"cookie_id": {"$exists": False}}))
            for doc in old_docs:
                platform = doc.get("platform", "unknown")
                cookies = doc.get("cookies", {})
                cookie_id = self._generate_cookie_id(platform, cookies)
                col.update_one(
                    {"_id": doc["_id"]},
                    {"$set": {"cookie_id": cookie_id}},
                )
                logger.info(f"[CookieManager] 迁移旧文档: {platform} → {cookie_id}")
        except Exception as e:
            logger.warning(f"[CookieManager] 迁移旧文档异常: {e}")

    def save_cookies(self, platform: str, cookie_dict: dict) -> str:
        """保存 cookie 到 MongoDB（按 cookie_id upsert），返回 cookie_id"""
        self._ensure_connected()
        cookie_id = self._generate_cookie_id(platform, cookie_dict)
        now = int(time.time())
        doc = {
            "cookie_id": cookie_id,
            "platform": platform,
            "cookies": cookie_dict,
            "saved_at": now,
            "status": "active",
        }
        col = self.mongo.get_collection(COLLECTION)
        col.update_one(
            {"cookie_id": cookie_id},
            {"$set": doc},
            upsert=True,
        )
        logger.info(f"[CookieManager] 已保存 {platform} cookie {cookie_id} ({len(cookie_dict)} 项)")
        return cookie_id

    def load_cookies(self, platform: str) -> Optional[tuple[str, dict]]:
        """加载平台 cookie，从所有 active cookie 中随机选一个，返回 (cookie_id, cookies_dict)"""
        self._ensure_connected()
        col = self.mongo.get_collection(COLLECTION)
        active_docs = list(col.find({
            "platform": platform,
            "status": "active",
        }))
        if not active_docs:
            return None
        doc = random.choice(active_docs)
        return (doc["cookie_id"], doc.get("cookies", {}))

    def mark_expired(self, platform: str, cookie_id: Optional[str] = None) -> None:
        """标记 cookie 为已过期，并发送告警。有 cookie_id 时只过期该条。"""
        self._ensure_connected()
        col = self.mongo.get_collection(COLLECTION)
        if cookie_id:
            col.update_one(
                {"cookie_id": cookie_id},
                {"$set": {"status": "expired", "expired_at": int(time.time())}},
            )
            logger.warning(f"[CookieManager] {platform} cookie {cookie_id} 已标记为过期")
        else:
            col.update_many(
                {"platform": platform},
                {"$set": {"status": "expired", "expired_at": int(time.time())}},
            )
            logger.warning(f"[CookieManager] {platform} 所有 cookie 已标记为过期")
        alert_cookie_expired(platform)

    def has_active_cookies(self, platform: str) -> bool:
        """检查平台是否有可用的 active cookie"""
        self._ensure_connected()
        col = self.mongo.get_collection(COLLECTION)
        return col.count_documents({"platform": platform, "status": "active"}) > 0

    def get_all_status(self) -> list[dict]:
        """获取所有 cookie 条目的状态（含 cookie_id）"""
        self._ensure_connected()
        docs = self.mongo.find(COLLECTION, {})
        result = []
        for doc in docs:
            result.append({
                "cookie_id": doc.get("cookie_id", ""),
                "platform": doc["platform"],
                "status": doc.get("status", "unknown"),
                "saved_at": doc.get("saved_at"),
            })
        # 补充未注册平台
        registered = {d["platform"] for d in result}
        for plat in _SESSION_COOKIE_KEYS:
            if plat not in registered:
                result.append({
                    "cookie_id": "",
                    "platform": plat,
                    "status": "missing",
                    "saved_at": None,
                })
        return sorted(result, key=lambda x: (x["platform"], x.get("cookie_id", "")))

    def check_health(self, platform: str) -> bool:
        """通过轻量 API 调用测试 cookie 是否有效"""
        loaded = self.load_cookies(platform)
        if not loaded:
            return False
        cookie_id, cookies = loaded

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
        loaded = self.load_cookies(platform)
        if not loaded:
            return False
        cookie_id, cookies = loaded

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
            logger.info(f"[CookieManager] 已注入 {len(cookie_list)} 个 cookie 到 {platform} (cookie_id={cookie_id})")
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
