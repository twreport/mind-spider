# -*- coding: utf-8 -*-
"""恢复被误标过期的 cookie + 插入测试任务"""

import time
from pymongo import MongoClient

client = MongoClient("mongodb://10.168.1.80:27018")
db = client["mindspider_signal"]

# 恢复被误标为 expired 的 bili cookie
result = db.platform_cookies.update_one(
    {"platform": "bili", "status": "expired"},
    {"$set": {"status": "active"}, "$unset": {"expired_at": ""}},
)
if result.modified_count:
    print("bili cookie 已恢复为 active")
else:
    print("bili cookie 状态未变（可能已是 active）")

# 插入新测试任务
db.crawl_tasks.insert_one({
    "task_id": "ct_test_shorttrack_bili_" + str(int(time.time())),
    "candidate_id": "cand_test_shorttrack",
    "topic_title": "中国短道速滑历史最差",
    "search_keywords": ["中国短道速滑历史最差", "短道速滑"],
    "platform": "bili",
    "max_notes": 5,
    "priority": 1,
    "status": "pending",
    "created_at": int(time.time()),
    "attempts": 0,
})

print("测试任务已插入")
client.close()
