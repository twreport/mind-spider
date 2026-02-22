# -*- coding: utf-8 -*-
"""插入多平台测试任务到 crawl_tasks"""

import time
from pymongo import MongoClient

client = MongoClient("mongodb://10.168.1.80:27018")
db = client["mindspider_signal"]

# 恢复所有被误标过期的 cookie
result = db.platform_cookies.update_many(
    {"status": "expired"},
    {"$set": {"status": "active"}, "$unset": {"expired_at": ""}},
)
if result.modified_count:
    print(f"已恢复 {result.modified_count} 个 cookie 为 active")

# 测试话题 + 关键词
TOPIC = "中国短道速滑历史最差"
KEYWORDS = ["中国短道速滑历史最差", "短道速滑"]

# 所有平台
# PLATFORMS = ["bili", "xhs", "dy", "ks", "wb", "tieba", "zhihu"]
PLATFORMS = ["ks"]  # 单平台测试

ts = int(time.time())
inserted = 0
for plat in PLATFORMS:
    task_id = f"ct_test_{plat}_{ts}"
    # 跳过已存在的同平台 pending 任务
    if db.crawl_tasks.find_one({"platform": plat, "status": "pending"}):
        print(f"  {plat}: 已有 pending 任务，跳过")
        continue

    db.crawl_tasks.insert_one({
        "task_id": task_id,
        "candidate_id": "cand_test_shorttrack",
        "topic_title": TOPIC,
        "search_keywords": KEYWORDS,
        "platform": plat,
        "max_notes": 5,
        "priority": 1,
        "status": "pending",
        "created_at": ts,
        "attempts": 0,
    })
    print(f"  {plat}: 已插入任务 {task_id}")
    inserted += 1

print(f"\n共插入 {inserted} 个任务")
client.close()
