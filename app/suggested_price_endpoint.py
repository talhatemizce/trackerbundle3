# app/suggested_price_endpoint.py
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, HTTPException, Query

from app.ebay_client import get_app_token, normalize_condition, BOOKS_CATEGORY_ID
from app.core.config import get_settings
from app import finding_cache

logger = logging.getLogger("trackerbundle.suggested_price")
router = APIRouter(tags=["suggested-price"])

# ── In-memory response cache ──────────────────────────────────────────────────
# Key: isbn_clean.  Value: {"ts": float, "data": dict}
# TTL driven by SGPRICE_SHORT_TTL_HOURS (default 2 h).
# Lock is lazy-created inside the running event loop to stay compatible with Python 3.9.
import os as _os
_RESPONSE_CACHE: Dict[str, Dict[str, Any]] = {}
_CACHE_TTL_SECONDS = int(float(_os.getenv("SGPRICE_SHORT_TTL_HOURS", "2")) * 3600)
_cache_lock: Optional[asyncio.Lock] = None


def _get_cache_lock() -> asyncio.Lock:
    global _cache_lock
    if _cache_lock is None:
        _cache_lock = asyncio.Lock()
    return _cache_lock


# ── Finding API ile tarih filtreli sold sorgusu ──────────────────────────────

async def _fetch_sold_in_range(
    client: httpx.AsyncClient,
    isbn: str,
    days_back: int,
    condition_filter: Optional[str] = None,  # "new" | "used" | None
    max_entries: int = 100,
) -> List[float]:
    """
    eBay Finding API ile son `days_back` gündeki satış toplamlarını döndürür.
    Disk cache kullanır (30d/100d → 2h TTL, 365d/3yr → 24h TTL).
    Tarih: endTimeFrom = (now - days_back), endTimeTo = now
    """
    # ── Cache kontrol ────────────────────────────────────────────────────────
    cached = finding_cache.get_cached(isbn, days_back, condition_filter)
    if cached is not None:
        logger.debug("Finding cache HIT isbn=%s days=%d cond=%s", isbn, days_back, condition_filter)
        return cached

    s = get_settings()
    app_id = s.ebay_app_id or s.ebay_client_id
    if not app_id:
        raise RuntimeError("EBAY_APP_ID eksik")

    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days_back)

    # eBay Finding API ISO format: 2024-01-15T00:00:00.000Z
    def fmt(dt: datetime) -> str:
        return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    isbn_clean = isbn.replace("-", "").replace(" ", "").strip()

    params: Dict[str, str] = {
        "OPERATION-NAME": "findCompletedItems",
        "SERVICE-VERSION": "1.13.0",
        "SECURITY-APPNAME": app_id,
        "RESPONSE-DATA-FORMAT": "JSON",
        "REST-PAYLOAD": "true",
        "keywords": isbn_clean,
        "categoryId": BOOKS_CATEGORY_ID,
        "paginationInput.entriesPerPage": str(max(1, min(max_entries, 100))),
        "itemFilter(0).name": "SoldItemsOnly",
        "itemFilter(0).value": "true",
        "itemFilter(1).name": "EndTimeFrom",
        "itemFilter(1).value": fmt(start),
        "itemFilter(2).name": "EndTimeTo",
        "itemFilter(2).value": fmt(now),
    }

    filter_idx = 3
    if condition_filter == "new":
        params[f"itemFilter({filter_idx}).name"] = "Condition"
        params[f"itemFilter({filter_idx}).value"] = "New"
        filter_idx += 1
    elif condition_filter == "used":
        params[f"itemFilter({filter_idx}).name"] = "Condition"
        for i, v in enumerate(["Used", "Good", "Very Good", "Acceptable", "Like New"]):
            params[f"itemFilter({filter_idx}).value({i})"] = v
        filter_idx += 1

    try:
        r = await client.get(
            "https://svcs.ebay.com/services/search/FindingService/v1",
            params=params,
            timeout=25,
        )
        r.raise_for_status()
    except Exception as api_err:
        # ── Rate-limit / network hatası: stale cache'e düş ──────────────────
        logger.warning(
            "Finding API error isbn=%s days=%d cond=%s err=%s — trying stale cache",
            isbn, days_back, condition_filter, api_err,
        )
        stale = finding_cache.get_stale(isbn, days_back, condition_filter)
        if stale is not None:
            logger.info(
                "Stale cache fallback OK isbn=%s days=%d cond=%s count=%d",
                isbn, days_back, condition_filter, len(stale),
            )
            return stale
        logger.warning(
            "No stale cache for isbn=%s days=%d cond=%s — returning empty",
            isbn, days_back, condition_filter,
        )
        return []

    j = r.json()

    totals: List[float] = []
    try:
        resp = (j.get("findCompletedItemsResponse") or [{}])[0]
        sr = (resp.get("searchResult") or [{}])[0]
        items = sr.get("item") or []

        for it in items:
            selling = (it.get("sellingStatus") or [{}])[0]
            cur = (selling.get("currentPrice") or [{}])[0]
            price_val = cur.get("__value__")
            if price_val is None:
                continue
            try:
                price_f = float(price_val)
            except (TypeError, ValueError):
                continue

            ship_f = 0.0
            try:
                ship_info = (it.get("shippingInfo") or [{}])[0]
                ship_cost = (ship_info.get("shippingServiceCost") or [{}])[0]
                sv = ship_cost.get("__value__")
                if sv is not None:
                    ship_f = float(sv)
            except Exception:
                pass

            totals.append(round(price_f + ship_f, 2))
    except Exception:
        logger.exception("Finding parse error isbn=%s days=%d", isbn, days_back)

    # ── Cache'e yaz (boş sonuç bile cache'lenir → rate limit baskısını azaltır) ──
    finding_cache.set_cached(isbn, days_back, condition_filter, totals)
    return totals


