# -*- coding: utf-8 -*-
"""
PlatformWorker — 在进程内调用 MediaCrawler 执行单个爬取任务

通过 _mc_import_context 上下文管理器临时切换 sys.path / sys.modules，
使 MediaCrawler 内部的 `import config` / `from main import ...` 解析到正确的模块。
每个任务执行前设置 ContextVar 以便 store 层写入 topic_id 和 crawling_task_id。
"""

import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from loguru import logger

_PROJECT_ROOT = str(Path(__file__).parent.parent)
_MC_ROOT = str(Path(__file__).parent / "MediaCrawler")

# 确保 MediaCrawler 在 sys.path 中（crawler 内部依赖需要）
if _MC_ROOT not in sys.path:
    sys.path.append(_MC_ROOT)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from DeepSentimentCrawling.cookie_manager import CookieManager
from DeepSentimentCrawling.alert import alert_cookie_expired

# 项目根和 MediaCrawler 都有同名模块：config（项目根是.py 文件，MC 是包）、main
_CONFLICTING = {"config", "main"}

# 缓存 MC 的冲突模块对象，跨任务复用（避免每次重新加载，也保持引用一致性）
_mc_module_cache: dict = {}


@contextmanager
def _mc_import_context():
    """
    上下文管理器：临时将 sys.modules / sys.path 调整为 MediaCrawler 视角。

    解决核心冲突：
    - 项目根有 config.py（Pydantic Settings）和 main.py（MindSpider 入口）
    - MediaCrawler 也有 config/（包，含 db_config 等子模块）和 main.py（CrawlerFactory）

    进入时：保存并移除项目根的 config/main，注入 MC 缓存，MC_ROOT 置于 sys.path 首位
    退出时：缓存 MC 的 config/main，恢复项目根模块和 sys.path
    """
    # 1. 保存并移除项目根的冲突模块
    saved_modules = {}
    for key in list(sys.modules.keys()):
        if key.split(".")[0] in _CONFLICTING:
            saved_modules[key] = sys.modules.pop(key)

    # 2. 注入之前缓存的 MC 模块（保持跨任务对象引用一致）
    sys.modules.update(_mc_module_cache)

    # 3. MC_ROOT 置于 sys.path 最前，确保新导入解析到 MC 模块
    saved_path = sys.path[:]
    sys.path = [_MC_ROOT] + [p for p in sys.path if p != _MC_ROOT]

    try:
        yield
    finally:
        # 4. 缓存 MC 的冲突模块（供下次任务复用）
        _mc_module_cache.clear()
        for key in list(sys.modules.keys()):
            if key.split(".")[0] in _CONFLICTING:
                _mc_module_cache[key] = sys.modules.pop(key)

        # 5. 恢复项目根模块
        sys.modules.update(saved_modules)

        # 6. 恢复 sys.path
        sys.path[:] = saved_path


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

        # 2. 在 MC 导入上下文中执行（解决 config/main 模块冲突）
        with _mc_import_context():
            import config as mc_config

            # 3. 快照全局配置
            saved_config = {k: getattr(mc_config, k) for k in dir(mc_config) if k.isupper()}

            try:
                # 4. 覆写 config 为当前任务参数
                mc_config.PLATFORM = platform
                mc_config.KEYWORDS = ",".join(task.get("search_keywords", []))
                mc_config.CRAWLER_MAX_NOTES_COUNT = task.get("max_notes", 20)
                mc_config.SAVE_DATA_OPTION = "db"
                mc_config.LOGIN_TYPE = "cookie"
                mc_config.COOKIES = CookieManager.format_cookies_for_config(cookies)
                mc_config.HEADLESS = True
                mc_config.ENABLE_GET_COMMENTS = True
                mc_config.CRAWLER_TYPE = "search"
                mc_config.ENABLE_GET_MEIDAS = False

                # 5. 设置 ContextVar
                from var import source_keyword_var, topic_id_var, crawling_task_id_var
                source_keyword_var.set(task.get("topic_title", ""))
                topic_id_var.set(candidate_id)
                crawling_task_id_var.set(task_id)

                # 6. 创建并运行 crawler
                from main import CrawlerFactory
                crawler = CrawlerFactory.create_crawler(platform)

                logger.info(
                    f"[Worker] 开始执行任务 {task_id}: "
                    f"platform={platform}, keywords={task.get('search_keywords', [])[:2]}, "
                    f"max_notes={task.get('max_notes')}"
                )

                await crawler.start()

                logger.info(f"[Worker] 任务 {task_id} 执行成功")
                return {"status": "success"}

            except Exception as e:
                error_msg = str(e)
                logger.error(f"[Worker] 任务 {task_id} 执行失败: {error_msg}")

                # 导入失败时清除 MC 模块缓存，下次任务重新加载
                if "module" in error_msg.lower() or "import" in error_msg.lower():
                    _mc_module_cache.clear()

                # 检查是否为 cookie 过期相关错误
                if any(kw in error_msg.lower() for kw in ["login", "cookie", "auth", "403", "未登录"]):
                    self.cookie_manager.mark_expired(platform)
                    return {"status": "failed", "error": error_msg, "cookie_expired": True}

                return {"status": "failed", "error": error_msg}

            finally:
                # 7. 恢复 config 快照
                for k, v in saved_config.items():
                    setattr(mc_config, k, v)
