"""
Suggested price calculator with caching and trend analysis.

Uses eBay Finding API (findCompletedItems) for sold stats by ISBN.

Cache policy:
  - Short windows (30d, 100d): refresh every SGPRICE_SHORT_TTL_HOURS (default 2h)
  - Long windows (365d, 3yr): refresh every SGPRICE_LONG_TTL_HOURS (default 6h)

Weighted formula:
  suggested = avg_30d * 0.25 + avg_100d * 0.25 + avg_365d * 0.50
  fallback: if any period missing, available weights are normalised to sum 1.0
  fallback for missing 365d: use 3yr average instead

Trend signal:
  compare avg_30d vs avg_365d (or 3yr if 365d missing)
  delta_pct = (avg_30d - avg_365d) / avg_365d * 100
  if delta_pct < -SGPRICE_TREND_THRESHOLD*100 : trend="down"  (market dropping, warning)
  if delta_pct >  SGPRICE_TREND_THRESHOLD*100 : trend="up"    (market rising, opportunity)
  else                                          : trend="flat"

Env vars:
  SGPRICE_SHORT_TTL_HOURS  (default 2)
  SGPRICE_LONG_TTL_HOURS   (default 6)
  SGPRICE_TREND_THRESHOLD  (default 0.40 = 40%)
  EBAY_APP_ID              required for Finding API
"""
from __future__ import annotations

import os
import json
import time
import asyncio
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

import httpx

# ── Paths ──────────────────────────────────────────────────────────────────────
DATA_DIR   = Path(__file__).resolve().parent / "data"
CACHE_FILE = DATA_DIR / "suggested_price_cache.json"

# ── Configuration from env ────────────────────────────────────────────────────
SHORT_TTL_HOURS    = float(os.getenv("SGPRICE_SHORT_TTL_HOURS", "2"))
LONG_TTL_HOURS     = float(os.getenv("SGPRICE_LONG_TTL_HOURS", "6"))
TREND_THRESHOLD    = float(os.getenv("SGPRICE_TREND_THRESHOLD", "0.40"))
EBAY_APP_ID        = os.getenv("EBAY_APP_ID", "").strip()

# period_key -> (days_back, ttl_hours)
_PERIODS: Dict[str, tuple] = {
    "30d":  (30,   SHORT_TTL_HOURS),
    "100d": (100,  SHORT_TTL_HOURS),
    "365d": (365,  LONG_TTL_HOURS),
    "3yr":  (1095, LONG_TTL_HOURS),
}

# ── Async concurrency lock (one refresh at a time per process) ────────────────
_cache_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    """Lazy-create the lock so it belongs to the running event loop."""
    global _cache_lock
    if _cache_lock is None:
        _cache_lock = asyncio.Lock()
    return _cache_lock


# ── Atomic file I/O (no sync lock inside async path) ─────────────────────────

def _read_cache_unsafe() -> Dict[str, Any]:
    """Read cache file; returns {} on any error. Must be called while holding _cache_lock."""
    try:
        if CACHE_FILE.exists():
            raw = CACHE_FILE.read_text(encoding="utf-8").strip()
            return json.loads(raw or "{}")
    except Exception:
        pass
    return {}


def _write_cache_unsafe(data: Dict[str, Any]) -> None:
    """Atomic write via tmp+os.replace. Must be called while holding _cache_lock."""
    import os as _os
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    _os.replace(tmp, CACHE_FILE)


# ── eBay Finding API helper with date range ───────────────────────────────────

