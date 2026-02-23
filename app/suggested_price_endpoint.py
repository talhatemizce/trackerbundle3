# app/suggested_price_endpoint.py
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, HTTPException, Query

from app.ebay_client import (
    get_app_token, normalize_condition, BOOKS_CATEGORY_ID,
    browse_search_isbn, item_total_price,
)
from app.core.config import get_settings
from app import finding_cache
from app import sold_stats_store

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

# eBay Finding API maksimum 90 günlük satış verisi saklar.
# EndTimeFrom bu limitin dışına çıkarsa HTTP 500 döner.
_FINDING_MAX_DAYS = 90


async def _fetch_sold_in_range(
    client: httpx.AsyncClient,
    isbn: str,
    days_back: int,
    condition_filter: Optional[str] = None,  # "new" | "used" | None
    max_entries: int = 100,
) -> List[float]:
    """
    eBay Finding API ile satış toplamlarını döndürür. Disk cache kullanır.

    eBay Finding API kısıtları:
      - findCompletedItems maksimum 90 günlük veri saklar
      - EndTimeFrom 90 günden eski bir tarihe ayarlanırsa HTTP 500 döner

    Bu nedenle:
      - days_back <= 90  → EndTimeFrom/EndTimeTo filtreleri kullanılır
      - days_back  > 90  → Tarih filtresi kullanılmaz; eBay son 90 günü döndürür
        (365d ve 1095d cache'leri aynı veriyi taşır, farklı TTL'lerle saklanır)
    """
    # ── Cache kontrol ────────────────────────────────────────────────────────
    cached = finding_cache.get_cached(isbn, days_back, condition_filter)
    if cached is not None:
        logger.debug("Finding cache HIT isbn=%s days=%d cond=%s", isbn, days_back, condition_filter)
        return cached

    # ── Rate-limit backoff ────────────────────────────────────────────────────
    if finding_cache.is_rate_limited():
        st = finding_cache.rate_limit_status()
        logger.warning(
            "Finding API backoff aktif (%.0fs kaldı) — stale cache dönülüyor isbn=%s",
            st.get("remaining_seconds", 0), isbn,
        )
        stale = finding_cache.get_stale(isbn, days_back, condition_filter)
        return stale if stale is not None else []

    s = get_settings()
    app_id = s.ebay_app_id or s.ebay_client_id
    if not app_id:
        raise RuntimeError("EBAY_APP_ID eksik")

    now = datetime.now(timezone.utc)
    isbn_clean = isbn.replace("-", "").replace(" ", "").strip()

    # eBay Finding API ISO format
    def fmt(dt: datetime) -> str:
        return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")

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
    }

    filter_idx = 1

    # 90 günden kısa dönemler için tarih filtresi ekle
    # Uzun dönemler (365d / 1095d) için ekleme: eBay zaten max 90 gün döndürür
    if days_back <= _FINDING_MAX_DAYS:
        start = now - timedelta(days=days_back)
        params[f"itemFilter({filter_idx}).name"] = "EndTimeFrom"
        params[f"itemFilter({filter_idx}).value"] = fmt(start)
        filter_idx += 1
        params[f"itemFilter({filter_idx}).name"] = "EndTimeTo"
        params[f"itemFilter({filter_idx}).value"] = fmt(now)
        filter_idx += 1
    else:
        logger.debug(
            "isbn=%s days=%d > %d: date filter skipped (eBay 90d limit)",
            isbn, days_back, _FINDING_MAX_DAYS,
        )

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
        if not r.is_success:
            body_text = r.text[:600]
            logger.error(
                "Finding API HTTP %d isbn=%s days=%d cond=%s body=%s",
                r.status_code, isbn, days_back, condition_filter, body_text,
            )
            # Rate-limit tespiti → 23h backoff başlat
            if r.status_code in (429, 500) and any(
                kw in body_text for kw in ("RateLimiter", "exceeded operation", "rate limit", "quota")
            ):
                finding_cache.set_rate_limited(23)
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

    # ── Disk cache'e yaz ─────────────────────────────────────────────────────
    finding_cache.set_cached(isbn, days_back, condition_filter, totals)

    # ── Accumulator'a yaz (30d ve 90d — uzun pencere veri biriktirme) ────────
    # 365d/1095d sorgularının zaten "tarihsiz" (90d max eBay) döndürdüğünü
    # biliyoruz; bu snapshotları "365d" olarak saklamak yanlış etiketleme olur.
    if totals and days_back <= _FINDING_MAX_DAYS:
        sold_stats_store.append_snapshot(isbn, days_back, condition_filter, totals)

    return totals


