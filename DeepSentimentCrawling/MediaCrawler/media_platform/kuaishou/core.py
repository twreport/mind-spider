# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。


import asyncio
import os
from typing import Dict, List, Optional

from playwright.async_api import (
    BrowserContext,
    BrowserType,
    Page,
    Playwright,
    async_playwright,
)

import config
from base.base_crawler import AbstractCrawler
from model.m_kuaishou import VideoUrlInfo, CreatorUrlInfo
from proxy.proxy_ip_pool import IpInfoModel, create_ip_pool
from store import kuaishou as kuaishou_store
from tools import utils
from tools.cdp_browser import CDPBrowserManager
from var import crawler_type_var, source_keyword_var

from .client import KuaiShouClient
from .exception import DataFetchError
from .help import parse_video_info_from_url, parse_creator_info_from_url
from .login import KuaishouLogin


class KuaishouCrawler(AbstractCrawler):
    context_page: Page
    ks_client: KuaiShouClient
    browser_context: BrowserContext
    cdp_manager: Optional[CDPBrowserManager]

    def __init__(self):
        self.index_url = "https://www.kuaishou.com"
        self.user_agent = utils.get_user_agent()
        self.cdp_manager = None
        self._crawled_video_ids: set = set()

    async def start(self):
        playwright_proxy_format, httpx_proxy_format = None, None
        if config.ENABLE_IP_PROXY:
            ip_proxy_pool = await create_ip_pool(
                config.IP_PROXY_POOL_COUNT, enable_validate_ip=True
            )
            ip_proxy_info: IpInfoModel = await ip_proxy_pool.get_proxy()
            playwright_proxy_format, httpx_proxy_format = utils.format_proxy_info(
                ip_proxy_info
            )

        async with async_playwright() as playwright:
            # 根据配置选择启动模式
            if config.ENABLE_CDP_MODE:
                utils.logger.info("[KuaishouCrawler] 使用CDP模式启动浏览器")
                self.browser_context = await self.launch_browser_with_cdp(
                    playwright,
                    playwright_proxy_format,
                    self.user_agent,
                    headless=config.CDP_HEADLESS,
                )
            else:
                utils.logger.info("[KuaishouCrawler] 使用标准模式启动浏览器")
                # Launch a browser context.
                chromium = playwright.chromium
                self.browser_context = await self.launch_browser(
                    chromium, None, self.user_agent, headless=config.HEADLESS
                )
                # stealth.min.js is a js script to prevent the website from detecting the crawler.
                await self.browser_context.add_init_script(path=os.path.join(config.LIBS_DIR, "stealth.min.js"))


            self.context_page = await self.browser_context.new_page()

            # 在导航前注入 cookie，确保页面以已登录状态加载
            if config.LOGIN_TYPE == "cookie" and config.COOKIES:
                for key, value in utils.convert_str_cookie_to_dict(config.COOKIES).items():
                    await self.browser_context.add_cookies([{
                        'name': key,
                        'value': value,
                        'domain': ".kuaishou.com",
                        'path': "/"
                    }])

            await self.context_page.goto(f"{self.index_url}?isHome=1")

            # Create a client to interact with the kuaishou website.
            self.ks_client = await self.create_ks_client(httpx_proxy_format)
            if config.LOGIN_TYPE == "cookie" and config.COOKIES:
                # cookie 模式：已在导航前注入，跳过 pong/login 流程
                utils.logger.info("[KuaishouCrawler] Cookie 模式，跳过 pong/login，直接使用已注入的 cookie")
                await self.ks_client.update_cookies(browser_context=self.browser_context)
            elif not await self.ks_client.pong():
                login_obj = KuaishouLogin(
                    login_type=config.LOGIN_TYPE,
                    login_phone=httpx_proxy_format,
                    browser_context=self.browser_context,
                    context_page=self.context_page,
                    cookie_str=config.COOKIES,
                )
                await login_obj.begin()
                await self.ks_client.update_cookies(
                    browser_context=self.browser_context
                )

            crawler_type_var.set(config.CRAWLER_TYPE)
            if config.CRAWLER_TYPE == "search":
                # Search for videos and retrieve their comment information.
                await self.search()
            elif config.CRAWLER_TYPE == "detail":
                # Get the information and comments of the specified post
                await self.get_specified_videos()
            elif config.CRAWLER_TYPE == "creator":
                # Get creator's information and their videos and comments
                await self.get_creators_and_videos()
            else:
                pass

            utils.logger.info("[KuaishouCrawler.start] Kuaishou Crawler finished ...")

    async def search(self):
        utils.logger.info("[KuaishouCrawler.search] Begin search kuaishou keywords")
        ks_limit_count = 20  # kuaishou limit page fixed value
        if config.CRAWLER_MAX_NOTES_COUNT < ks_limit_count:
            config.CRAWLER_MAX_NOTES_COUNT = ks_limit_count
        max_notes = config.CRAWLER_MAX_NOTES_COUNT
        start_page = config.START_PAGE
        for keyword in config.KEYWORDS.split(","):
            search_session_id = ""
            source_keyword_var.set(keyword)
            utils.logger.info(
                f"[KuaishouCrawler.search] Current search keyword: {keyword}"
            )
            page = 1
            while (
                page - start_page + 1
            ) * ks_limit_count <= max_notes:
                if page < start_page:
                    utils.logger.info(f"[KuaishouCrawler.search] Skip page: {page}")
                    page += 1
                    continue
                utils.logger.info(
                    f"[KuaishouCrawler.search] search kuaishou keyword: {keyword}, page: {page}"
                )
                video_id_list: List[str] = []
                videos_res = await self.ks_client.search_info_by_keyword(
                    keyword=keyword,
                    pcursor=str(page),
                    search_session_id=search_session_id,
                )
                if not videos_res:
                    utils.logger.error(
                        f"[KuaishouCrawler.search] search info by keyword:{keyword} not found data"
                    )
                    continue

                vision_search_photo: Dict = videos_res.get("visionSearchPhoto")
                if vision_search_photo.get("result") != 1:
                    utils.logger.error(
                        f"[KuaishouCrawler.search] search info by keyword:{keyword} not found data "
                    )
                    continue
                search_session_id = vision_search_photo.get("searchSessionId", "")
                for video_detail in vision_search_photo.get("feeds"):
                    video_id = video_detail.get("photo", {}).get("id")
                    if video_id in self._crawled_video_ids:
                        utils.logger.info(f"[KuaishouCrawler.search] Skip duplicate video: {video_id}")
                        continue
                    self._crawled_video_ids.add(video_id)
                    video_id_list.append(video_id)
                    await kuaishou_store.update_kuaishou_video(video_item=video_detail)

                # batch fetch video comments
                page += 1

                # Sleep after page navigation
                await utils.random_sleep()
                utils.logger.info(f"[KuaishouCrawler.search] Sleeping for {config.CRAWLER_MAX_SLEEP_SEC} seconds after page {page-1}")

                await self.batch_get_video_comments(video_id_list)

    async def get_specified_videos(self):
        """Get the information and comments of the specified post"""
        utils.logger.info("[KuaishouCrawler.get_specified_videos] Parsing video URLs...")
        video_ids = []
        for video_url in config.KS_SPECIFIED_ID_LIST:
            try:
                video_info = parse_video_info_from_url(video_url)
                video_ids.append(video_info.video_id)
                utils.logger.info(f"Parsed video ID: {video_info.video_id} from {video_url}")
            except ValueError as e:
                utils.logger.error(f"Failed to parse video URL: {e}")
                continue

        semaphore = asyncio.Semaphore(config.MAX_CONCURRENCY_NUM)
        task_list = [
            self.get_video_info_task(video_id=video_id, semaphore=semaphore)
            for video_id in video_ids
        ]
        video_details = await asyncio.gather(*task_list)
        for video_detail in video_details:
            if video_detail is not None:
                await kuaishou_store.update_kuaishou_video(video_detail)
        await self.batch_get_video_comments(video_ids)

    async def get_video_info_task(
        self, video_id: str, semaphore: asyncio.Semaphore
    ) -> Optional[Dict]:
        """Get video detail task"""
        async with semaphore:
            try:
                result = await self.ks_client.get_video_info(video_id)
                
                # Sleep after fetching video details
                await utils.random_sleep()
                utils.logger.info(f"[KuaishouCrawler.get_video_info_task] Sleeping for {config.CRAWLER_MAX_SLEEP_SEC} seconds after fetching video details {video_id}")
                
                utils.logger.info(
                    f"[KuaishouCrawler.get_video_info_task] Get video_id:{video_id} info result: {result} ..."
                )
                return result.get("visionVideoDetail")
            except DataFetchError as ex:
                utils.logger.error(
                    f"[KuaishouCrawler.get_video_info_task] Get video detail error: {ex}"
                )
                return None
            except KeyError as ex:
                utils.logger.error(
                    f"[KuaishouCrawler.get_video_info_task] have not fund video detail video_id:{video_id}, err: {ex}"
                )
                return None

    async def batch_get_video_comments(self, video_id_list: List[str]):
        """
        batch get video comments (sequential - each video requires page navigation for DOM extraction)
        :param video_id_list:
        :return:
        """
        if not config.ENABLE_GET_COMMENTS:
            utils.logger.info(
                f"[KuaishouCrawler.batch_get_video_comments] Crawling comment mode is not enabled"
            )
            return

        utils.logger.info(
            f"[KuaishouCrawler.batch_get_video_comments] video ids:{video_id_list}"
        )
        # 逐个处理，因为 DOM 提取需要逐个导航到视频页面
        for video_id in video_id_list:
            await self.get_comments(video_id)

    async def get_comments(self, video_id: str):
        """
        get comment for video id (DOM extraction, sequential)
        :param video_id:
        :return:
        """
        try:
            utils.logger.info(
                f"[KuaishouCrawler.get_comments] begin get video_id: {video_id} comments ..."
            )

            await self.ks_client.get_video_all_comments(
                photo_id=video_id,
                crawl_interval=utils.get_platform_sleep_sec(),
                callback=kuaishou_store.batch_update_ks_video_comments,
                max_count=config.CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES,
            )

            # Sleep between videos
            await utils.random_sleep()

        except DataFetchError as ex:
            utils.logger.error(
                f"[KuaishouCrawler.get_comments] get video_id: {video_id} comment error: {ex}"
            )
        except Exception as e:
            utils.logger.error(
                f"[KuaishouCrawler.get_comments] video_id: {video_id} error: {e}"
            )

    async def create_ks_client(self, httpx_proxy: Optional[str]) -> KuaiShouClient:
        """Create ks client"""
        utils.logger.info(
            "[KuaishouCrawler.create_ks_client] Begin create kuaishou API client ..."
        )
        cookie_str, cookie_dict = utils.convert_cookies(
            await self.browser_context.cookies()
        )
        ks_client_obj = KuaiShouClient(
            proxy=httpx_proxy,
            headers={
                "User-Agent": self.user_agent,
                "Cookie": cookie_str,
                "Origin": self.index_url,
                "Referer": self.index_url,
                "Content-Type": "application/json;charset=UTF-8",
            },
            playwright_page=self.context_page,
            cookie_dict=cookie_dict,
        )
        return ks_client_obj

    async def launch_browser(
        self,
        chromium: BrowserType,
        playwright_proxy: Optional[Dict],
        user_agent: Optional[str],
        headless: bool = True,
    ) -> BrowserContext:
        """Launch browser and create browser context"""
        utils.logger.info(
            "[KuaishouCrawler.launch_browser] Begin create browser context ..."
        )
        if config.SAVE_LOGIN_STATE:
            user_data_dir = os.path.join(
                os.getcwd(), "browser_data", config.USER_DATA_DIR % config.PLATFORM
            )  # type: ignore
            browser_context = await chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                accept_downloads=True,
                headless=headless,
                proxy=playwright_proxy,  # type: ignore
                viewport={"width": 1920, "height": 1080},
                user_agent=user_agent,
            )
            return browser_context
        else:
            browser = await chromium.launch(headless=headless, proxy=playwright_proxy)  # type: ignore
            browser_context = await browser.new_context(
                viewport={"width": 1920, "height": 1080}, user_agent=user_agent
            )
            return browser_context

    async def launch_browser_with_cdp(
        self,
        playwright: Playwright,
        playwright_proxy: Optional[Dict],
        user_agent: Optional[str],
        headless: bool = True,
    ) -> BrowserContext:
        """
        使用CDP模式启动浏览器
        """
        try:
            self.cdp_manager = CDPBrowserManager()
            browser_context = await self.cdp_manager.launch_and_connect(
                playwright=playwright,
                playwright_proxy=playwright_proxy,
                user_agent=user_agent,
                headless=headless,
            )

            # 显示浏览器信息
            browser_info = await self.cdp_manager.get_browser_info()
            utils.logger.info(f"[KuaishouCrawler] CDP浏览器信息: {browser_info}")

            return browser_context

        except Exception as e:
            utils.logger.error(
                f"[KuaishouCrawler] CDP模式启动失败，回退到标准模式: {e}"
            )
            # 回退到标准模式
            chromium = playwright.chromium
            return await self.launch_browser(
                chromium, playwright_proxy, user_agent, headless
            )

    async def get_creators_and_videos(self) -> None:
        """Get creator's videos and retrieve their comment information."""
        utils.logger.info(
            "[KuaiShouCrawler.get_creators_and_videos] Begin get kuaishou creators"
        )
        for creator_url in config.KS_CREATOR_ID_LIST:
            try:
                # Parse creator URL to get user_id
                creator_info: CreatorUrlInfo = parse_creator_info_from_url(creator_url)
                utils.logger.info(f"[KuaiShouCrawler.get_creators_and_videos] Parse creator URL info: {creator_info}")
                user_id = creator_info.user_id

                # get creator detail info from web html content
                createor_info: Dict = await self.ks_client.get_creator_info(user_id=user_id)
                if createor_info:
                    await kuaishou_store.save_creator(user_id, creator=createor_info)
            except ValueError as e:
                utils.logger.error(f"[KuaiShouCrawler.get_creators_and_videos] Failed to parse creator URL: {e}")
                continue

            # Get all video information of the creator
            all_video_list = await self.ks_client.get_all_videos_by_creator(
                user_id=user_id,
                crawl_interval=utils.get_platform_sleep_sec(),
                callback=self.fetch_creator_video_detail,
            )

            video_ids = [
                video_item.get("photo", {}).get("id") for video_item in all_video_list
            ]
            await self.batch_get_video_comments(video_ids)

    async def fetch_creator_video_detail(self, video_list: List[Dict]):
        """
        Concurrently obtain the specified post list and save the data
        """
        semaphore = asyncio.Semaphore(config.MAX_CONCURRENCY_NUM)
        task_list = [
            self.get_video_info_task(post_item.get("photo", {}).get("id"), semaphore)
            for post_item in video_list
        ]

        video_details = await asyncio.gather(*task_list)
        for video_detail in video_details:
            if video_detail is not None:
                await kuaishou_store.update_kuaishou_video(video_detail)

    async def close(self):
        """Close browser context"""
        # 如果使用CDP模式，需要特殊处理
        if self.cdp_manager:
            await self.cdp_manager.cleanup()
            self.cdp_manager = None
        else:
            await self.browser_context.close()
        utils.logger.info("[KuaishouCrawler.close] Browser context closed ...")
