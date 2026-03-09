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
import random
from asyncio import Task
from typing import Any, Dict, List, Optional, Tuple

from playwright.async_api import (
    BrowserContext,
    BrowserType,
    Page,
    Playwright,
    async_playwright,
)

import config
from base.base_crawler import AbstractCrawler
from proxy.proxy_ip_pool import IpInfoModel, create_ip_pool
from store import douyin as douyin_store
from tools import utils
from tools.cdp_browser import CDPBrowserManager
from var import crawler_type_var, source_keyword_var

from .client import DouYinClient
from .exception import DataFetchError
from .field import PublishTimeType
from .help import parse_video_info_from_url, parse_creator_info_from_url
from .login import DouYinLogin


class DouYinCrawler(AbstractCrawler):
    context_page: Page
    dy_client: DouYinClient
    browser_context: BrowserContext
    cdp_manager: Optional[CDPBrowserManager]

    def __init__(self) -> None:
        self.index_url = "https://www.douyin.com"
        self.cdp_manager = None
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        self._crawled_aweme_ids: set = set()

    async def start(self) -> None:
        playwright_proxy_format, httpx_proxy_format = None, None
        if config.ENABLE_IP_PROXY:
            ip_proxy_pool = await create_ip_pool(config.IP_PROXY_POOL_COUNT, enable_validate_ip=True)
            ip_proxy_info: IpInfoModel = await ip_proxy_pool.get_proxy()
            playwright_proxy_format, httpx_proxy_format = utils.format_proxy_info(ip_proxy_info)

        async with async_playwright() as playwright:
            # 根据配置选择启动模式
            if config.ENABLE_CDP_MODE:
                utils.logger.info("[DouYinCrawler] 使用CDP模式启动浏览器")
                self.browser_context = await self.launch_browser_with_cdp(
                    playwright,
                    playwright_proxy_format,
                    None,
                    headless=config.CDP_HEADLESS,
                )
            else:
                utils.logger.info("[DouYinCrawler] 使用标准模式启动浏览器")
                # Launch a browser context.
                chromium = playwright.chromium
                self.browser_context = await self.launch_browser(
                    chromium,
                    playwright_proxy_format,
                    user_agent=self.user_agent,
                    headless=config.HEADLESS,
                )
                # stealth.min.js is a js script to prevent the website from detecting the crawler.
                await self.browser_context.add_init_script(path=os.path.join(config.LIBS_DIR, "stealth.min.js"))

            self.context_page = await self.browser_context.new_page()

            # 在导航前注入 cookie，确保页面以已登录状态加载
            if config.LOGIN_TYPE == "cookie" and config.COOKIES:
                cookie_dict = utils.convert_str_cookie_to_dict(config.COOKIES)
                for key, value in cookie_dict.items():
                    await self.browser_context.add_cookies([{
                        'name': key,
                        'value': value,
                        'domain': ".douyin.com",
                        'path': "/"
                    }])
                # 补充 LOGIN_STATUS=1，Chrome 扩展可能未导出此 cookie，
                # 但有 sessionid 即代表已登录
                if "sessionid" in cookie_dict and "LOGIN_STATUS" not in cookie_dict:
                    await self.browser_context.add_cookies([{
                        'name': 'LOGIN_STATUS',
                        'value': '1',
                        'domain': ".douyin.com",
                        'path': "/"
                    }])
                    utils.logger.info("[DouYinCrawler] 自动补充 LOGIN_STATUS=1 cookie")

            await self.context_page.goto(self.index_url)
            # 等待页面 JS 初始化完成（设置 localStorage 等）
            await asyncio.sleep(3)

            self.dy_client = await self.create_douyin_client(httpx_proxy_format)
            if config.LOGIN_TYPE == "cookie" and config.COOKIES:
                # cookie 模式：已在导航前注入，跳过 pong/login 流程
                # 直接从浏览器上下文更新客户端 cookie（包含服务端新设置的 cookie）
                utils.logger.info("[DouYinCrawler] Cookie 模式，跳过 pong/login，直接使用已注入的 cookie")
                await self.dy_client.update_cookies(browser_context=self.browser_context)
            elif not await self.dy_client.pong(browser_context=self.browser_context):
                login_obj = DouYinLogin(
                    login_type=config.LOGIN_TYPE,
                    login_phone="",  # you phone number
                    browser_context=self.browser_context,
                    context_page=self.context_page,
                    cookie_str=config.COOKIES,
                )
                await login_obj.begin()
                await self.dy_client.update_cookies(browser_context=self.browser_context)
            crawler_type_var.set(config.CRAWLER_TYPE)
            if config.CRAWLER_TYPE == "search":
                # Search for notes and retrieve their comment information.
                await self.search()
            elif config.CRAWLER_TYPE == "detail":
                # Get the information and comments of the specified post
                await self.get_specified_awemes()
            elif config.CRAWLER_TYPE == "creator":
                # Get the information and comments of the specified creator
                await self.get_creators_and_videos()

            utils.logger.info("[DouYinCrawler.start] Douyin Crawler finished ...")

    async def search(self) -> None:
        """Search douyin keywords using search box + response interception.

        The douyin search API now returns verify_check for direct API calls
        (httpx + a_bogus). However, typing keywords into the search box on the
        homepage triggers the browser's own signed request which works correctly.
        We intercept the browser's /search/single/ responses to extract data.
        """
        utils.logger.info("[DouYinCrawler.search] Begin search douyin keywords")
        max_notes = config.CRAWLER_MAX_NOTES_COUNT

        for keyword in config.KEYWORDS.split(","):
            source_keyword_var.set(keyword)
            utils.logger.info(f"[DouYinCrawler.search] Current keyword: {keyword}")
            aweme_list: List[str] = []

            # Collect aweme_info items from intercepted API responses
            intercepted_items: List[Dict] = []

            async def on_search_response(response):
                """Intercept /search/single/ responses from the browser."""
                try:
                    if "/search/single/" in response.url and response.status == 200:
                        body = await response.json()
                        for item in body.get("data", []):
                            aweme_info = item.get("aweme_info")
                            if aweme_info:
                                intercepted_items.append(aweme_info)
                except Exception:
                    pass

            self.context_page.on("response", on_search_response)

            try:
                # Navigate to homepage first
                await self.context_page.goto(self.index_url, wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(5)

                page_url = self.context_page.url
                page_title = await self.context_page.title()
                utils.logger.info(
                    f"[DouYinCrawler.search] Homepage loaded: url={page_url}, title={page_title}"
                )

                # Find and use the search box (wait for it to appear)
                search_selectors = (
                    'input[data-e2e="searchbar-input"], input[placeholder*="搜索"], '
                    '#search-content-input, input[type="search"], '
                    'input[class*="search"], input[class*="Search"]'
                )
                search_input = None
                try:
                    search_input = await self.context_page.wait_for_selector(
                        search_selectors, timeout=10000
                    )
                except Exception:
                    # wait_for_selector timed out, try query_selector as fallback
                    search_input = await self.context_page.query_selector(search_selectors)

                if not search_input:
                    # Debug: dump all input elements on page
                    inputs_debug = await self.context_page.evaluate("""
                        () => Array.from(document.querySelectorAll('input')).map(i => ({
                            type: i.type, placeholder: i.placeholder,
                            className: (i.className || '').slice(0, 60),
                            id: i.id, visible: i.offsetHeight > 0,
                        })).slice(0, 10)
                    """)
                    utils.logger.error(
                        f"[DouYinCrawler.search] Could not find search input. "
                        f"Page inputs: {inputs_debug}"
                    )
                    utils.logger.error("[DouYinCrawler.search] Could not find search input on homepage")
                    # Fallback to API search
                    await self._search_via_api(keyword, max_notes, aweme_list)
                else:
                    await search_input.click()
                    await asyncio.sleep(0.5)
                    await search_input.fill(keyword)
                    await asyncio.sleep(0.5)
                    await self.context_page.keyboard.press("Enter")
                    await asyncio.sleep(8)

                    title = await self.context_page.title()
                    if "验证" in title:
                        utils.logger.warning("[DouYinCrawler.search] Search triggered CAPTCHA page, falling back to API")
                        await self._search_via_api(keyword, max_notes, aweme_list)
                    else:
                        utils.logger.info(
                            f"[DouYinCrawler.search] Search page loaded: {title}, "
                            f"intercepted {len(intercepted_items)} items so far"
                        )

                        # Scroll to trigger lazy loading and load more results
                        collected = len(intercepted_items)
                        scroll_rounds = max(1, (max_notes - collected) // 10 + 1)
                        for i in range(min(scroll_rounds, 10)):
                            if len(intercepted_items) >= max_notes:
                                break
                            await self.context_page.evaluate(f"window.scrollTo(0, {(i + 1) * 1000})")
                            await asyncio.sleep(3)
                            utils.logger.info(
                                f"[DouYinCrawler.search] Scroll {i+1}, intercepted {len(intercepted_items)} items"
                            )

                        # Process intercepted items
                        for aweme_info in intercepted_items[:max_notes]:
                            aweme_id = aweme_info.get("aweme_id", "")
                            if aweme_id and aweme_id not in self._crawled_aweme_ids:
                                self._crawled_aweme_ids.add(aweme_id)
                                aweme_list.append(aweme_id)
                                await douyin_store.update_douyin_aweme(aweme_item=aweme_info)
                                await self.get_aweme_media(aweme_item=aweme_info)

                        utils.logger.info(
                            f"[DouYinCrawler.search] keyword:{keyword}, "
                            f"intercepted {len(intercepted_items)} items, "
                            f"stored {len(aweme_list)} unique aweme_ids"
                        )

                        # If interception got nothing, fall back to API
                        if not aweme_list:
                            utils.logger.warning("[DouYinCrawler.search] No results from interception, trying API fallback")
                            await self._search_via_api(keyword, max_notes, aweme_list)

            finally:
                # Remove the response listener
                self.context_page.remove_listener("response", on_search_response)

            utils.logger.info(f"[DouYinCrawler.search] keyword:{keyword}, aweme_list:{aweme_list}")
            await self.batch_get_note_comments(aweme_list)

    async def _search_via_api(self, keyword: str, max_notes: int, aweme_list: List[str]) -> None:
        """Fallback: search via direct API call (httpx + a_bogus).

        May fail with verify_check if the signing is outdated.
        """
        utils.logger.info(f"[DouYinCrawler._search_via_api] Trying API search for: {keyword}")
        dy_limit_count = 10
        start_page = config.START_PAGE
        page = 0
        dy_search_id = ""
        while (page - start_page + 1) * dy_limit_count <= max_notes:
            if page < start_page:
                page += 1
                continue
            try:
                posts_res = await self.dy_client.search_info_by_keyword(
                    keyword=keyword,
                    offset=page * dy_limit_count - dy_limit_count,
                    publish_time=PublishTimeType(config.PUBLISH_TIME_TYPE),
                    search_id=dy_search_id,
                )
                if "aweme_list" in posts_res and "data" not in posts_res:
                    posts_res["data"] = [{"aweme_info": item} for item in posts_res["aweme_list"]]

                if not posts_res.get("data"):
                    nil_type = posts_res.get("search_nil_info", {}).get("search_nil_type", "")
                    utils.logger.info(
                        f"[DouYinCrawler._search_via_api] Empty result. "
                        f"status={posts_res.get('status_code')}, nil_type={nil_type}"
                    )
                    break
            except DataFetchError:
                utils.logger.error(f"[DouYinCrawler._search_via_api] search failed for: {keyword}")
                break

            page += 1
            if "data" not in posts_res:
                break
            dy_search_id = posts_res.get("extra", {}).get("logid", "")
            for post_item in posts_res.get("data"):
                try:
                    aweme_info: Dict = (
                        post_item.get("aweme_info")
                        or post_item.get("aweme_mix_info", {}).get("mix_items")[0]
                    )
                except TypeError:
                    continue
                aweme_id = aweme_info.get("aweme_id", "")
                if aweme_id and aweme_id not in self._crawled_aweme_ids:
                    self._crawled_aweme_ids.add(aweme_id)
                    aweme_list.append(aweme_id)
                    await douyin_store.update_douyin_aweme(aweme_item=aweme_info)
                    await self.get_aweme_media(aweme_item=aweme_info)
            await utils.random_sleep()

    async def get_specified_awemes(self):
        """Get the information and comments of the specified post from URLs or IDs"""
        utils.logger.info("[DouYinCrawler.get_specified_awemes] Parsing video URLs...")
        aweme_id_list = []
        for video_url in config.DY_SPECIFIED_ID_LIST:
            try:
                video_info = parse_video_info_from_url(video_url)

                # 处理短链接
                if video_info.url_type == "short":
                    utils.logger.info(f"[DouYinCrawler.get_specified_awemes] Resolving short link: {video_url}")
                    resolved_url = await self.dy_client.resolve_short_url(video_url)
                    if resolved_url:
                        # 从解析后的URL中提取视频ID
                        video_info = parse_video_info_from_url(resolved_url)
                        utils.logger.info(f"[DouYinCrawler.get_specified_awemes] Short link resolved to aweme ID: {video_info.aweme_id}")
                    else:
                        utils.logger.error(f"[DouYinCrawler.get_specified_awemes] Failed to resolve short link: {video_url}")
                        continue

                aweme_id_list.append(video_info.aweme_id)
                utils.logger.info(f"[DouYinCrawler.get_specified_awemes] Parsed aweme ID: {video_info.aweme_id} from {video_url}")
            except ValueError as e:
                utils.logger.error(f"[DouYinCrawler.get_specified_awemes] Failed to parse video URL: {e}")
                continue

        semaphore = asyncio.Semaphore(config.MAX_CONCURRENCY_NUM)
        task_list = [self.get_aweme_detail(aweme_id=aweme_id, semaphore=semaphore) for aweme_id in aweme_id_list]
        aweme_details = await asyncio.gather(*task_list)
        for aweme_detail in aweme_details:
            if aweme_detail is not None:
                await douyin_store.update_douyin_aweme(aweme_item=aweme_detail)
                await self.get_aweme_media(aweme_item=aweme_detail)
        await self.batch_get_note_comments(aweme_id_list)

    async def get_aweme_detail(self, aweme_id: str, semaphore: asyncio.Semaphore) -> Any:
        """Get note detail"""
        async with semaphore:
            try:
                result = await self.dy_client.get_video_by_id(aweme_id)
                # Sleep after fetching aweme detail
                await utils.random_sleep()
                utils.logger.info(f"[DouYinCrawler.get_aweme_detail] Sleeping for {config.CRAWLER_MAX_SLEEP_SEC} seconds after fetching aweme {aweme_id}")
                return result
            except DataFetchError as ex:
                utils.logger.error(f"[DouYinCrawler.get_aweme_detail] Get aweme detail error: {ex}")
                return None
            except KeyError as ex:
                utils.logger.error(f"[DouYinCrawler.get_aweme_detail] have not fund note detail aweme_id:{aweme_id}, err: {ex}")
                return None

    async def batch_get_note_comments(self, aweme_list: List[str]) -> None:
        """
        Batch get note comments
        """
        if not config.ENABLE_GET_COMMENTS:
            utils.logger.info(f"[DouYinCrawler.batch_get_note_comments] Crawling comment mode is not enabled")
            return

        task_list: List[Task] = []
        semaphore = asyncio.Semaphore(config.MAX_CONCURRENCY_NUM)
        for aweme_id in aweme_list:
            task = asyncio.create_task(self.get_comments(aweme_id, semaphore), name=aweme_id)
            task_list.append(task)
        if len(task_list) > 0:
            await asyncio.wait(task_list)

    async def get_comments(self, aweme_id: str, semaphore: asyncio.Semaphore) -> None:
        async with semaphore:
            try:
                # 将关键词列表传递给 get_aweme_all_comments 方法
                # Use platform-specific crawling interval
                crawl_interval = utils.get_platform_sleep_sec()
                await self.dy_client.get_aweme_all_comments(
                    aweme_id=aweme_id,
                    crawl_interval=crawl_interval,
                    is_fetch_sub_comments=config.ENABLE_GET_SUB_COMMENTS,
                    callback=douyin_store.batch_update_dy_aweme_comments,
                    max_count=config.CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES,
                )
                # Sleep after fetching comments
                await asyncio.sleep(crawl_interval)
                utils.logger.info(f"[DouYinCrawler.get_comments] Sleeping for {crawl_interval} seconds after fetching comments for aweme {aweme_id}")
                utils.logger.info(f"[DouYinCrawler.get_comments] aweme_id: {aweme_id} comments have all been obtained and filtered ...")
            except DataFetchError as e:
                utils.logger.error(f"[DouYinCrawler.get_comments] aweme_id: {aweme_id} get comments failed, error: {e}")

    async def get_creators_and_videos(self) -> None:
        """
        Get the information and videos of the specified creator from URLs or IDs
        """
        utils.logger.info("[DouYinCrawler.get_creators_and_videos] Begin get douyin creators")
        utils.logger.info("[DouYinCrawler.get_creators_and_videos] Parsing creator URLs...")

        for creator_url in config.DY_CREATOR_ID_LIST:
            try:
                creator_info_parsed = parse_creator_info_from_url(creator_url)
                user_id = creator_info_parsed.sec_user_id
                utils.logger.info(f"[DouYinCrawler.get_creators_and_videos] Parsed sec_user_id: {user_id} from {creator_url}")
            except ValueError as e:
                utils.logger.error(f"[DouYinCrawler.get_creators_and_videos] Failed to parse creator URL: {e}")
                continue

            creator_info: Dict = await self.dy_client.get_user_info(user_id)
            if creator_info:
                await douyin_store.save_creator(user_id, creator=creator_info)

            # Get all video information of the creator
            all_video_list = await self.dy_client.get_all_user_aweme_posts(sec_user_id=user_id, callback=self.fetch_creator_video_detail)

            video_ids = [video_item.get("aweme_id") for video_item in all_video_list]
            await self.batch_get_note_comments(video_ids)

    async def fetch_creator_video_detail(self, video_list: List[Dict]):
        """
        Concurrently obtain the specified post list and save the data
        """
        semaphore = asyncio.Semaphore(config.MAX_CONCURRENCY_NUM)
        task_list = [self.get_aweme_detail(post_item.get("aweme_id"), semaphore) for post_item in video_list]

        note_details = await asyncio.gather(*task_list)
        for aweme_item in note_details:
            if aweme_item is not None:
                await douyin_store.update_douyin_aweme(aweme_item=aweme_item)
                await self.get_aweme_media(aweme_item=aweme_item)

    async def create_douyin_client(self, httpx_proxy: Optional[str]) -> DouYinClient:
        """Create douyin client"""
        cookie_str, cookie_dict = utils.convert_cookies(await self.browser_context.cookies())  # type: ignore
        douyin_client = DouYinClient(
            proxy=httpx_proxy,
            headers={
                "User-Agent": await self.context_page.evaluate("() => navigator.userAgent"),
                "Cookie": cookie_str,
                "Host": "www.douyin.com",
                "Origin": "https://www.douyin.com/",
                "Referer": "https://www.douyin.com/",
                "Content-Type": "application/json;charset=UTF-8",
            },
            playwright_page=self.context_page,
            cookie_dict=cookie_dict,
        )
        return douyin_client

    async def launch_browser(
        self,
        chromium: BrowserType,
        playwright_proxy: Optional[Dict],
        user_agent: Optional[str],
        headless: bool = True,
    ) -> BrowserContext:
        """Launch browser and create browser context"""
        if config.SAVE_LOGIN_STATE:
            user_data_dir = os.path.join(os.getcwd(), "browser_data", config.USER_DATA_DIR % config.PLATFORM)  # type: ignore
            browser_context = await chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                accept_downloads=True,
                headless=headless,
                proxy=playwright_proxy,  # type: ignore
                viewport={
                    "width": 1920,
                    "height": 1080
                },
                user_agent=user_agent,
            )  # type: ignore
            return browser_context
        else:
            browser = await chromium.launch(headless=headless, proxy=playwright_proxy)  # type: ignore
            browser_context = await browser.new_context(viewport={"width": 1920, "height": 1080}, user_agent=user_agent)
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

            # 添加反检测脚本
            await self.cdp_manager.add_stealth_script()

            # 显示浏览器信息
            browser_info = await self.cdp_manager.get_browser_info()
            utils.logger.info(f"[DouYinCrawler] CDP浏览器信息: {browser_info}")

            return browser_context

        except Exception as e:
            utils.logger.error(f"[DouYinCrawler] CDP模式启动失败，回退到标准模式: {e}")
            # 回退到标准模式
            chromium = playwright.chromium
            return await self.launch_browser(chromium, playwright_proxy, user_agent, headless)

    async def close(self) -> None:
        """Close browser context"""
        # 如果使用CDP模式，需要特殊处理
        if self.cdp_manager:
            await self.cdp_manager.cleanup()
            self.cdp_manager = None
        else:
            await self.browser_context.close()
        utils.logger.info("[DouYinCrawler.close] Browser context closed ...")

    async def get_aweme_media(self, aweme_item: Dict):
        """
        获取抖音媒体，自动判断媒体类型是短视频还是帖子图片并下载

        Args:
            aweme_item (Dict): 抖音作品详情
        """
        if not config.ENABLE_GET_MEIDAS:
            utils.logger.info(f"[DouYinCrawler.get_aweme_media] Crawling image mode is not enabled")
            return
        # 笔记 urls 列表，若为短视频类型则返回为空列表
        note_download_url: List[str] = douyin_store._extract_note_image_list(aweme_item)
        # 视频 url，永远存在，但为短视频类型时的文件其实是音频文件
        video_download_url: str = douyin_store._extract_video_download_url(aweme_item)
        # TODO: 抖音并没采用音视频分离的策略，故音频可从原视频中分离，暂不提取
        if note_download_url:
            await self.get_aweme_images(aweme_item)
        else:
            await self.get_aweme_video(aweme_item)

    async def get_aweme_images(self, aweme_item: Dict):
        """
        get aweme images. please use get_aweme_media
        
        Args:
            aweme_item (Dict): 抖音作品详情
        """
        if not config.ENABLE_GET_MEIDAS:
            return
        aweme_id = aweme_item.get("aweme_id")
        # 笔记 urls 列表，若为短视频类型则返回为空列表
        note_download_url: List[str] = douyin_store._extract_note_image_list(aweme_item)

        if not note_download_url:
            return
        picNum = 0
        for url in note_download_url:
            if not url:
                continue
            content = await self.dy_client.get_aweme_media(url)
            await asyncio.sleep(random.random())
            if content is None:
                continue
            extension_file_name = f"{picNum:>03d}.jpeg"
            picNum += 1
            await douyin_store.update_dy_aweme_image(aweme_id, content, extension_file_name)

    async def get_aweme_video(self, aweme_item: Dict):
        """
        get aweme videos. please use get_aweme_media

        Args:
            aweme_item (Dict): 抖音作品详情
        """
        if not config.ENABLE_GET_MEIDAS:
            return
        aweme_id = aweme_item.get("aweme_id")

        # 视频 url，永远存在，但为短视频类型时的文件其实是音频文件
        video_download_url: str = douyin_store._extract_video_download_url(aweme_item)

        if not video_download_url:
            return
        content = await self.dy_client.get_aweme_media(video_download_url)
        await asyncio.sleep(random.random())
        if content is None:
            return
        extension_file_name = f"video.mp4"
        await douyin_store.update_dy_aweme_video(aweme_id, content, extension_file_name)
