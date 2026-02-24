from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.config import get_settings
from app.core.json_store import file_lock, _read_unsafe, _write_unsafe


def _path() -> Path:
    p = get_settings().resolved_data_dir() / "alert_history.json"
    return p


def _data_dir() -> Path:
    return get_settings().resolved_data_dir()


def add_entry(
    isbn: str,
    item_id: str,
    title: str,
    condition: str,
    total: float,
    limit: float,
    decision: str,          # "BUY" | "OFFER"
    url: str = "",
    image_url: str = "",
    sold_avg: Optional[float] = None,
    sold_count: Optional[int] = None,
    ship_estimated: bool = False,
    match_quality: str = "CONFIRMED",        # "CONFIRMED" | "UNVERIFIED_SUPER_DEAL"
    verified: bool = True,
    verification_reason: str = "gtins_match",
    deal_score: Optional[int] = None,
) -> None:
    entry = {
        "ts": int(time.time()),
        "isbn": isbn,
        "item_id": item_id,
        "title": title,
        "condition": condition,
        "total": round(total, 2),
        "limit": round(limit, 2),
        "decision": decision,
        "url": url,
        "image_url": image_url,
        "sold_avg": sold_avg,
        "sold_count": sold_count,
        "ship_estimated": ship_estimated,
        "match_quality": match_quality,
        "verified": verified,
        "verification_reason": verification_reason,
        "deal_score": deal_score,
    }
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(p):
        data = _read_unsafe(p, default={"entries": []})
        entries = data.get("entries", [])
        entries.append(entry)
        # Keep last 500 entries
        if len(entries) > 500:
            entries = entries[-500:]
        _write_unsafe(p, {"entries": entries})


def get_history(limit: int = 50, isbn_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    p = _path()
    data = _read_unsafe(p, default={"entries": []})
    entries = data.get("entries", []) or []
    if isbn_filter:
        entries = [e for e in entries if e.get("isbn") == isbn_filter]
    # Newest first
    entries = list(reversed(entries))
    return entries[:limit]


def get_summary() -> Dict[str, Any]:
    p = _path()
    data = _read_unsafe(p, default={"entries": []})
    entries = data.get("entries", []) or []

    now = int(time.time())
    day_ago = now - 86400

    total = len(entries)
    last_24h = sum(1 for e in entries if e.get("ts", 0) >= day_ago)

    by_isbn: Dict[str, int] = {}
    for e in entries:
        isbn = e.get("isbn", "")
        by_isbn[isbn] = by_isbn.get(isbn, 0) + 1

    return {
        "total": total,
        "last_24h": last_24h,
        "by_isbn": by_isbn,
    }


def clear_isbn(isbn: str) -> int:
    p = _path()
    with file_lock(p):
        data = _read_unsafe(p, default={"entries": []})
        entries = data.get("entries", []) or []
        before = len(entries)
        entries = [e for e in entries if e.get("isbn") != isbn]
        after = len(entries)
        _write_unsafe(p, {"entries": entries})
    return before - after
