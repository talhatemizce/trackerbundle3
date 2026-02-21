from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

from app.core.config import get_settings
from app.core.json_store import file_lock, _read_unsafe, _write_unsafe


def _path() -> Path:
    return get_settings().resolved_isbn_store()


def _clean(isbn: str) -> str:
    return re.sub(r"[^0-9X]", "", (isbn or "").upper()).strip()


def _validate(isbn: str) -> bool:
    s = _clean(isbn)
    return len(s) in (10, 13)


def _coerce(data: Any) -> Dict[str, List[str]]:
    # Supports:
    #   {"isbns":[...]}  (new)
    #   ["...","..."]    (legacy)
    if isinstance(data, dict):
        isbns = data.get("isbns", [])
        return {"isbns": [str(x) for x in isbns]} if isinstance(isbns, list) else {"isbns": []}
    if isinstance(data, list):
        return {"isbns": [str(x) for x in data]}
    return {"isbns": []}


def list_isbns() -> List[str]:
    p = _path()
    with file_lock(p):
        raw = _read_unsafe(p, default={"isbns": []})
        data = _coerce(raw)

        s: set[str] = set()
        for x in data["isbns"]:
            cx = _clean(x)
            if len(cx) in (10, 13):
                s.add(cx)

        out = sorted(s)

        # migrate-on-read: legacy list -> dict
        if isinstance(raw, list) or data.get("isbns") != out:
            _write_unsafe(p, {"isbns": out})

        return out


def add_isbn(isbn: str) -> bool:
    if not _validate(isbn):
        return False
    isbn = _clean(isbn)

    p = _path()
    with file_lock(p):
        raw = _read_unsafe(p, default={"isbns": []})
        data = _coerce(raw)
        s = set(_clean(x) for x in data["isbns"])
        if isbn in s:
            return False
        s.add(isbn)
        _write_unsafe(p, {"isbns": sorted(s)})
        return True


def delete_isbn(isbn: str) -> bool:
    isbn = _clean(isbn)
    if not isbn:
        return False

    p = _path()
    with file_lock(p):
        raw = _read_unsafe(p, default={"isbns": []})
        data = _coerce(raw)
        s = set(_clean(x) for x in data["isbns"])
        if isbn not in s:
            return False
        s.remove(isbn)
        _write_unsafe(p, {"isbns": sorted(s)})
        return True
