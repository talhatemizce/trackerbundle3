from __future__ import annotations

import time
from pathlib import Path

from app.core.config import get_settings
from app.core.json_store import file_lock, _read_unsafe, _write_unsafe

# ── In-memory cache (avoids redundant disk I/O per due() call) ────────────────
_cache: dict[str, float] | None = None


def _path() -> Path:
    return get_settings().resolved_data_dir() / "last_run.json"


def _ensure_cache() -> dict[str, float]:
    global _cache
    if _cache is None:
        p = _path()
        with file_lock(p):
            data = _read_unsafe(p, default={"by_isbn": {}})
        raw = data.get("by_isbn", {}) or {}
        _cache = {k: float(v) for k, v in raw.items()}
    return _cache


def get_last_run(isbn: str) -> float:
    cache = _ensure_cache()
    return cache.get(isbn, 0.0)


def set_last_run(isbn: str, ts: float | None = None) -> None:
    if ts is None:
        ts = time.time()

    cache = _ensure_cache()
    cache[isbn] = float(ts)

    p = _path()
    with file_lock(p):
        data = _read_unsafe(p, default={"by_isbn": {}})
        by = data.setdefault("by_isbn", {})
        by[isbn] = float(ts)
        _write_unsafe(p, data)


def due(isbn: str, interval_seconds: int, now: float | None = None) -> bool:
    if now is None:
        now = time.time()
    last = get_last_run(isbn)
    return (now - last) >= float(interval_seconds)
