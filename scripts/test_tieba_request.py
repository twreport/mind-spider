# -*- coding: utf-8 -*-
"""单独测试贴吧搜索请求，排查服务器网络/TLS问题"""

import requests
from urllib.parse import urlencode
from pymongo import MongoClient

# 拿 cookie
client = MongoClient("mongodb://10.168.1.80:27018")
db = client["mindspider_signal"]
doc = db.platform_cookies.find_one({"platform": "tieba", "status": "active"})
cookies = doc["cookies"]
cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
client.close()

keyword = "短道速滑"
params = {"ie": "utf-8", "qw": keyword, "rn": 10, "pn": 1, "sm": 1, "only_thread": 0}
url = f"https://tieba.baidu.com/f/search/res?{urlencode(params)}"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Cookie": cookie_str,
    "Referer": "https://tieba.baidu.com/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

print(f"URL: {url}")
print(f"Cookie 长度: {len(cookie_str)}")
print(f"BDUSS: {'YES' if 'BDUSS' in cookies else 'NO'}")
print(f"STOKEN: {'YES' if 'STOKEN' in cookies else 'NO'}")
print("正在请求...")

try:
    resp = requests.get(url, headers=headers, timeout=30, allow_redirects=True)
    print(f"Status: {resp.status_code}")
    print(f"Content-Length: {len(resp.text)}")

    if "百度安全验证" in resp.text[:500]:
        print(">>> 触发了百度安全验证!")
    elif "s_post" in resp.text:
        count = resp.text.count("s_post")
        print(f">>> 成功! 找到 {count} 个 s_post 匹配")
    else:
        print(">>> 未知页面内容:")
        print(resp.text[:500])
except Exception as e:
    print(f">>> 请求失败: {type(e).__name__}: {e}")