async def _browse_price_proxy(
    client: httpx.AsyncClient,
    isbn: str,
    condition_filter: Optional[str],
) -> List[float]:
    """
    Fallback: Finding API backoff aktifken Browse API aktif listeleme fiyatlarını
    sold stats proxy olarak döndürür.
    condition_filter: "new" | "used" | None
    """
    s = get_settings()
    calc_est = s.calculated_ship_estimate_usd if s.calculated_ship_estimate_usd > 0 else None
    try:
        items = await browse_search_isbn(client, isbn, limit=50, strict=False)
    except Exception as e:
        logger.warning("browse_price_proxy error isbn=%s: %s", isbn, e)
        return []

    totals: List[float] = []
    for it in items:
        if condition_filter:
            bucket = normalize_condition(it.get("condition"), it.get("conditionId"))
            if condition_filter == "new" and bucket != "brand_new":
                continue
            if condition_filter == "used" and bucket == "brand_new":
                continue
        total = item_total_price(it, calc_ship_est=calc_est)
        if total is None:
            continue
        totals.append(total)

    logger.info(
        "browse_price_proxy isbn=%s cond=%s → %d prices",
        isbn, condition_filter, len(totals),
    )
    return sorted(totals)


def _avg(vals: List[float]) -> Optional[float]:
    return round(sum(vals) / len(vals), 2) if vals else None


def _volatility(vals: List[float]) -> Optional[float]:
    """sold_max / sold_min oranı. >2 ise fiyat tutarsız."""
    if not vals or min(vals) == 0:
        return None
    return round(max(vals) / min(vals), 2)


