# -*- coding: utf-8 -*-
"""查看抖音爬取结果"""

import pymysql

MYSQL_HOST = "10.168.1.80"
MYSQL_PORT = 3306
MYSQL_USER = "root"
MYSQL_PASS = "Tangwei7311Yeti."
MYSQL_DB = "fish"

conn = pymysql.connect(
    host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER,
    password=MYSQL_PASS, database=MYSQL_DB, charset="utf8mb4",
)
cursor = conn.cursor()

# 抖音视频
cursor.execute(
    "SELECT COUNT(*) FROM douyin_aweme"
)
total_videos = cursor.fetchone()[0]

cursor.execute(
    "SELECT aweme_id, title, liked_count, comment_count, share_count, source_keyword "
    "FROM douyin_aweme ORDER BY add_ts DESC LIMIT 15"
)
videos = cursor.fetchall()

print(f"=== 抖音视频 (总计 {total_videos}) ===")
print(f"{'aweme_id':<22} {'likes':>8} {'comments':>8} {'shares':>8}  keyword / title")
print("-" * 100)
for vid, title, likes, comments, shares, kw in videos:
    t = (title or "")[:50]
    print(f"{vid:<22} {str(likes):>8} {str(comments):>8} {str(shares):>8}  [{kw}] {t}")

# 抖音评论
cursor.execute("SELECT COUNT(*) FROM douyin_aweme_comment")
total_comments = cursor.fetchone()[0]

cursor.execute(
    "SELECT c.comment_id, c.aweme_id, c.nickname, c.ip_location, c.content, c.sub_comment_count "
    "FROM douyin_aweme_comment c "
    "ORDER BY c.add_ts DESC LIMIT 20"
)
comments = cursor.fetchall()

print(f"\n=== 抖音评论 (总计 {total_comments}) ===")
for cid, aid, nick, ip, content, sub in comments:
    c = (content or "")[:60]
    print(f"  [{aid}] {nick} ({ip}) sub={sub}: {c}")

conn.close()