async def _finding_sold_avg(
    client: httpx.AsyncClient,
    keywords: str,
    days: int,
) -> Optional[float]:
    """
    Query findCompletedItems for `keywords` within last `days` days.
    Returns average sold price (float) or None if no data / error.
    """
    if not EBAY_APP_ID:
        raise RuntimeError("EBAY_APP_ID not set — cannot fetch sold stats")

    now_utc        = datetime.now(timezone.utc)
    end_time_from  = (now_utc - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    base   = "https://svcs.ebay.com/services/search/FindingService/v1"
    params = {
        "OPERATION-NAME":               "findCompletedItems",
        "SERVICE-VERSION":              "1.13.0",
        "SECURITY-APPNAME":             EBAY_APP_ID,
        "RESPONSE-DATA-FORMAT":         "JSON",
        "REST-PAYLOAD":                 "true",
        "keywords":                     keywords,
        "paginationInput.entriesPerPage": "100",
        "itemFilter(0).name":           "SoldItemsOnly",
        "itemFilter(0).value":          "true",
        "itemFilter(1).name":           "EndTimeFrom",
        "itemFilter(1).value":          end_time_from,
    }

    try:
        r = await client.get(base, params=params, timeout=20)
        j = r.json() if r.text else {}
        if r.status_code < 200 or r.status_code >= 300:
            return None

        resp  = (j.get("findCompletedItemsResponse") or [])[0]
        sr    = (resp.get("searchResult") or [])[0]
        items = sr.get("item") or []

        totals: list[float] = []
        for it in items:
            selling = (it.get("sellingStatus") or [])[0]
            cur     = (selling.get("currentPrice") or [])[0]
            v       = cur.get("__value__")
            if v is None:
                continue
            try:
                totals.append(float(v))
            except Exception:
                continue

        if not totals:
            return None
        return sum(totals) / len(totals)

    except Exception:
        return None


# ── Public API ────────────────────────────────────────────────────────────────

def _safe_int(x: Any) -> Optional[int]:
    try:
        return int(round(float(x))) if x is not None else None
    except Exception:
        return None


async def get_suggested_price(isbn: str) -> Dict[str, Any]:
    """
    Returns a dict with:
      isbn            : str
      suggested       : int | None       weighted average, int-rounded
      avgs            : {30d, 100d, 365d, 3yr}  each int | None
      trend           : "up" | "down" | "flat" | "unknown"
      delta_pct       : float | None     (avg_30d − avg_365d) / avg_365d × 100
      price_shift_flag: bool             true if |delta_pct| > threshold
      fetched_at      : ISO-8601 UTC string
    """
    lock = _get_lock()
    async with lock:
        cache  = _read_cache_unsafe()
        entry: Dict[str, Any]          = cache.get(isbn) or {}
        avgs:  Dict[str, Optional[float]] = {k: entry.get("avgs", {}).get(k) for k in _PERIODS}
        fetched_at_ts: Dict[str, float]   = entry.get("fetched_at_ts", {})
        now_ts = time.time()

        # Determine which periods are stale
        stale: list[tuple[str, int]] = []
        for period_key, (days, ttl_hours) in _PERIODS.items():
            last = fetched_at_ts.get(period_key, 0.0)
            if now_ts - last > ttl_hours * 3600:
                stale.append((period_key, days))

        if stale:
            keywords = f"ISBN {isbn}"
            async with httpx.AsyncClient(timeout=25) as client:
                for period_key, days in stale:
                    val = await _finding_sold_avg(client, keywords, days)
                    avgs[period_key]            = val
                    fetched_at_ts[period_key]   = now_ts

            # Persist updated cache (atomic)
            cache[isbn] = {
                "avgs":         {k: v for k, v in avgs.items()},
                "fetched_at_ts": fetched_at_ts,
            }
            _write_cache_unsafe(cache)

    # ── Weighted suggested price ──────────────────────────────────────────────
    # Weights: 30d=0.25, 100d=0.25, 365d=0.50
    # If 365d missing, substitute 3yr.
    weights = {"30d": 0.25, "100d": 0.25, "365d": 0.50}

    a365 = avgs.get("365d")
    a3yr = avgs.get("3yr")
    effective_365 = a365 if a365 is not None else a3yr  # fallback

    effective: Dict[str, Optional[float]] = {
        "30d":  avgs.get("30d"),
        "100d": avgs.get("100d"),
        "365d": effective_365,
    }

    total_weight = sum(w for k, w in weights.items() if effective.get(k) is not None)
    if total_weight == 0:
        suggested = None
    else:
        raw = sum(
            (effective[k] or 0.0) * w
            for k, w in weights.items()
            if effective.get(k) is not None
        ) / total_weight
        suggested = int(round(raw))

    # ── Trend signal ──────────────────────────────────────────────────────────
    a30       = avgs.get("30d")
    trend     = "unknown"
    delta_pct: Optional[float] = None
    price_shift_flag = False

    if a30 is not None and effective_365 is not None and effective_365 != 0:
        delta_pct = round((a30 - effective_365) / effective_365 * 100, 1)
        abs_threshold = TREND_THRESHOLD * 100
        if delta_pct < -abs_threshold:
            trend            = "down"
            price_shift_flag = True
        elif delta_pct > abs_threshold:
            trend            = "up"
            price_shift_flag = True
        else:
            trend = "flat"

    return {
        "isbn":             isbn,
        "suggested":        suggested,
        "avgs": {
            "30d":  _safe_int(avgs.get("30d")),
            "100d": _safe_int(avgs.get("100d")),
            "365d": _safe_int(avgs.get("365d")),
            "3yr":  _safe_int(avgs.get("3yr")),
        },
        "trend":            trend,
        "delta_pct":        delta_pct,
        "price_shift_flag": price_shift_flag,
        "fetched_at":       datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


async def bust_cache(isbn: str) -> None:
    """Remove all cached data for an ISBN so next call fetches fresh."""
    lock = _get_lock()
    async with lock:
        cache = _read_cache_unsafe()
        if isbn in cache:
            del cache[isbn]
            _write_cache_unsafe(cache)
