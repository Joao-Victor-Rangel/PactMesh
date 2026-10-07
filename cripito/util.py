from __future__ import annotations

import time
from datetime import datetime, timezone

# Patchable clock so tests can simulate expiry without sleeping.
_offset = 0.0


def now() -> int:
    return int(time.time() + _offset)


def advance_clock(seconds: float) -> None:
    global _offset
    _offset += seconds


def iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> int:
    return int(datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())
