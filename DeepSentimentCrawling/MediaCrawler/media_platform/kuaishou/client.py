# 声明：本代码仅供学习和研究目的使用。使用者应遵守以下原则：
# 1. 不得用于任何商业用途。
# 2. 使用时应遵守目标平台的使用条款和robots.txt规则。
# 3. 不得进行大规模爬取或对平台造成运营干扰。
# 4. 应合理控制请求频率，避免给目标平台带来不必要的负担。
# 5. 不得用于任何非法或不当的用途。
#
# 详细许可条款请参阅项目根目录下的LICENSE文件。
# 使用本代码即表示您同意遵守上述原则和LICENSE中的所有条款。


# -*- coding: utf-8 -*-
import asyncio
import hashlib
import json
import time
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlencode

import httpx
from playwright.async_api import BrowserContext, Page

import config
from base.base_crawler import AbstractApiClient
from tools import utils

from .exception import DataFetchError
from .graphql import KuaiShouGraphQL

# DOM 评论提取 JS（快手 GraphQL commentListQuery 已废弃，改用 SSR DOM 提取）
EXTRACT_COMMENTS_JS = """
() => {
    const results = [];
    const items = document.querySelectorAll('.comment-item.comment-list-item');
    items.forEach((item, idx) => {
        const authorEl = item.querySelector('.author-name');
        const timeEl = item.querySelector('.comment-item-time');
        const contentEl = item.querySelector('.comment-item-content');
        const avatarEl = item.querySelector('.comment-item-portrait');
        const profileEl = item.querySelector('.comment-item-author a[href], .router-link a[href]');

        const author = authorEl ? authorEl.textContent.trim() : '';
        const timeStr = timeEl ? timeEl.textContent.trim() : '';
        const content = contentEl ? contentEl.textContent.trim() : '';
        const avatar = avatarEl ? (avatarEl.getAttribute('src') || '') : '';
        const profileHref = profileEl ? (profileEl.getAttribute('href') || '') : '';

        // 提取 authorId: 从 profile 链接 /profile/xxx 中提取
        let authorId = '';
        if (profileHref) {
            const m = profileHref.match(/\\/profile\\/([\\w]+)/);
            if (m) authorId = m[1];
        }

        results.push({
            author: author,
            authorId: authorId,
            time: timeStr,
            content: content,
            avatar: avatar,
            index: idx,
        });
    });
    return results;
}
"""