def _avg(vals: List[float]) -> Optional[float]:
    return round(sum(vals) / len(vals), 2) if vals else None


def _volatility(vals: List[float]) -> Optional[float]:
    """sold_max / sold_min oranı. >2 ise fiyat tutarsız."""
    if not vals or min(vals) == 0:
        return None
    return round(max(vals) / min(vals), 2)


def _calc_suggested(
    avg_30: Optional[float],
    avg_100: Optional[float],
    avg_365: Optional[float],
    avg_fallback: Optional[float],
) -> Optional[float]:
    """
    Formül: avg_30*0.25 + avg_100*0.25 + avg_365*0.50
    Eksik dönem → fallback ile doldur.
    """
    a30  = avg_30  or avg_fallback
    a100 = avg_100 or avg_fallback
    a365 = avg_365 or avg_fallback

    if not any([a30, a100, a365]):
        return None

    # Her eksik dönemi fallback ile doldur, ağırlıkları koru
    val = 0.0
    w_total = 0.0
    for v, w in [(a30, 0.25), (a100, 0.25), (a365, 0.50)]:
        if v is not None:
            val += v * w
            w_total += w

    if w_total == 0:
        return None

    # Eksik ağırlık varsa normalize et
    return round(val / w_total, 2)


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.get("/suggested-price/{isbn}/cache/clear", tags=["suggested-price"])
async def clear_suggested_price_cache(isbn: str):
    """In-memory + disk cache'i bu ISBN için sıfırla (panel'den manuel tetikleme)."""
    isbn_clean = isbn.replace("-", "").replace(" ", "").strip()
    async with _get_cache_lock():
        removed = isbn_clean in _RESPONSE_CACHE
        _RESPONSE_CACHE.pop(isbn_clean, None)
    disk_removed = finding_cache.clear_isbn(isbn_clean)
    return {"ok": True, "isbn": isbn_clean, "removed": removed, "disk_entries_removed": disk_removed}


