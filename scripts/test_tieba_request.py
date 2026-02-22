# -*- coding: utf-8 -*-
"""单独测试贴吧搜索请求，排查服务器网络/TLS问题

多种方式对比:
  1. requests (Python urllib3/OpenSSL)
  2. curl 子进程 (libcurl/OpenSSL)
  3. 不带 cookie 的裸请求
  4. 访问贴吧首页(非搜索)
"""

import subprocess
import time
import requests
from urllib.parse import urlencode
from pymongo import MongoClient


def get_cookie_str():
    client = MongoClient("mongodb://10.168.1.80:27018")
    db = client["mindspider_signal"]
    doc = db.platform_cookies.find_one({"platform": "tieba", "status": "active"})
    cookies = doc["cookies"]
    cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
    client.close()
    print(f"Cookie 长度: {len(cookie_str)}")
    print(f"BDUSS: {'YES' if 'BDUSS' in cookies else 'NO'}")
    print(f"STOKEN: {'YES' if 'STOKEN' in cookies else 'NO'}")
    return cookie_str


def check_response(label, resp_text):
    if "百度安全验证" in resp_text[:500]:
        print(f"  [{label}] 触发百度安全验证")
    elif "s_post" in resp_text:
        count = resp_text.count("s_post")
        print(f"  [{label}] 成功! 找到 {count} 个 s_post")
    elif "<title>" in resp_text[:500]:
        import re
        m = re.search(r"<title>(.*?)</title>", resp_text[:500])
        title = m.group(1) if m else "unknown"
        print(f"  [{label}] 页面 title: {title}")
    else:
        print(f"  [{label}] 未知内容: {resp_text[:200]}")


def test_requests(url, headers, label="requests"):
    print(f"\n--- 测试 {label} ---")
    print(f"  URL: {url}")
    t0 = time.time()
    try:
        resp = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
        elapsed = time.time() - t0
        print(f"  Status: {resp.status_code}, 长度: {len(resp.text)}, 耗时: {elapsed:.1f}s")
        check_response(label, resp.text)
    except Exception as e:
        elapsed = time.time() - t0
        print(f"  失败 ({elapsed:.1f}s): {type(e).__name__}: {e}")


def test_curl(url, cookie_str, label="curl"):
    print(f"\n--- 测试 {label} ---")
    cmd = [
        "curl", "-sS", "-L",
        "--max-time", "15",
        "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "-H", f"Cookie: {cookie_str}",
        "-H", "Referer: https://tieba.baidu.com/",
        "-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "-H", "Accept-Language: zh-CN,zh;q=0.9,en;q=0.8",
        "-o", "/dev/null",
        "-w", "status=%{http_code} size=%{size_download} time=%{time_total}s",
        url,
    ]
    t0 = time.time()
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        elapsed = time.time() - t0
        print(f"  {result.stdout.strip()} (wall={elapsed:.1f}s)")
        if result.returncode != 0:
            print(f"  stderr: {result.stderr.strip()}")
    except Exception as e:
        elapsed = time.time() - t0
        print(f"  失败 ({elapsed:.1f}s): {type(e).__name__}: {e}")

    # 再跑一次拿内容
    print(f"  获取内容...")
    cmd2 = [
        "curl", "-sS", "-L",
        "--max-time", "15",
        "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "-H", f"Cookie: {cookie_str}",
        "-H", "Referer: https://tieba.baidu.com/",
        url,
    ]
    try:
        result2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=20)
        if result2.returncode == 0 and result2.stdout:
            check_response(label, result2.stdout)
        elif result2.stderr:
            print(f"  stderr: {result2.stderr.strip()}")
    except Exception as e:
        print(f"  获取内容失败: {type(e).__name__}: {e}")


if __name__ == "__main__":
    cookie_str = get_cookie_str()

    keyword = "短道速滑"
    params = {"ie": "utf-8", "qw": keyword, "rn": 10, "pn": 1, "sm": 1, "only_thread": 0}
    search_url = f"https://tieba.baidu.com/f/search/res?{urlencode(params)}"
    homepage_url = "https://tieba.baidu.com/"

    headers_with_cookie = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Cookie": cookie_str,
        "Referer": "https://tieba.baidu.com/",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    headers_no_cookie = {k: v for k, v in headers_with_cookie.items() if k != "Cookie"}

    # 1) requests + cookie → 搜索
    test_requests(search_url, headers_with_cookie, "requests+cookie 搜索")

    # 2) requests 无 cookie → 搜索
    test_requests(search_url, headers_no_cookie, "requests无cookie 搜索")

    # 3) requests + cookie → 首页
    test_requests(homepage_url, headers_with_cookie, "requests+cookie 首页")

    # 4) requests 无 cookie → 首页
    test_requests(homepage_url, headers_no_cookie, "requests无cookie 首页")

    # 5) curl + cookie → 搜索
    test_curl(search_url, cookie_str, "curl+cookie 搜索")

    # 6) curl 无 cookie → 首页
    test_curl(homepage_url, "", "curl无cookie 首页")
