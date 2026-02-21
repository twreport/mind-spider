# -*- coding: utf-8 -*-
"""恢复 bili cookie 状态"""
from pymongo import MongoClient

client = MongoClient("mongodb://10.168.1.80:27018")
db = client["mindspider_signal"]

result = db.platform_cookies.update_one(
    {"platform": "bili"},
    {"$set": {"status": "active"}, "$unset": {"expired_at": ""}},
)
print(f"modified: {result.modified_count}")
client.close()