def _calc_suggested(
    avg_30: Optional[float],
    avg_90: Optional[float],
    avg_365: Optional[float],
    avg_fallback: Optional[float] = None,
) -> Optional[float]:
    """
    Ağırlıklı satış fiyatı tahmini.
    Formül: avg_30 × 0.25 + avg_90 × 0.25 + avg_365 × 0.50
    Eksik dönem normalize edilir (ağırlıkları mevcut dönemlere dağıt).
    avg_fallback: eski API uyumluluğu için (genellikle avg_3yr).
    """
    a30  = avg_30  or avg_fallback
    a90  = avg_90  or avg_fallback
    a365 = avg_365 or avg_fallback

    if not any([a30, a90, a365]):
        return None

    val = 0.0
    w_total = 0.0
    for v, w in [(a30, 0.25), (a90, 0.25), (a365, 0.50)]:
        if v is not None:
            val += v * w
            w_total += w

    if w_total == 0:
        return None

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
                # Finding API: sadece 30d ve 90d (eBay max 90 gün saklar)
                # Her başarılı çağrı accumulator'a da yazar (uzun pencere için birikim)
                d30_task = asyncio.create_task(
                    _fetch_sold_in_range(client, isbn_clean, 30,  cond_key)
                )
                d90_task = asyncio.create_task(
                    _fetch_sold_in_range(client, isbn_clean, 90,  cond_key)
                )
                vals_30, vals_90 = await asyncio.gather(
                    d30_task, d90_task, return_exceptions=True,
                )

                def safe(v: Any) -> List[float]:
                    return v if isinstance(v, list) else []

                v30 = safe(vals_30)
                v90 = safe(vals_90)

                # 365d ve 1095d: accumulator'dan (zamanla biriken gerçek tarihsel veri)
                # Daha az güvenilir veri varken fallback: 90d Finding API sonucu
                v365 = sold_stats_store.query_window(isbn_clean, 365,  cond_key)
                v3y  = sold_stats_store.query_window(isbn_clean, 1095, cond_key)

                # Fallback: accumulator boşsa mevcut en iyi veriyi kullan
                if not v365:
                    v365 = v90   # Henüz 365d birikim yok → 90d ile tahmini doldur
                if not v3y:
                    v3y = v365   # Henüz 3yr birikim yok → 365d ile tahmini doldur

                # ── Browse proxy fallback ─────────────────────────────────────
                # Finding backoff aktifse VE tüm dönemler boşsa aktif listeleme
                # fiyatlarını sold stats proxy olarak kullan.
                backoff_st = finding_cache.rate_limit_status()
                data_source = "finding_api"
                if backoff_st.get("active") and not any([v30, v90]):
                    proxy = await _browse_price_proxy(client, isbn_clean, cond_key)
                    if proxy:
                        v30 = v90 = v365 = v3y = proxy
                        data_source = "browse_proxy"
                    else:
                        data_source = "empty"
                elif any([v30, v90]):
                    data_source = "finding_api"
                elif any([v365, v3y]):
                    data_source = "accumulator"
                else:
                    data_source = "empty"

                avg_30  = _avg(v30)
                avg_90  = _avg(v90)
                avg_365 = _avg(v365)
                avg_3y  = _avg(v3y)

                # Suggested price formülü: ağırlıklı ortalama
                suggested = _calc_suggested(avg_30, avg_90, avg_365, avg_3y)

                # Volatility
                all_vals = v3y or v365 or v90 or v30
                vol = _volatility(all_vals)

                # Trend analizi
                trends = sold_stats_store.compute_trends(avg_30, avg_90, avg_365)

                # Accumulator span — kullanıcıya veri güvenilirliği göstergesi
                span_days = sold_stats_store.snapshot_span_days(isbn_clean, cond_key)

                results[cond_key] = {
                    "suggested": round(suggested) if suggested else None,
                    "suggested_exact": suggested,
                    "periods": {
                        "avg_30d": {"avg": avg_30,  "count": len(v30)},
                        "avg_90d": {"avg": avg_90,  "count": len(v90)},
                        "avg_365d": {
                            "avg": avg_365, "count": len(v365),
                            "accumulated": sold_stats_store.snapshot_span_days(isbn_clean, cond_key) is not None,
                        },
                        "avg_3yr": {
                            "avg": avg_3y, "count": len(v3y),
                            "accumulated": (span_days or 0) > 180,
                        },
                    },
                    "volatility": vol,
                    "volatile_warning": vol is not None and vol > 2.0,
                    "trends": trends,
                    "history_span_days": span_days,
                    "fallback_used": any([
                        avg_30  is None and avg_3y is not None,
                        avg_90  is None and avg_3y is not None,
                        avg_365 is None and avg_3y is not None,
                    ]),
                    "formula": "avg_30d×0.25 + avg_90d×0.25 + avg_365d×0.50",
                    "data_source": data_source,
                    "backoff_active": backoff_st.get("active", False),
                    "backoff_remaining_seconds": int(backoff_st.get("remaining_seconds", 0)),
                }

            except Exception as e:
                logger.exception("suggested_price error isbn=%s cond=%s", isbn_clean, cond_key)
                results[cond_key] = {"error": str(e)}

    response = {"ok": True, **results, "cached": False, "cache_age_seconds": 0}

    # ── Store in cache ────────────────────────────────────────────────────────
    async with lock:
        _RESPONSE_CACHE[isbn_clean] = {"ts": time.time(), "data": response}

    return response
