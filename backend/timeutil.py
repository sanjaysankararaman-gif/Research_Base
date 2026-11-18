"""Timestamps used across the app (ISO-8601, UTC, second precision)."""
from datetime import datetime, timezone


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
