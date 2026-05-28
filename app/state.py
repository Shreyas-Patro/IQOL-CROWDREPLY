"""
Lightweight in-memory state shared across modules.
Only survives the process lifetime — used for UI "last scan" display.
"""
from datetime import datetime, timezone

_last_scan: datetime | None = None


def record_scan() -> None:
    global _last_scan
    _last_scan = datetime.now(timezone.utc)


def get_last_scan() -> datetime | None:
    return _last_scan
