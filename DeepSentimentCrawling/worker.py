# -*- coding: utf-8 -*-
"""
PlatformWorker — 在进程内调用 MediaCrawler 执行单个爬取任务

通过保存/恢复 MediaCrawler 的全局 config 模块实现并发安全，
每个任务执行前设置 ContextVar 以便 store 层写入 topic_id 和 crawling_task_id。
"""

import sys
import traceback
from pathlib import Path
from typing import Optional

from loguru import logger

_PROJECT_ROOT = str(Path(__file__).parent.parent)
_MC_ROOT = str(Path(__file__).parent / "MediaCrawler")

# MediaCrawler 在 sys.path 中（crawler 内部依赖需要）
if _MC_ROOT not in sys.path:
    sys.path.insert(0, _MC_ROOT)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from DeepSentimentCrawling.cookie_manager import CookieManager
from DeepSentimentCrawling.alert import alert_cookie_expired

import config as mc_config
from var import source_keyword_var, topic_id_var, crawling_task_id_var
from media_platform.bilibili import BilibiliCrawler
from media_platform.douyin import DouYinCrawler
from media_platform.kuaishou import KuaishouCrawler
from media_platform.tieba import TieBaCrawler
from media_platform.weibo import WeiboCrawler
from media_platform.xhs import XiaoHongShuCrawler
from media_platform.zhihu import ZhihuCrawler

_CRAWLERS = {
    "xhs": XiaoHongShuCrawler,
    "dy": DouYinCrawler,
    "ks": KuaishouCrawler,
    "bili": BilibiliCrawler,
    "wb": WeiboCrawler,
    "tieba": TieBaCrawler,
    "zhihu": ZhihuCrawler,
}


def _save_config() -> dict:
    """快照 MediaCrawler config 模块的所有大写属性"""
    return {k: getattr(mc_config, k) for k in dir(mc_config) if k.isupper()}


def _restore_config(saved: dict) -> None:
    """恢复 MediaCrawler config 模块到快照状态"""
    for k, v in saved.items():
        setattr(mc_config, k, v)


class PlatformWorker:
    """单平台爬取任务执行器"""

    def __init__(self, cookie_manager: Optional[CookieManager] = None):
        self.cookie_manager = cookie_manager or CookieManager()

    async def execute_task(self, task: dict) -> dict:
        """
        执行一个爬取任务

        Args:
            task: crawl_tasks 文档

        Returns:
            {"status": "success"|"failed"|"blocked", "error": "..."}
        """
        platform = task["platform"]
        task_id = task["task_id"]
        candidate_id = task["candidate_id"]

        # 1. 加载 cookie
        cookies = self.cookie_manager.load_cookies(platform)
        if not cookies:
            logger.warning(f"[Worker] {platform} 无可用 cookie，任务 {task_id} 阻塞")
            alert_cookie_expired(platform)
            return {"status": "blocked", "reason": "no_cookies"}

        # 2. 保存 MediaCrawler 全局配置快照
        saved_config = _save_config()

        try:
            # 3. 覆写 config 为当前任务参数
            mc_config.PLATFORM = platform
            mc_config.KEYWORDS = ",".join(task.get("search_keywords", []))
            mc_config.CRAWLER_MAX_NOTES_COUNT = task.get("max_notes", 20)
            mc_config.SAVE_DATA_OPTION = "db"
            mc_config.LOGIN_TYPE = "cookie"
            mc_config.COOKIES = CookieManager.format_cookies_for_config(cookies)
            mc_config.HEADLESS = True
            mc_config.ENABLE_CDP_MODE = False
            mc_config.ENABLE_GET_COMMENTS = True
            mc_config.CRAWLER_TYPE = "search"
            mc_config.ENABLE_GET_MEIDAS = False

            # 4. 设置 ContextVar
            source_keyword_var.set(task.get("topic_title", ""))
            topic_id_var.set(candidate_id)
            crawling_task_id_var.set(task_id)

            # 5. 创建并运行 crawler
            crawler_cls = _CRAWLERS.get(platform)
            if not crawler_cls:
                return {"status": "failed", "error": f"不支持的平台: {platform}"}
            crawler = crawler_cls()

            logger.info(
                f"[Worker] 开始执行任务 {task_id}: "
                f"platform={platform}, keywords={task.get('search_keywords', [])[:2]}, "
                f"max_notes={task.get('max_notes')}"
            )

            await crawler.start()

            logger.info(f"[Worker] 任务 {task_id} 执行成功")
            return {"status": "success"}

        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            logger.error(f"[Worker] 任务 {task_id} 执行失败: {error_msg}\n{traceback.format_exc()}")

            # 检查是否为 cookie 过期相关错误（只检查错误前 200 字符，避免 Chrome 启动参数误匹配）
            short_err = error_msg[:200].lower()
            if any(kw in short_err for kw in ["login", "cookie", "auth", "403", "未登录"]):
                self.cookie_manager.mark_expired(platform)
                return {"status": "failed", "error": error_msg, "cookie_expired": True}

            return {"status": "failed", "error": error_msg}

        finally:
            # 6. 恢复 config 快照
            _restore_config(saved_config)
