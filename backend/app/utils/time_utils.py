"""
时间工具
统一输出带时区的 UTC 时间戳，避免 naive datetime 造成时序语义漂移。
"""

from datetime import datetime, timezone


def utc_now() -> datetime:
    """返回当前 UTC 时间。"""
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    """返回当前 UTC ISO8601 时间字符串。"""
    return utc_now().isoformat()


def parse_iso_datetime(value: str) -> datetime:
    """
    解析 ISO8601 时间字符串。

    要求传入有效时间，且最终返回带时区的 datetime。
    """
    if not value or not isinstance(value, str):
        raise ValueError("timestamp 为空")

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"timestamp 缺少时区信息: {value}")
    return parsed.astimezone(timezone.utc)
