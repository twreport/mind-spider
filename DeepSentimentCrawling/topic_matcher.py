# -*- coding: utf-8 -*-
"""
TopicMatcher — 话题匹配 + 关键词扩展

匹配流程（三分类）：
1. 精确去重：24h 内相同 topic_title 的用户任务 → duplicate
2. 候选匹配：jieba 预筛 + LLM 语义判断
   - duplicate: 同一事件同一角度，返回已有数据
   - development: 同一事件新进展/新角度，需要爬取但关联已有 candidate
   - different: 无关事件，正常新建

关键词扩展（独立调用）：
- 仅当用户未传 search_keywords 时触发
- LLM 生成最多 2 个补充关键词
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

# 匹配结果类型
MATCH_DUPLICATE = "duplicate"       # 同一事件同一角度，无需爬取
MATCH_DEVELOPMENT = "development"   # 同一事件新进展，需要爬取
MATCH_DIFFERENT = "different"       # 无关事件


def _extract_keywords(title: str) -> set[str]:
    """用 jieba 从标题提取关键词，过滤停用词和单字"""
    if not title:
        return set()
    words = jieba.cut(title)
    return {w for w in words if len(w) >= 2 and w not in _STOPWORDS}


def _parse_llm_json(text: str) -> dict:
    """从 LLM 输出中提取 JSON，容忍 markdown code block"""
    text = text.strip()
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    return json.loads(text)


class TopicMatcher:
    """话题匹配 + 关键词扩展（两个独立 LLM 调用）"""

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

    # ── 话题匹配（三分类）──────────────────────────────────────

    def match(self, topic_title: str) -> Optional[dict]:
        """
        主入口：精确去重 → jieba 预筛 → LLM 三分类 → jieba fallback

        Returns:
            匹配结果 dict，包含 match_type 字段：
            - duplicate: 完全重复，无需爬取
            - development: 事件进展，需爬取但关联已有 candidate
            未命中返回 None（等价于 different）
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

        # 4) LLM 三分类
        if self._llm_available:
            llm_result = self._llm_match(topic_title, shortlist)
            if llm_result:
                return llm_result

        # 5) LLM 不可用或未命中，降级 jieba（overlap >= 0.6 → duplicate）
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
                    "match_type": MATCH_DUPLICATE,
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
        """
        查已爬取话题，两个来源合并去重：
        1. candidates 集合（表层采集自动发现，status ∈ exploded/tracking/closed）
        2. crawl_tasks 集合（用户发起的已完成/进行中任务，按 topic_title 去重）
        """
        results = []
        seen_titles: set[str] = set()

        try:
            self.mongo.connect()

            # 来源 1: candidates 集合
            cand_col = self.mongo.get_collection("candidates")
            cand_docs = list(
                cand_col.find(
                    {"status": {"$in": ["exploded", "tracking", "closed"]}},
                    {"candidate_id": 1, "canonical_title": 1, "source_titles": 1, "status": 1, "_id": 0},
                )
                .sort([("updated_at", -1)])
                .limit(100)
            )
            for doc in cand_docs:
                results.append(doc)
                seen_titles.add(doc.get("canonical_title", ""))

            # 来源 2: crawl_tasks 中用户发起的已完成/进行中任务
            task_col = self.mongo.get_collection("crawl_tasks")
            pipeline = [
                {"$match": {
                    "_source": "user",
                    "status": {"$in": ["completed", "running"]},
                }},
                {"$sort": {"created_at": -1}},
                {"$group": {
                    "_id": "$topic_title",
                    "candidate_id": {"$first": "$candidate_id"},
                    "status": {"$first": "$status"},
                    "search_keywords": {"$first": "$search_keywords"},
                }},
                {"$limit": 50},
            ]
            for doc in task_col.aggregate(pipeline):
                title = doc["_id"]
                if title and title not in seen_titles:
                    results.append({
                        "candidate_id": doc.get("candidate_id", "user_api"),
                        "canonical_title": title,
                        "source_titles": [title],
                        "status": doc.get("status", "completed"),
                    })
                    seen_titles.add(title)

        except Exception as e:
            logger.warning(f"[TopicMatcher] 候选查询失败: {e}")

        return results

    def _jieba_prefilter(
        self, topic_title: str, candidates: list[dict]
    ) -> list[tuple[dict, float]]:
        """jieba 关键词重叠率预筛，overlap >= 0.3，返回 top 10"""
        user_kw = _extract_keywords(topic_title)
        if not user_kw:
            return []

        scored = []
        for cand in candidates:
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
        """LLM 三分类：duplicate / development / different"""
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
            "判断用户话题与候选的关系，三选一：\n"
            '- "duplicate"：同一事件、同一角度，信息完全重复\n'
            '- "development"：同一事件，但有新进展、新角度或新信息\n'
            '- "different"：不同事件，无关联\n\n'
            "输出 JSON（不要输出其他内容）：\n"
            '{"type": "duplicate|development|different", '
            '"matched_id": "candidate_id 或 null", '
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
            result = _parse_llm_json(resp.choices[0].message.content)
            match_type = result.get("type", "different")
            matched_id = result.get("matched_id")

            if match_type == MATCH_DIFFERENT or not matched_id:
                return None

            # 查找匹配的候选
            matched_cand = None
            for c, _ in shortlist:
                if c.get("candidate_id") == matched_id:
                    matched_cand = c
                    break

            if not matched_cand:
                return None

            logger.info(
                f"[TopicMatcher] LLM 匹配: {topic_title} -> "
                f"{matched_cand.get('canonical_title')} "
                f"(type={match_type}, confidence={result.get('confidence')})"
            )
            return {
                "match_type": match_type,
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
        """LLM 不可用时降级到 jieba（overlap >= 0.6 → duplicate）"""
        for cand, overlap in shortlist:
            if overlap >= 0.6:
                logger.info(
                    f"[TopicMatcher] jieba 降级匹配: {topic_title} -> "
                    f"{cand.get('canonical_title')} (overlap={overlap:.2f})"
                )
                cand_id = cand.get("candidate_id", "")
                return {
                    "match_type": MATCH_DUPLICATE,
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

    # ── 关键词扩展（独立调用）─────────────────────────────────

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
            result = _parse_llm_json(resp.choices[0].message.content)
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
