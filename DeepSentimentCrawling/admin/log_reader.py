# -*- coding: utf-8 -*-
"""
日志文件解析模块

读取 logs/deep_crawl_*.log 目录下的深层采集日志，解析 ERROR 级别日志行。
"""

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

# 日志目录（项目根 / logs）
_LOG_DIR = Path(__file__).parent.parent.parent / "logs"

# 日志行格式: {time:YYYY-MM-DD HH:mm:ss} | {level:<7} | {message}
_LOG_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*\|\s*(\w+)\s*\|\s*(.+)$")

# 从消息中提取平台标识
_PLATFORM_PATTERN = re.compile(r"\[(?:Dispatcher|Worker|LoginConsole|DeepCrawl)\]\s*(\w+)")


def get_error_logs(
    platform: Optional[str] = None,
    limit: int = 50,
) -> List[Dict]:
    """
    读取今天和昨天的深层采集日志，解析 ERROR 级别日志。

    Args:
        platform: 按平台过滤（模糊匹配）
        limit: 返回最大条数

    Returns:
        [{time, level, message, platform_hint}, ...] 最新在前
    """
    today = datetime.now().date()
    yesterday = today - timedelta(days=1)

    errors = []
    for day in [yesterday, today]:
        log_file = _LOG_DIR / f"deep_crawl_{day.isoformat()}.log"
        if not log_file.exists():
            continue

        try:
            with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.rstrip()
                    m = _LOG_PATTERN.match(line)
                    if not m:
                        continue

                    time_str, level, message = (
                        m.group(1),
                        m.group(2).strip(),
                        m.group(3),
                    )

                    if level != "ERROR":
                        continue

                    platform_hint = _extract_platform(message)

                    if platform and platform.lower() not in message.lower():
                        continue

                    errors.append(
                        {
                            "time": time_str,
                            "level": level,
                            "message": message,
                            "platform_hint": platform_hint,
                        }
                    )
        except Exception as e:
            logger.warning(f"[DeepDashboard] 读取日志文件失败 {log_file}: {e}")

    # 最新在前
    errors.reverse()
    return errors[:limit]


def _extract_platform(message: str) -> Optional[str]:
    """从日志消息中提取平台标识"""
    m = _PLATFORM_PATTERN.search(message)
    if m:
        plat = m.group(1).lower()
        valid = {"xhs", "dy", "bili", "wb", "ks", "zhihu"}
        if plat in valid:
            return plat
    return None
