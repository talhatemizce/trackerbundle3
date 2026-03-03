from __future__ import annotations

import time
from pathlib import Path

from app.core.config import get_settings
from app.core.json_store import file_lock, _read_unsafe, _write_unsafe

# In-memory cache — her due() çağrısında disk lock'tan kaçınır
_cache: dict = {}  # isbn → float (last_run timestamp)


def _path() -> Path:
    return get_settings().resolved_data_dir() / "last_run.json"


def get_last_run(isbn: str) -> float:
    if isbn in _cache:
        return _cache[isbn]
    p = _path()
    with file_lock(p):
        data = _read_unsafe(p, default={"by_isbn": {}})
        by = data.get("by_isbn", {}) or {}
        try:
            val = float(by.get(isbn, 0))
        except Exception:
            val = 0.0
    _cache[isbn] = val
    return val


def set_last_run(isbn: str, ts: float | None = None) -> None:
    if ts is None:
        ts = time.time()
    _cache[isbn] = float(ts)  # cache'i anında güncelle
    p = _path()
    with file_lock(p):
        data = _read_unsafe(p, default={"by_isbn": {}})
        by = data.setdefault("by_isbn", {})
        by[isbn] = float(ts)
        _write_unsafe(p, data)


def due(isbn: str, interval_seconds: int, now: float | None = None) -> bool:
    if now is None:
        now = time.time()
    last = get_last_run(isbn)  # cache hit — disk'e gitmiyor
    return (now - last) >= float(interval_seconds)