@router.get("/suggested-price/{isbn}")
async def get_suggested_price(
    isbn: str,
    condition: str = Query(
        default="used",
        description="'new' veya 'used'. Her ikisi için de hesaplar.",
    ),
    force_refresh: bool = Query(
        default=False,
        description="True ise cache bypass edilerek fresh veri çekilir.",
    ),
):
    """
    ISBN için önerilen alım fiyatı hesaplar.

    Formül:
      suggested = avg_30d * 0.25 + avg_100d * 0.25 + avg_365d * 0.50
      Eksik dönem → 3 yıllık fallback avg ile doldurulur.
      New ve used kondisyon ayrı hesaplanır.

    Ek metrikler:
      - volatility: max/min oranı (>2 ise fiyat tutarsız uyarısı)
      - sample_count: kaç satış baz alındı
      - cached: True ise cache'den döndü
      - cache_age_seconds: cache ne kadar yaşlı
    """
    isbn_clean = isbn.replace("-", "").replace(" ", "").strip()

    # ── Cache check ──────────────────────────────────────────────────────────
    lock = _get_cache_lock()
    if not force_refresh:
        async with lock:
            entry = _RESPONSE_CACHE.get(isbn_clean)
            if entry:
                age = time.time() - entry["ts"]
                if age < _CACHE_TTL_SECONDS:
                    cached_data = dict(entry["data"])
                    cached_data["cached"] = True
                    cached_data["cache_age_seconds"] = int(age)
                    return cached_data

    # ── Fresh fetch ───────────────────────────────────────────────────────────
    results: Dict[str, Any] = {"isbn": isbn_clean, "new": None, "used": None}

    async with httpx.AsyncClient(timeout=40) as client:
        for cond_key in ["new", "used"]:
            try:
                # Paralel fetch: 30d, 100d, 365d, 1095d (3yıl)
                d30_task  = asyncio.create_task(_fetch_sold_in_range(client, isbn_clean, 30,   cond_key))
                d100_task = asyncio.create_task(_fetch_sold_in_range(client, isbn_clean, 100,  cond_key))
                d365_task = asyncio.create_task(_fetch_sold_in_range(client, isbn_clean, 365,  cond_key))
                d3y_task  = asyncio.create_task(_fetch_sold_in_range(client, isbn_clean, 1095, cond_key))

                vals_30, vals_100, vals_365, vals_3y = await asyncio.gather(
                    d30_task, d100_task, d365_task, d3y_task,
                    return_exceptions=True,
                )

                # Exception'ları boş listeye çevir
                def safe(v: Any) -> List[float]:
                    return v if isinstance(v, list) else []

                v30  = safe(vals_30)
                v100 = safe(vals_100)
                v365 = safe(vals_365)
                v3y  = safe(vals_3y)

                avg_30  = _avg(v30)
                avg_100 = _avg(v100)
                avg_365 = _avg(v365)
                avg_3y  = _avg(v3y)

                suggested = _calc_suggested(avg_30, avg_100, avg_365, avg_3y)

                # Volatility: en geniş veri setinden hesapla
                all_vals = v3y or v365 or v100 or v30
                vol = _volatility(all_vals)

                results[cond_key] = {
                    "suggested": round(suggested) if suggested else None,
                    "suggested_exact": suggested,
                    "periods": {
                        "avg_30d":  {"avg": avg_30,  "count": len(v30)},
                        "avg_100d": {"avg": avg_100, "count": len(v100)},
                        "avg_365d": {"avg": avg_365, "count": len(v365)},
                        "avg_3yr":  {"avg": avg_3y,  "count": len(v3y)},
                    },
                    "volatility": vol,
                    "volatile_warning": vol is not None and vol > 2.0,
                    "fallback_used": any([
                        avg_30  is None and avg_3y is not None,
                        avg_100 is None and avg_3y is not None,
                        avg_365 is None and avg_3y is not None,
                    ]),
                    "formula": "avg_30d×0.25 + avg_100d×0.25 + avg_365d×0.50",
                }

            except Exception as e:
                logger.exception("suggested_price error isbn=%s cond=%s", isbn_clean, cond_key)
                results[cond_key] = {"error": str(e)}

    response = {"ok": True, **results, "cached": False, "cache_age_seconds": 0}

    # ── Store in cache ────────────────────────────────────────────────────────
    async with lock:
        _RESPONSE_CACHE[isbn_clean] = {"ts": time.time(), "data": response}

    return response
