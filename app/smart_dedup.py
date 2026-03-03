"""
Smart dedup — price-tolerant, TTL-based, better-deal override.

Key: isbn + bucket + price_bucket (rounded to $0.25)
TTL: 6 hours
Override: new alert fires if score improves by ≥10 OR price drops ≥15%
"""
from __future__ import annotations

import math
import time
import logging
from pathlib import Path
from typing import Optional

from app.core.config import get_settings
from app.core.json_store import file_lock, _read_unsafe, _write_unsafe

logger = logging.getLogger("trackerbundle.smart_dedup")

_DEDUP_TTL_S    = 6 * 3600   # 6 hours
_PRICE_BUCKET   = 0.25        # $0.25 tolerance band
_SCORE_OVERRIDE = 10          # score improvement threshold
_PRICE_OVERRIDE = 0.15        # 15% cheaper → always fire


def _path() -> Path:
    return get_settings().resolved_data_dir() / "smart_dedup.json"


def _price_key(total: float) -> str:
    """Round to nearest $0.25 bucket."""
    return str(math.floor(total / _PRICE_BUCKET) * _PRICE_BUCKET)


def _dedup_key(isbn: str, bucket: str, total: float) -> str:
    return f"{isbn}|{bucket}|{_price_key(total)}"


def should_send(
    isbn: str,
    bucket: str,
    total: float,
    score: int,
    item_id: str,
) -> tuple[bool, str]:
    """
    Returns (send: bool, reason: str).

    Reasons:
      "new"           — never seen
      "ttl_expired"   — seen but TTL elapsed
      "better_score"  — same price band, score improved ≥10
      "better_price"  — price ≥15% lower than last seen
      "duplicate"     — suppressed
    """
    p = _path()
    now = time.time()

    with file_lock(p):
        data = _read_unsafe(p, default={"entries": {}})
        entries = data.setdefault("entries", {})

        # Expire old TTL entries (keep memory bounded)
        stale = [k for k, v in entries.items() if now - v.get("ts", 0) > _DEDUP_TTL_S * 2]
        for k in stale:
            del entries[k]

        key = _dedup_key(isbn, bucket, total)
        existing = entries.get(key)

        if existing is None:
            # Check if there's ANY entry for same isbn+bucket (for better_price check)
            prefix = f"{isbn}|{bucket}|"
            siblings = {k: v for k, v in entries.items() if k.startswith(prefix)}
            
            if siblings:
                # Find the min price seen recently
                min_price = min((v.get("total", 9999) for v in siblings.values() if now - v.get("ts",0) < _DEDUP_TTL_S), default=9999)
                if min_price < 9999 and total <= min_price * (1 - _PRICE_OVERRIDE):
                    # Significantly cheaper than anything seen — fire
                    _mark(entries, key, total, score, item_id, now)
                    _write_unsafe(p, data)
                    return True, "better_price"
            
            _mark(entries, key, total, score, item_id, now)
            _write_unsafe(p, data)
            return True, "new"

        ts = existing.get("ts", 0)

        # TTL expired
        if now - ts > _DEDUP_TTL_S:
            _mark(entries, key, total, score, item_id, now)
            _write_unsafe(p, data)
            return True, "ttl_expired"

        # Better score override
        last_score = existing.get("score", 0)
        if score >= last_score + _SCORE_OVERRIDE:
            _mark(entries, key, total, score, item_id, now)
            _write_unsafe(p, data)
            return True, "better_score"

        # Suppress
        return False, "duplicate"


def _mark(entries: dict, key: str, total: float, score: int, item_id: str, now: float) -> None:
    entries[key] = {"ts": now, "total": total, "score": score, "item_id": item_id}


def clear_isbn(isbn: str) -> int:
    p = _path()
    with file_lock(p):
        data = _read_unsafe(p, default={"entries": {}})
        keys = [k for k in data.get("entries", {}) if k.startswith(f"{isbn}|")]
        for k in keys:
            del data["entries"][k]
        _write_unsafe(p, data)
    return len(keys)


def get_stats() -> dict:
    data = _read_unsafe(_path(), default={"entries": {}})
    now = time.time()
    active = {k: v for k, v in data.get("entries", {}).items()
              if now - v.get("ts", 0) < _DEDUP_TTL_S}
    return {"active_keys": len(active), "total_keys": len(data.get("entries", {}))}
