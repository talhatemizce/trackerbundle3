from __future__ import annotations

import time
from pathlib import Path

from app.core.config import get_settings
from app.core.json_store import file_lock, _read_unsafe, _write_unsafe


def _path() -> Path:
    return get_settings().resolved_data_dir() / "last_run.json"


def get_last_run(isbn: str) -> float:
    p = _path()
    with file_lock(p):
        data = _read_unsafe(p, default={"by_isbn": {}})
        by = data.get("by_isbn", {}) or {}
        try:
            return float(by.get(isbn, 0))
        except Exception:
            return 0.0


def set_last_run(isbn: str, ts: float | None = None) -> None:
    if ts is None:
        ts = time.time()

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