class KuaiShouClient(AbstractApiClient):
    def __init__(
        self,
        timeout=10,
        proxy=None,
        *,
        headers: Dict[str, str],
        playwright_page: Page,
        cookie_dict: Dict[str, str],
    ):
        self.proxy = proxy
        self.timeout = timeout
        self.headers = headers
        self._host = "https://www.kuaishou.com/graphql"
        self.playwright_page = playwright_page
        self.cookie_dict = cookie_dict
        self.graphql = KuaiShouGraphQL()

    async def request(self, method, url, **kwargs) -> Any:
        async with httpx.AsyncClient(proxy=self.proxy) as client:
            response = await client.request(method, url, timeout=self.timeout, **kwargs)
        data: Dict = response.json()
        if data.get("errors"):
            utils.logger.error(f"[KuaiShouClient.request] GraphQL errors: {data.get('errors')}")
            raise DataFetchError(data.get("errors", "unkonw error"))
        else:
            return data.get("data", {})

    async def get(self, uri: str, params=None) -> Dict:
        final_uri = uri
        if isinstance(params, dict):
            final_uri = f"{uri}?" f"{urlencode(params)}"
        return await self.request(
            method="GET", url=f"{self._host}{final_uri}", headers=self.headers
        )

    async def post(self, uri: str, data: dict) -> Dict:
        json_str = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
        return await self.request(
            method="POST", url=f"{self._host}{uri}", data=json_str, headers=self.headers
        )

    async def pong(self) -> bool:
        """get a note to check if login state is ok"""
        utils.logger.info("[KuaiShouClient.pong] Begin pong kuaishou...")
        ping_flag = False
        try:
            post_data = {
                "operationName": "visionProfileUserList",
                "variables": {
                    "ftype": 1,
                },
                "query": self.graphql.get("vision_profile_user_list"),
            }
            res = await self.post("", post_data)
            if res.get("visionProfileUserList", {}).get("result") == 1:
                ping_flag = True
        except Exception as e:
            utils.logger.error(
                f"[KuaiShouClient.pong] Pong kuaishou failed: {e}, and try to login again..."
            )
            ping_flag = False
        return ping_flag

    async def update_cookies(self, browser_context: BrowserContext):
        cookie_str, cookie_dict = utils.convert_cookies(await browser_context.cookies())
        self.headers["Cookie"] = cookie_str
        self.cookie_dict = cookie_dict

    async def search_info_by_keyword(
        self, keyword: str, pcursor: str, search_session_id: str = ""
    ):
        """
        KuaiShou web search api
        :param keyword: search keyword
        :param pcursor: limite page curson
        :param search_session_id: search session id
        :return:
        """
        post_data = {
            "operationName": "visionSearchPhoto",
            "variables": {
                "keyword": keyword,
                "pcursor": pcursor,
                "page": "search",
                "searchSessionId": search_session_id,
            },
            "query": self.graphql.get("search_query"),
        }
        return await self.post("", post_data)

    async def get_video_info(self, photo_id: str) -> Dict:
        """
        Kuaishou web video detail api
        :param photo_id:
        :return:
        """
        post_data = {
            "operationName": "visionVideoDetail",
            "variables": {"photoId": photo_id, "page": "search"},
            "query": self.graphql.get("video_detail"),
        }
        return await self.post("", post_data)

    async def get_video_comments_from_dom(self, photo_id: str) -> List[Dict]:
        """从视频页 DOM 提取评论（GraphQL commentListQuery 已废弃）

        导航到视频页面，等待评论区渲染，通过 CSS 选择器提取评论数据。
        返回格式与原 GraphQL 接口兼容。
        """
        video_url = f"https://www.kuaishou.com/short-video/{photo_id}"
        try:
            utils.logger.info(
                f"[KuaiShouClient.get_video_comments_from_dom] 导航到 {video_url}"
            )
            await self.playwright_page.goto(
                video_url, wait_until="domcontentloaded", timeout=15000
            )
            await asyncio.sleep(4)  # 等待评论区 SSR 渲染完成

            # 滚动到评论区以确保渲染
            await self.playwright_page.evaluate(
                "window.scrollTo(0, document.body.scrollHeight / 3)"
            )
            await asyncio.sleep(1)

            raw_comments = await self.playwright_page.evaluate(EXTRACT_COMMENTS_JS)

            # 转换为与 GraphQL 接口兼容的格式
            comments = []
            for raw in raw_comments:
                # 生成稳定的 commentId: 基于 video_id + author + content 的 hash
                id_src = f"{photo_id}_{raw['author']}_{raw['content']}_{raw['index']}"
                comment_id = hashlib.md5(id_src.encode()).hexdigest()[:16]

                comments.append({
                    "commentId": comment_id,
                    "authorId": raw.get("authorId", ""),
                    "authorName": raw.get("author", ""),
                    "content": raw.get("content", ""),
                    "headurl": raw.get("avatar", ""),
                    "timestamp": int(time.time()),  # DOM 中只有相对时间，用当前时间戳
                    "likedCount": 0,
                    "realLikedCount": 0,
                    "liked": False,
                    "status": 1,
                    "authorLiked": False,
                    "subCommentCount": 0,
                    "subCommentsPcursor": "no_more",
                    "subComments": [],
                })

            utils.logger.info(
                f"[KuaiShouClient.get_video_comments_from_dom] "
                f"photo_id={photo_id} 从 DOM 提取到 {len(comments)} 条评论"
            )
            return comments

        except Exception as e:
            utils.logger.error(
                f"[KuaiShouClient.get_video_comments_from_dom] "
                f"photo_id={photo_id} 提取失败: {e}"
            )
            return []

    async def get_video_all_comments(
        self,
        photo_id: str,
        crawl_interval: float = 1.0,
        callback: Optional[Callable] = None,
        max_count: int = 10,
    ):
        """
        获取视频所有评论（通过 DOM 提取，GraphQL 接口已废弃）
        :param photo_id:
        :param crawl_interval:
        :param callback:
        :param max_count:
        :return:
        """
        comments = await self.get_video_comments_from_dom(photo_id)
        if not comments:
            return []

        # 限制数量
        if len(comments) > max_count:
            comments = comments[:max_count]

        if callback:
            await callback(photo_id, comments)

        return comments

    async def get_creator_profile(self, userId: str) -> Dict:
        post_data = {
            "operationName": "visionProfile",
            "variables": {"userId": userId},
            "query": self.graphql.get("vision_profile"),
        }
        return await self.post("", post_data)

    async def get_video_by_creater(self, userId: str, pcursor: str = "") -> Dict:
        post_data = {
            "operationName": "visionProfilePhotoList",
            "variables": {"page": "profile", "pcursor": pcursor, "userId": userId},
            "query": self.graphql.get("vision_profile_photo_list"),
        }
        return await self.post("", post_data)

    async def get_creator_info(self, user_id: str) -> Dict:
        """
        eg: https://www.kuaishou.com/profile/3x4jtnbfter525a
        快手用户主页
        """

        visionProfile = await self.get_creator_profile(user_id)
        return visionProfile.get("userProfile")

    async def get_all_videos_by_creator(
        self,
        user_id: str,
        crawl_interval: float = 1.0,
        callback: Optional[Callable] = None,
    ) -> List[Dict]:
        """
        获取指定用户下的所有发过的帖子，该方法会一直查找一个用户下的所有帖子信息
        Args:
            user_id: 用户ID
            crawl_interval: 爬取一次的延迟单位（秒）
            callback: 一次分页爬取结束后的更新回调函数
        Returns:

        """
        result = []
        pcursor = ""

        while pcursor != "no_more":
            videos_res = await self.get_video_by_creater(user_id, pcursor)
            if not videos_res:
                utils.logger.error(
                    f"[KuaiShouClient.get_all_videos_by_creator] The current creator may have been banned by ks, so they cannot access the data."
                )
                break

            vision_profile_photo_list = videos_res.get("visionProfilePhotoList", {})
            pcursor = vision_profile_photo_list.get("pcursor", "")

            videos = vision_profile_photo_list.get("feeds", [])
            utils.logger.info(
                f"[KuaiShouClient.get_all_videos_by_creator] got user_id:{user_id} videos len : {len(videos)}"
            )

            if callback:
                await callback(videos)
            await asyncio.sleep(crawl_interval)
            result.extend(videos)
        return result
