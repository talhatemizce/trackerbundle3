from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict

from app.core.config import get_settings
from app.core.json_store import file_lock, _read_unsafe, _write_unsafe

logger = logging.getLogger("trackerbundle.alert_store")


def _path() -> Path:
    return get_settings().resolved_notified_file()


def check_and_mark(isbn: str, item_id: str) -> bool:
    """
    Tek lock içinde kontrol + işaretle.
    True  => zaten vardı (gönderme)
    False => yeni (gönder)
    Per-ISBN max 200 item_id tutulur (eski olanlar silinir).
    """
    _MAX_PER_ISBN = 200
    p = _path()
    with file_lock(p):
        data = _read_unsafe(p, default={"by_isbn": {}})
        by_isbn = data.setdefault("by_isbn", {})
        items = by_isbn.get(isbn, [])
        s = set(items)
        if item_id in s:
            return True
        s.add(item_id)
        trimmed = sorted(s)[-_MAX_PER_ISBN:]
        by_isbn[isbn] = trimmed
        _write_unsafe(p, data)
        return False


def clear_isbn(isbn: str) -> int:
    p = _path()
    with file_lock(p):
        data = _read_unsafe(p, default={"by_isbn": {}})
        by = data.get("by_isbn", {})
        count = len(by.get(isbn, []))
        if isbn in by:
            del by[isbn]
            _write_unsafe(p, data)
    return count


def get_stats() -> Dict[str, int]:
    data = _read_unsafe(_path(), default={"by_isbn": {}})
    return {isbn: len(ids) for isbn, ids in (data.get("by_isbn", {}) or {}).items()}
