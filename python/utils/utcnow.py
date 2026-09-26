"""UTC 时间工具。

统一用 aware-now 后去除 tzinfo 生成 naive-UTC，规避 Python 3.12 起弃用的
datetime.utcnow()，同时保持既有 DATETIME 字符串格式（无 tz 后缀）不变。
新代码一律 import 这里的 utcnow() / utcnow_iso()，不要在业务里手写
datetime.now(timezone.utc).replace(tzinfo=None)。
"""
from datetime import datetime, timezone


def utcnow() -> datetime:
    """naive 的 UTC 当前时间。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utcnow_iso() -> str:
    """naive-UTC 的 ISO 字符串（%Y-%m-%dT%H:%M:%S.%f）。"""
    return utcnow().isoformat()
