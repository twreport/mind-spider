# -*- coding: utf-8 -*-
"""
TopicMatcher — 话题匹配 + 关键词扩展

流程：
1. 精确去重：24h 内相同 topic_title 的用户任务
2. 候选匹配：jieba 预筛 + LLM 语义判断
3. 关键词扩展：LLM 生成补充搜索关键词
"""

import json
import time
from typing import Optional

import jieba
from loguru import logger
from openai import OpenAI

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from ms_config import settings
from BroadTopicExtraction.pipeline.mongo_writer import MongoWriter

# 复用 signal_detector 的停用词
_STOPWORDS = frozenset(
    "的 了 在 是 我 有 和 就 不 人 都 一 一个 上 也 很 到 说 要 去 你 会 着 没有 看 好 "
    "自己 这 他 她 它 们 那 被 从 把 让 用 为 什么 怎么 如何 如何看待 哪些 为什么 "
    "怎样 可以 这个 那个 还是 或者 以及 但是 然而 因为 所以 如果 虽然 已经 正在 "
    "关于 对于 通过 进行 开始 之后 之前 以来 目前 今天 昨天 明天 最新 最近 突发 "
    "热搜 曝光 回应 官方 发布 公布 通报".split()
)


def _extract_keywords(title: str) -> set[str]:
    """用 jieba 从标题提取关键词，过滤停用词和单字"""
    if not title:
        return set()
    words = jieba.cut(title)
    return {w for w in words if len(w) >= 2 and w not in _STOPWORDS}


