# -*- coding: utf-8 -*-
"""
日志文件解析模块

读取 BroadTopicExtraction/logs/ 目录下的调度器日志，解析 ERROR 级别日志行。
"""

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

# 日志目录
_LOG_DIR = Path(__file__).parent.parent / "logs"

# 日志行格式: {time:YYYY-MM-DD HH:mm:ss} | {level:<7} | {message}
_LOG_PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*\|\s*(\w+)\s*\|\s*(.+)$"
)


def get_error_logs(
    source: Optional[str] = None,
    limit: int = 50,
) -> List[Dict]:
    """
    读取今天和昨天的日志文件，解析 ERROR 级别日志。

    Args:
        source: 按源名称过滤（模糊匹配）
        limit: 返回最大条数

    Returns:
        [{time, level, message, source_hint}, ...] 最新在前
    """
    today = datetime.now().date()
    yesterday = today - timedelta(days=1)

    errors = []
    for day in [today, yesterday]:
        log_file = _LOG_DIR / f"scheduler_{day.isoformat()}.log"
        if not log_file.exists():
            continue

        try:
            with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.rstrip()
                    m = _LOG_PATTERN.match(line)
                    if not m:
                        continue

                    time_str, level, message = m.group(1), m.group(2).strip(), m.group(3)

                    if level != "ERROR":
                        continue

                    # 尝试提取源名称
                    source_hint = _extract_source(message)

                    if source and source.lower() not in message.lower():
                        continue

                    errors.append({
                        "time": time_str,
                        "level": level,
                        "message": message,
                        "source_hint": source_hint,
                    })
        except Exception as e:
            logger.warning(f"[Admin] 读取日志文件失败 {log_file}: {e}")

    # 最新在前，截取 limit 条
    errors.reverse()
    return errors[:limit]


def _extract_source(message: str) -> Optional[str]:
    """从日志消息中提取源名称，如 [Scheduler] xxx_scrapy 执行失败"""
    # 匹配 [Scheduler] source_name 或 [Signal] source_name 模式
    m = re.search(r"\[(?:Scheduler|Signal)\]\s+(\S+)", message)
    return m.group(1) if m else None