class TopicMatcher:
    """话题匹配 + 关键词扩展"""

    def __init__(self, mongo: MongoWriter):
        self.mongo = mongo

        # LLM 客户端（fallback 到主配置）
        api_key = settings.TOPIC_MATCHER_API_KEY or settings.MINDSPIDER_API_KEY
        base_url = settings.TOPIC_MATCHER_BASE_URL or settings.MINDSPIDER_BASE_URL
        self.model = settings.TOPIC_MATCHER_MODEL_NAME or "qwen-flash"

        self._llm_available = bool(api_key and base_url)
        if self._llm_available:
            self.client = OpenAI(api_key=api_key, base_url=base_url)
            logger.info(f"[TopicMatcher] LLM 已初始化: model={self.model}, base_url={base_url}")
        else:
            self.client = None
            logger.warning("[TopicMatcher] LLM 未配置，将仅使用 jieba 降级匹配")

    # ── 话题匹配 ──────────────────────────────────────────────

    def match(self, topic_title: str) -> Optional[dict]:
        """
        主入口：精确去重 → jieba 预筛 → LLM 语义匹配 → jieba fallback

        Returns:
            匹配结果 dict（含 candidate 信息），未命中返回 None
        """
        # 1) 24h 精确去重
        exact = self._check_recent_user_tasks(topic_title)
        if exact:
            return exact

        # 2) 从 MongoDB 拉候选
        candidates = self._fetch_deep_crawled_candidates()
        if not candidates:
            return None

        # 3) jieba 预筛
        shortlist = self._jieba_prefilter(topic_title, candidates)
        if not shortlist:
            return None

        # 4) LLM 语义匹配
        if self._llm_available:
            llm_result = self._llm_match(topic_title, shortlist)
            if llm_result:
                return llm_result

        # 5) LLM 不可用或未命中，降级 jieba（overlap >= 0.6）
        return self._jieba_fallback(topic_title, shortlist)

    def _check_recent_user_tasks(self, topic_title: str) -> Optional[dict]:
        """24h 内精确去重：相同 topic_title 的用户任务"""
        try:
            self.mongo.connect()
            col = self.mongo.get_collection("crawl_tasks")
            cutoff = int(time.time()) - 86400
            doc = col.find_one(
                {
                    "topic_title": topic_title,
                    "_source": "user",
                    "created_at": {"$gte": cutoff},
                },
                sort=[("created_at", -1)],
            )
            if doc:
                task_id = doc.get("task_id", "")
                logger.info(f"[TopicMatcher] 精确去重命中: {topic_title} -> {task_id}")
                return {
                    "candidate_id": doc.get("candidate_id", "user_api"),
                    "canonical_title": topic_title,
                    "status": doc.get("status", "pending"),
                    "source_titles": [topic_title],
                    "crawl_stats": self._get_crawl_stats_by_title(topic_title),
                    "match_method": "exact",
                    "confidence": 1.0,
                    "reason": f"24h 内已有相同话题的用户任务 ({task_id})",
                }
        except Exception as e:
            logger.warning(f"[TopicMatcher] 精确去重查询失败: {e}")
        return None

    def _fetch_deep_crawled_candidates(self) -> list[dict]:
        """查 MongoDB candidates（已爬状态，最近 100 条）"""
        try:
            self.mongo.connect()
            col = self.mongo.get_collection("candidates")
            docs = list(
                col.find(
                    {"status": {"$in": ["exploded", "tracking", "closed"]}},
                    {"candidate_id": 1, "canonical_title": 1, "source_titles": 1, "status": 1, "_id": 0},
                )
                .sort([("updated_at", -1)])
                .limit(100)
            )
            return docs
        except Exception as e:
            logger.warning(f"[TopicMatcher] 候选查询失败: {e}")
            return []

    def _jieba_prefilter(
        self, topic_title: str, candidates: list[dict]
    ) -> list[tuple[dict, float]]:
        """
        jieba 关键词重叠率预筛，overlap >= 0.3，返回 top 10
        """
        user_kw = _extract_keywords(topic_title)
        if not user_kw:
            return []

        scored = []
        for cand in candidates:
            # 合并 canonical_title + source_titles 的关键词
            titles = [cand.get("canonical_title", "")]
            titles.extend(cand.get("source_titles", []))
            cand_kw: set[str] = set()
            for t in titles:
                cand_kw |= _extract_keywords(t)

            if not cand_kw:
                continue

            overlap = len(user_kw & cand_kw) / len(user_kw | cand_kw)
            if overlap >= 0.3:
                scored.append((cand, overlap))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:10]

    def _llm_match(
        self, topic_title: str, shortlist: list[tuple[dict, float]]
    ) -> Optional[dict]:
        """LLM 语义判断"""
        candidates_text = json.dumps(
            [
                {
                    "id": c.get("candidate_id"),
                    "canonical_title": c.get("canonical_title"),
                    "source_titles": c.get("source_titles", [])[:5],
                }
                for c, _ in shortlist
            ],
            ensure_ascii=False,
        )

        prompt = (
            f'用户话题："{topic_title}"\n'
            f"已有候选话题：{candidates_text}\n\n"
            "任务：判断用户话题是否与某个候选指向同一事件。\n"
            "输出 JSON（不要输出其他内容）：\n"
            '{"match": bool, "matched_id": "candidate_id 或 null", '
            '"confidence": 0.0-1.0, "reason": "简短原因"}'
        )

        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你是话题匹配专家。只输出 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=200,
            )
            text = resp.choices[0].message.content.strip()
            # 提取 JSON（容忍 markdown code block）
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()
            result = json.loads(text)

            if result.get("match") and result.get("matched_id"):
                matched_id = result["matched_id"]
                # 查找匹配的候选
                matched_cand = None
                for c, _ in shortlist:
                    if c.get("candidate_id") == matched_id:
                        matched_cand = c
                        break

                if matched_cand:
                    logger.info(
                        f"[TopicMatcher] LLM 匹配命中: {topic_title} -> "
                        f"{matched_cand.get('canonical_title')} (confidence={result.get('confidence')})"
                    )
                    return {
                        "candidate_id": matched_id,
                        "canonical_title": matched_cand.get("canonical_title", ""),
                        "status": matched_cand.get("status", ""),
                        "source_titles": matched_cand.get("source_titles", []),
                        "crawl_stats": self._get_crawl_stats(matched_id),
                        "match_method": "llm",
                        "confidence": result.get("confidence", 0.0),
                        "reason": result.get("reason", ""),
                    }
        except Exception as e:
            logger.warning(f"[TopicMatcher] LLM 匹配调用失败: {e}")
        return None

    def _jieba_fallback(
        self, topic_title: str, shortlist: list[tuple[dict, float]]
    ) -> Optional[dict]:
        """LLM 不可用时降级到 jieba（overlap >= 0.6）"""
        for cand, overlap in shortlist:
            if overlap >= 0.6:
                logger.info(
                    f"[TopicMatcher] jieba 降级匹配: {topic_title} -> "
                    f"{cand.get('canonical_title')} (overlap={overlap:.2f})"
                )
                cand_id = cand.get("candidate_id", "")
                return {
                    "candidate_id": cand_id,
                    "canonical_title": cand.get("canonical_title", ""),
                    "status": cand.get("status", ""),
                    "source_titles": cand.get("source_titles", []),
                    "crawl_stats": self._get_crawl_stats(cand_id),
                    "match_method": "jieba",
                    "confidence": round(overlap, 2),
                    "reason": f"jieba 关键词重叠率 {overlap:.0%}",
                }
        return None

    # ── 关键词扩展 ─────────────────────────────────────────────

    def expand_keywords(self, topic_title: str) -> list[str]:
        """
        LLM 生成最多 2 个补充关键词，与 topic_title 组成最多 3 个 search_keywords。
        LLM 不可用时返回 [topic_title]。
        """
        base = [topic_title]

        if not self._llm_available:
            return base

        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你是社交媒体搜索关键词专家。只输出 JSON。"},
                    {
                        "role": "user",
                        "content": (
                            f'话题："{topic_title}"\n'
                            "请为该话题生成最多 2 个最适合在社交媒体平台搜索的补充关键词。\n"
                            "要求：与原标题互补，覆盖不同表述角度，不要重复原标题。\n"
                            '输出 JSON（不要输出其他内容）：{"extra_keywords": ["关键词1", "关键词2"]}'
                        ),
                    },
                ],
                temperature=0.1,
                max_tokens=200,
            )
            text = resp.choices[0].message.content.strip()
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()
            result = json.loads(text)
            extra = result.get("extra_keywords", [])
            if isinstance(extra, list):
                for kw in extra[:2]:
                    kw = str(kw).strip()
                    if kw and kw != topic_title:
                        base.append(kw)
            logger.info(f"[TopicMatcher] 关键词扩展: {topic_title} -> {base}")
        except Exception as e:
            logger.warning(f"[TopicMatcher] 关键词扩展失败，仅使用标题: {e}")

        return base

    def match_and_expand(
        self, topic_title: str, shortlist: list[tuple[dict, float]]
    ) -> tuple[Optional[dict], list[str]]:
        """
        合并一次 LLM 调用完成匹配 + 关键词扩展（有候选时）。
        Returns: (match_result, expanded_keywords)
        """
        if not self._llm_available:
            match_result = self._jieba_fallback(topic_title, shortlist)
            return match_result, [topic_title]

        candidates_text = json.dumps(
            [
                {
                    "id": c.get("candidate_id"),
                    "canonical_title": c.get("canonical_title"),
                    "source_titles": c.get("source_titles", [])[:5],
                }
                for c, _ in shortlist
            ],
            ensure_ascii=False,
        )

        prompt = (
            f'用户话题："{topic_title}"\n'
            f"已有候选话题：{candidates_text}\n\n"
            "任务1：判断用户话题是否与某个候选指向同一事件。\n"
            "任务2：如未匹配，为该话题生成最多 2 个适合社交媒体搜索的补充关键词。\n\n"
            "输出 JSON（不要输出其他内容）：\n"
            '{"match": bool, "matched_id": "candidate_id 或 null", '
            '"confidence": 0.0-1.0, "reason": "简短原因", '
            '"extra_keywords": ["补充关键词1", "补充关键词2"] 或 []}'
        )

        match_result = None
        keywords = [topic_title]

        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "你是话题匹配与关键词专家。只输出 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=200,
            )
            text = resp.choices[0].message.content.strip()
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()
            result = json.loads(text)

            # 处理匹配
            if result.get("match") and result.get("matched_id"):
                matched_id = result["matched_id"]
                matched_cand = None
                for c, _ in shortlist:
                    if c.get("candidate_id") == matched_id:
                        matched_cand = c
                        break
                if matched_cand:
                    logger.info(
                        f"[TopicMatcher] LLM 合并匹配命中: {topic_title} -> "
                        f"{matched_cand.get('canonical_title')} (confidence={result.get('confidence')})"
                    )
                    match_result = {
                        "candidate_id": matched_id,
                        "canonical_title": matched_cand.get("canonical_title", ""),
                        "status": matched_cand.get("status", ""),
                        "source_titles": matched_cand.get("source_titles", []),
                        "crawl_stats": self._get_crawl_stats(matched_id),
                        "match_method": "llm",
                        "confidence": result.get("confidence", 0.0),
                        "reason": result.get("reason", ""),
                    }

            # 处理关键词（无论是否匹配都提取）
            extra = result.get("extra_keywords", [])
            if isinstance(extra, list):
                for kw in extra[:2]:
                    kw = str(kw).strip()
                    if kw and kw != topic_title:
                        keywords.append(kw)

            logger.info(f"[TopicMatcher] 合并调用完成: match={match_result is not None}, keywords={keywords}")

        except Exception as e:
            logger.warning(f"[TopicMatcher] LLM 合并调用失败: {e}")
            # LLM 失败降级
            match_result = self._jieba_fallback(topic_title, shortlist)

        return match_result, keywords

    # ── 辅助方法 ────────────────────────────────────────────────

    def _get_crawl_stats(self, candidate_id: str) -> dict:
        """查 crawl_tasks 获取爬取统计"""
        try:
            self.mongo.connect()
            col = self.mongo.get_collection("crawl_tasks")
            pipeline = [
                {"$match": {"candidate_id": candidate_id}},
                {
                    "$group": {
                        "_id": None,
                        "total_tasks": {"$sum": 1},
                        "completed": {
                            "$sum": {"$cond": [{"$eq": ["$status", "completed"]}, 1, 0]}
                        },
                        "platforms": {"$addToSet": "$platform"},
                    }
                },
            ]
            results = list(col.aggregate(pipeline))
            if results:
                r = results[0]
                return {
                    "total_tasks": r.get("total_tasks", 0),
                    "completed": r.get("completed", 0),
                    "platforms": r.get("platforms", []),
                }
        except Exception as e:
            logger.warning(f"[TopicMatcher] 爬取统计查询失败: {e}")
        return {"total_tasks": 0, "completed": 0, "platforms": []}

    def _get_crawl_stats_by_title(self, topic_title: str) -> dict:
        """按 topic_title 查 crawl_tasks 获取爬取统计"""
        try:
            self.mongo.connect()
            col = self.mongo.get_collection("crawl_tasks")
            pipeline = [
                {"$match": {"topic_title": topic_title, "_source": "user"}},
                {
                    "$group": {
                        "_id": None,
                        "total_tasks": {"$sum": 1},
                        "completed": {
                            "$sum": {"$cond": [{"$eq": ["$status", "completed"]}, 1, 0]}
                        },
                        "platforms": {"$addToSet": "$platform"},
                    }
                },
            ]
            results = list(col.aggregate(pipeline))
            if results:
                r = results[0]
                return {
                    "total_tasks": r.get("total_tasks", 0),
                    "completed": r.get("completed", 0),
                    "platforms": r.get("platforms", []),
                }
        except Exception as e:
            logger.warning(f"[TopicMatcher] 爬取统计查询失败: {e}")
        return {"total_tasks": 0, "completed": 0, "platforms": []}
