"""
TrackerBundle Domain API
========================
Bot'un (port 8000) bağlandığı ana API.
ISBN yönetimi, rules, watchlist endpointleri.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
import csv
import io
from typing import List, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from app import isbn_store
from app import rules_store

app = FastAPI(title="TrackerBundle API", version="0.2.0")

# CORS (panel dev mode port 3000 + production)
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---- Models ----
class ISBNItem(BaseModel):
    isbn: str = Field(min_length=1)


class IntervalPayload(BaseModel):
    interval_seconds: int = Field(gt=0)


class OverridePayload(BaseModel):
    new_max: Optional[float] = Field(default=None, gt=0)
    used_all_max: Optional[float] = Field(default=None, gt=0)


class ImportRow(BaseModel):
    isbn: str
    new_max: Optional[float] = Field(default=None, gt=0)
    used_all_max: Optional[float] = Field(default=None, gt=0)
    interval_seconds: Optional[int] = Field(default=None, gt=0)


class ImportPayload(BaseModel):
    """
    Toplu ISBN ekleme.
    Ya rows (JSON dizi) ya da csv_text (CSV metni) gönderilmeli.

    CSV formatı (başlık satırı zorunlu):
        isbn,new_max,used_all_max,interval
        9780132350884,50,30,4h
        9780974769431,,25,
    """
    rows: Optional[List[ImportRow]] = None
    csv_text: Optional[str] = None


# ---- Core routes (bot talks to these) ----
@app.get("/")
def home():
    return {
        "name": "TrackerBundle API",
        "version": app.version,
        "docs": "/docs",
        "endpoints": ["/health", "/status", "/isbns", "/rules/*"],
    }


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/status")
def status():
    isbns = isbn_store.list_isbns()
    return {
        "ok": True,
        "service": "trackerbundle-api",
        "time_utc": datetime.now(timezone.utc).isoformat(),
        "has_bot_token": bool(os.getenv("TELEGRAM_BOT_TOKEN")),
        "isbn_count": len(isbns),
    }


# ---- ISBN endpoints (file_lock + atomic write via isbn_store) ----
@app.get("/isbns")
def list_isbns_endpoint():
    items = isbn_store.list_isbns()
    return {"ok": True, "count": len(items), "items": items}


@app.post("/isbns")
def add_isbn_endpoint(item: ISBNItem):
    isbn = item.isbn.strip()
    if not isbn:
        raise HTTPException(status_code=400, detail="isbn empty")

    # Validate before attempting add
    if not isbn_store._validate(isbn):
        raise HTTPException(status_code=400, detail="invalid_isbn")

    canonical = isbn_store._clean(isbn)
    added = isbn_store.add_isbn(isbn)
    count = len(isbn_store.list_isbns())
    return {"ok": True, "added": added, "isbn": canonical, "count": count}


@app.delete("/isbns/{isbn}")
def delete_isbn_endpoint(isbn: str):
    isbn = isbn.strip()
    deleted = isbn_store.delete_isbn(isbn)
    count = len(isbn_store.list_isbns())
    return {"ok": True, "deleted": deleted, "isbn": isbn, "count": count}


def _parse_csv_import(csv_text: str) -> List[ImportRow]:
    """CSV metin → ImportRow listesi. Başlık satırı: isbn[,new_max[,used_all_max[,interval]]]"""
    rows: List[ImportRow] = []
    reader = csv.DictReader(io.StringIO(csv_text.strip()))
    for lineno, row in enumerate(reader, start=2):
        isbn_raw = (row.get("isbn") or "").strip()
        if not isbn_raw:
            continue

        def _f(key: str) -> Optional[float]:
            v = (row.get(key) or "").strip()
            if not v:
                return None
            try:
                return float(v)
            except ValueError:
                raise ValueError(f"Satır {lineno}: '{key}' sayı değil: {v!r}")

        def _interval(key: str = "interval") -> Optional[int]:
            v = (row.get(key) or "").strip()
            if not v:
                return None
            # "4h", "30m", "1d", ya da saniye sayısı
            import re as _re
            m = _re.match(r"^(\d+(?:\.\d+)?)(d|h|m|s)?$", v.lower())
            if not m:
                raise ValueError(f"Satır {lineno}: interval formatı geçersiz: {v!r}")
            n, u = float(m.group(1)), (m.group(2) or "s")
            secs = int(n * {"d": 86400, "h": 3600, "m": 60, "s": 1}[u])
            return secs

        rows.append(ImportRow(
            isbn=isbn_raw,
            new_max=_f("new_max"),
            used_all_max=_f("used_all_max"),
            interval_seconds=_interval(),
        ))
    return rows


@app.post("/isbns/import")
def import_isbns(payload: ImportPayload):
    """
    Toplu ISBN ekleme. rows JSON dizisi veya csv_text CSV metni kabul eder.
    Geçersiz ISBN'ler skip edilir, hata kesmez.
    """
    rows: List[ImportRow] = []
    errors: List[str] = []

    if payload.csv_text:
        try:
            rows = _parse_csv_import(payload.csv_text)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))

    if payload.rows:
        rows.extend(payload.rows)

    if not rows:
        raise HTTPException(status_code=422, detail="rows veya csv_text boş")

    added_isbns: List[str] = []
    skipped: List[str] = []

    for row in rows:
        added = isbn_store.add_isbn(row.isbn)
        if added:
            added_isbns.append(row.isbn)
            # Opsiyonel limit/interval kaydet
            try:
                if row.new_max is not None or row.used_all_max is not None:
                    rules_store.set_override(row.isbn, new_max=row.new_max, used_all_max=row.used_all_max)
                if row.interval_seconds is not None:
                    rules_store.set_interval(row.isbn, row.interval_seconds)
            except ValueError as e:
                errors.append(f"{row.isbn}: {e}")
        else:
            skipped.append(row.isbn)

    return {
        "ok": True,
        "added": len(added_isbns),
        "skipped_duplicates": len(skipped),
        "errors": errors,
        "added_isbns": added_isbns,
    }


# ---- Rules endpoints (interval + overrides, file_lock via rules_store) ----
@app.get("/rules")
def get_rules():
    intervals = rules_store.list_intervals()
    # Also return new_max/used_all_max per ISBN
    rules = {}
    for isbn, secs in intervals.items():
        try:
            r = rules_store.get_rule(isbn)
            rules[isbn] = {"interval_seconds": r.interval_seconds, "new_max": r.new_max, "used_all_max": r.used_all_max}
        except Exception:
            rules[isbn] = {"interval_seconds": secs, "new_max": None, "used_all_max": None}
    return {"ok": True, "intervals": intervals, "rules": rules}


@app.get("/rules/{isbn}")
def get_isbn_rule(isbn: str):
    r = rules_store.get_rule(isbn)
    return {
        "ok": True,
        "isbn": isbn,
        "interval_seconds": r.interval_seconds,
        "new_max": r.new_max,
        "used_all_max": r.used_all_max,
    }


@app.put("/rules/{isbn}/interval")
def set_isbn_interval(isbn: str, payload: IntervalPayload):
    try:
        rules_store.set_interval(isbn, payload.interval_seconds)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {"ok": True, "isbn": isbn, "interval_seconds": payload.interval_seconds}


@app.put("/rules/{isbn}/override")
def set_isbn_override_endpoint(isbn: str, payload: OverridePayload):
    try:
        rules_store.set_override(isbn, new_max=payload.new_max, used_all_max=payload.used_all_max)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    r = rules_store.get_rule(isbn)
    return {
        "ok": True,
        "isbn": isbn,
        "new_max": r.new_max,
        "used_all_max": r.used_all_max,
    }


# ---- Alert stats & clear (panel dashboard) ----
from app import alert_store as _alert_store
from app import alert_history_store as _alert_history

@app.get("/alerts/stats")
def alerts_stats():
    return {"ok": True, "stats": _alert_store.get_stats()}

@app.get("/alerts/summary")
def alerts_summary():
    return {"ok": True, **_alert_history.get_summary()}

@app.get("/alerts/history")
def alerts_history(limit: int = 50, isbn: str | None = None):
    entries = _alert_history.get_history(limit=limit, isbn_filter=isbn)
    return {"ok": True, "entries": entries, "count": len(entries)}

@app.delete("/alerts/dedup/{isbn}")
def clear_dedup(isbn: str):
    """ISBN için dedup store'u temizle — bir sonraki scheduler çalışmasında yeniden alert gönderilir.
    NOT: history store'a dokunmaz; sadece notified.json'u temizler."""
    count = _alert_store.clear_isbn(isbn)
    return {"ok": True, "isbn": isbn, "dedup_cleared": count}


@app.delete("/alerts/{isbn}")
def clear_alerts(isbn: str):
    """Hem dedup store'u hem history store'u temizler."""
    _alert_store.clear_isbn(isbn)
    _alert_history.clear_isbn(isbn)
    return {"ok": True, "isbn": isbn}

# ── Alert details — drawer için, 15dk cache ──────────────────────────────────
import time as _time
_details_cache: dict = {}  # key: isbn → {ts, data}
_DETAILS_TTL = 900  # 15 dakika

@app.get("/alerts/details")
async def alert_details(isbn: str, ebay_item_id: str = ""):
    """
    Drawer için tek endpoint: eBay active stats + sold proxy + Amazon buybox.
    15 dakika cache — her tıklamada canlı çağrı yapmaz.
    """
    import traceback as _tb
    try:
        return await _alert_details_inner(isbn, ebay_item_id)
    except Exception as _exc:
        _tb.print_exc()
        return {"ok": False, "error": f"{type(_exc).__name__}: {_exc}"}


async def _alert_details_inner(isbn: str, ebay_item_id: str = ""):
    from app.ebay_client import browse_search_isbn, normalize_condition, item_total_price
    from app.core.config import get_settings as _gs
    from app import finding_cache

    isbn_clean = isbn.replace("-", "").replace(" ", "").strip()
    now = _time.time()

    cached = _details_cache.get(isbn_clean)
    if cached and now - cached["ts"] < _DETAILS_TTL:
        return {**cached["data"], "cached": True, "cache_age": int(now - cached["ts"])}

    s = _gs()
    calc_est = s.calculated_ship_estimate_usd if s.calculated_ship_estimate_usd > 0 else None

    # ── eBay active stats ─────────────────────────────────────────────────────
    ebay_data: dict = {"ok": False, "error": None}
    try:
        import httpx as _httpx
        async with _httpx.AsyncClient() as client:
            items = await browse_search_isbn(client, isbn_clean, limit=50, strict=False)

        buckets: dict = {}
        for it in items:
            total = item_total_price(it, calc_ship_est=calc_est)
            if total is None:
                continue
            bucket = normalize_condition(it.get("condition"), it.get("conditionId"))
            buckets.setdefault(bucket, []).append(round(float(total), 2))

        def _st(prices):
            if not prices: return None
            return {"count": len(prices), "min": round(min(prices), 2), "avg": round(sum(prices)/len(prices), 2)}

        _NEW = {"brand_new"}
        by_cond = {b: _st(p) for b, p in buckets.items() if _st(p)}
        new_p  = [p for b, ps in buckets.items() if b in _NEW  for p in ps]
        used_p = [p for b, ps in buckets.items() if b not in _NEW for p in ps]

        ebay_data = {
            "ok": True,
            "by_condition": by_cond,
            "new":  _st(new_p),
            "used": _st(used_p),
            "total_listings": sum(len(ps) for ps in buckets.values()),
        }
    except Exception as exc:
        ebay_data["error"] = str(exc)

    # ── Sold proxy / Finding status ───────────────────────────────────────────
    backoff = finding_cache.rate_limit_status()
    sold_data = {
        "data_source": "browse_proxy" if backoff.get("active") else "finding_api",
        "backoff_active": backoff.get("active", False),
        "backoff_remaining": int(backoff.get("remaining_seconds", 0)),
        "sold_avg": None,
        "sold_count": None,
    }
    # sold_stats_store'dan en güncel snapshot'ı çek
    try:
        from app import sold_stats_store as _sss
        v = _sss.query_window(isbn_clean, 90, "used")
        if v:
            sold_data["sold_avg"] = round(sum(v)/len(v), 2)
            sold_data["sold_count"] = len(v)
    except Exception:
        pass

    # ── Amazon buybox (SP-API, opsiyonel) ─────────────────────────────────────
    amazon_data: dict = {"available": False, "reason": "not_configured"}
    try:
        from app import amazon_client as _az
        # amazon_client'ın price endpoint'ini çağır — ASIN yok, ISBN'den lookup yapmıyoruz
        # Sadece "configured" olup olmadığını raporla
        az_cfg = s.amazon_sp_refresh_token if hasattr(s, "amazon_sp_refresh_token") else None
        if not az_cfg:
            amazon_data["reason"] = "not_configured"
        else:
            amazon_data["available"] = False
            amazon_data["reason"] = "isbn_to_asin_required"
            amazon_data["note"] = "ASIN gerekli — ISBN→ASIN lookup henüz otomatik değil"
    except Exception:
        amazon_data["reason"] = "module_error"

    # ── Profit simulation (if amazon available) ──────────────────────────────
    from app.profit_calc import calculate as _profit_calc, DEFAULT_FEES
    profit_data: dict | None = None

    # Use cheapest eBay active listing as cost basis
    _ebay_used_min = (ebay_data.get("used") or {}).get("min")
    _ebay_new_min  = (ebay_data.get("new")  or {}).get("min")
    _cost_basis    = _ebay_used_min or _ebay_new_min

    # Profit: calculate whenever we have a cost basis.
    # profit_calc returns None if Amazon sell price is unavailable — UI handles None gracefully.
    if _cost_basis:
        pr = _profit_calc(float(_cost_basis), amazon_data, DEFAULT_FEES)
        if pr:
            profit_data = pr.to_dict()
            profit_data["fees_config"] = {
                "referral_pct": DEFAULT_FEES.referral_pct,
                "closing_fee":  DEFAULT_FEES.closing_fee,
                "fulfillment":  DEFAULT_FEES.fulfillment,
                "inbound":      DEFAULT_FEES.inbound,
            }

    result = {
        "ok": True,
        "isbn": isbn_clean,
        "ebay": ebay_data,
        "sold": sold_data,
        "amazon": amazon_data,
        "profit": profit_data,
        "updated_at": int(now),
        "cached": False,
        "cache_age": 0,
    }
    _details_cache[isbn_clean] = {"ts": now, "data": result}
    return result


@app.post("/debug/inject-history")
def inject_test_history():
    """Test amaçlı — alert history'ye sahte entry ekler. UI'ı test etmek için kullan."""
    import time
    _alert_history.add_entry(
        isbn="TEST0000001",
        item_id="test-item-001",
        title="Test Book — Clean Code (Robert C. Martin)",
        condition="good",
        total=18.50,
        limit=30.00,
        decision="BUY",
        url="https://www.ebay.com/sch/i.html?_nkw=clean+code",
        image_url="",
        sold_avg=25,
        sold_count=12,
        ship_estimated=False,
        deal_score=62,
    )
    return {"ok": True, "msg": "Test entry injected. Check /alerts/history."}

# ---- Run state (scheduler son tarama zamanları, panel dashboard) ----
from app.core.json_store import _read_unsafe
from app.core.config import get_settings as _get_settings

@app.get("/run-state")
def run_state_endpoint():
    p = _get_settings().resolved_data_dir() / "last_run.json"
    data = _read_unsafe(p, default={"by_isbn": {}})
    return {"ok": True, "by_isbn": data.get("by_isbn", {})}


# ---- Suggested price router (panel pricing tab) ----
from app.suggested_price_endpoint import router as suggested_router
app.include_router(suggested_router)


# ---- Amazon SP-API: top2 new + used with A/M label ----
from app import amazon_client as _amz

@app.get("/amazon/prices/{asin}")
async def amazon_prices(asin: str):
    """Top 2 New + Used fiyatlar, A (FBA) / M (FBM) label ile."""
    try:
        data = await _amz.get_top2_prices(asin)
        return {"ok": True, **data}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

@app.get("/amazon/prices/{asin}/telegram")
async def amazon_prices_telegram(asin: str):
    """Telegram-ready formatted string."""
    try:
        data = await _amz.get_top2_prices(asin)
        return {"ok": True, "text": _amz.format_telegram(data)}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# ---- Link telemetry (broken eBay links) ----
@app.post("/telemetry/link-broken")
async def report_broken_link(request: Request):
    """
    Lightweight broken-link report from panel.
    Appends to data/link_telemetry.jsonl (newline-delimited JSON, append-only).
    Internal use only — no auth needed since on VPS private network.
    """
    import json, time
    from pathlib import Path as _P
    from app.core.config import get_settings as _gs

    try:
        body = await request.json()
    except Exception:
        body = {}

    entry = {
        "ts": int(time.time()),
        "isbn": str(body.get("isbn", ""))[:20],
        "url": str(body.get("url", ""))[:300],
        "context": str(body.get("context", ""))[:30],
        "build_id": str(body.get("build_id", ""))[:40],
        "user_agent": str(body.get("userAgent", ""))[:120],
    }

    try:
        out = _gs().resolved_data_dir() / "link_telemetry.jsonl"
        with open(out, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.warning("link_telemetry write failed: %s", e)

    return {"ok": True}


@app.get("/ebay/sold-avg/{isbn}")
@app.get("/ebay/sold/{isbn}")
async def ebay_sold_avg(isbn: str):
    """
    On-demand eBay sold price scraper.
    User-triggered only (button click). 30min cache per ISBN.
    Returns count/min/max/avg/median from completed sold listings.
    Alias: /ebay/sold/{isbn} for backward compatibility.
    """
    import traceback as _tb
    try:
        from app.sold_scraper import fetch_sold_avg
        return await fetch_sold_avg(isbn)
    except Exception as e:
        _tb.print_exc()
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@app.get("/bookfinder/{isbn}")
async def bookfinder_prices(isbn: str):
    """
    On-demand BookFinder.com price comparison.
    User-triggered only (button click). 30min cache per ISBN.
    Returns new/used offers from AbeBooks, Alibris, Biblio, BetterWorldBooks, etc.
    """
    import traceback as _tb
    try:
        from app.bookfinder_client import fetch_bookfinder
        return await fetch_bookfinder(isbn)
    except Exception as e:
        _tb.print_exc()
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@app.get("/telemetry/link-broken")
async def get_link_telemetry(limit: int = 50):
    """Read last N broken-link reports."""
    import json
    from app.core.config import get_settings as _gs
    out = _gs().resolved_data_dir() / "link_telemetry.jsonl"
    if not out.exists():
        return {"ok": True, "entries": [], "count": 0}
    lines = out.read_text(encoding="utf-8").splitlines()
    entries = []
    for line in lines[-limit:]:
        try:
            entries.append(json.loads(line))
        except Exception:
            pass
    return {"ok": True, "entries": list(reversed(entries)), "count": len(lines)}


# ---- Active listing stats ----
@app.get("/ebay/active-stats/{isbn}")
async def ebay_active_stats(isbn: str):
    """
    Active eBay listing stats for an ISBN.
    Returns per-condition count/min/avg and top cheapest items.
    """
    from app.ebay_client import browse_search_isbn, normalize_condition, item_total_price
    from app.core.config import get_settings as _gs
    import statistics, httpx

    s = _gs()
    calc_est = s.calculated_ship_estimate_usd if s.calculated_ship_estimate_usd > 0 else None

    async with httpx.AsyncClient() as client:
        try:
            items = await browse_search_isbn(client, isbn, limit=100, strict=False)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"eBay fetch failed: {e}")

    # Bucket all items
    _NEW_BUCKETS = {"brand_new"}
    buckets: dict = {}
    for it in items:
        total = item_total_price(it, calc_ship_est=calc_est)
        if total is None:
            continue
        bucket = normalize_condition(it.get("condition"), it.get("conditionId"))
        buckets.setdefault(bucket, []).append({
            "total": round(float(total), 2),
            "bucket": bucket,
            "itemId": it.get("itemId", ""),
            "title": (it.get("title") or "")[:80],
            "url": it.get("itemWebUrl") or "",
            "image": ((it.get("image") or {}).get("imageUrl") or ""),
        })

    def _stats(rows):
        prices = [r["total"] for r in rows]
        if not prices:
            return None
        return {
            "count": len(prices),
            "min": round(min(prices), 2),
            "avg": round(sum(prices)/len(prices), 2),
        }

    by_condition = {}
    for b, rows in buckets.items():
        st = _stats(rows)
        if st:
            by_condition[b] = st

    new_rows  = [r for b, rows in buckets.items() if b in _NEW_BUCKETS  for r in rows]
    used_rows = [r for b, rows in buckets.items() if b not in _NEW_BUCKETS for r in rows]

    overall = {}
    ns = _stats(new_rows)
    us = _stats(used_rows)
    if ns: overall["new"]  = ns
    if us: overall["used"] = us

    # Top cheapest 10 across all conditions
    all_rows = [r for rows in buckets.values() for r in rows]
    all_rows.sort(key=lambda r: r["total"])
    top_cheapest = all_rows[:10]

    return {
        "ok": True,
        "isbn": isbn,
        "source": "browse",
        "overall": overall,
        "by_condition": by_condition,
        "top_cheapest": top_cheapest,
    }


# ---- eBay debug (raw Browse results inspection) ----
from app.ebay_client import (
    browse_search_isbn as _browse_search,
    isbn_variants as _isbn_variants,
    item_total_price as _item_total_price,
    normalize_condition as _norm_cond,
    _isbn_strict_match as _strict_match,
)


@app.get("/ebay/debug/finding")
async def ebay_debug_finding(isbn: str, days: int = 30, condition: Optional[str] = None):
    """
    Ham Finding API (findCompletedItems) yanıtını döndürür — hata teşhisi için.

    Parametreler:
      isbn      : 10 veya 13 haneli ISBN
      days      : lookback gün (30 önerilen; >90 → tarih filtresi atlanır)
      condition : "new" | "used" | None

    Dönüş:
      http_status   : eBay'den gelen HTTP kodu
      ok            : 2xx ise True
      body_raw      : ham yanıt (hata ayıklama için)
      params_sent   : API'ye gönderilen query parametreleri (app_id maskelenir)
      parsed_count  : JSON parse edilebilirse kaç item döndü
    """
    from app.core.config import get_settings as _cfg
    from app.ebay_client import BOOKS_CATEGORY_ID as _CAT
    from datetime import datetime, timedelta, timezone

    s = _cfg()
    app_id = s.ebay_app_id or s.ebay_client_id
    if not app_id:
        raise HTTPException(status_code=503, detail="EBAY_APP_ID eksik")

    isbn_clean = isbn.replace("-", "").replace(" ", "").strip()
    now = datetime.now(timezone.utc)

    def _fmt(dt: datetime) -> str:
        return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    params: dict = {
        "OPERATION-NAME": "findCompletedItems",
        "SERVICE-VERSION": "1.13.0",
        "SECURITY-APPNAME": app_id,
        "RESPONSE-DATA-FORMAT": "JSON",
        "REST-PAYLOAD": "true",
        "keywords": isbn_clean,
        "categoryId": _CAT,
        "paginationInput.entriesPerPage": "10",
        "itemFilter(0).name": "SoldItemsOnly",
        "itemFilter(0).value": "true",
    }

    fi = 1
    if days <= 90:
        start = now - timedelta(days=days)
        params[f"itemFilter({fi}).name"] = "EndTimeFrom"
        params[f"itemFilter({fi}).value"] = _fmt(start)
        fi += 1
        params[f"itemFilter({fi}).name"] = "EndTimeTo"
        params[f"itemFilter({fi}).value"] = _fmt(now)
        fi += 1

    if condition == "new":
        params[f"itemFilter({fi}).name"] = "Condition"
        params[f"itemFilter({fi}).value"] = "New"
    elif condition == "used":
        params[f"itemFilter({fi}).name"] = "Condition"
        for i, v in enumerate(["Used", "Good", "Very Good", "Acceptable", "Like New"]):
            params[f"itemFilter({fi}).value({i})"] = v

    # Maskelenmiş params (app_id gizle)
    safe_params = {k: ("***" if k == "SECURITY-APPNAME" else v) for k, v in params.items()}

    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get("https://svcs.ebay.com/services/search/FindingService/v1", params=params)

    body_text = r.text
    parsed = None
    parsed_count = None
    try:
        parsed = r.json()
        resp0 = (parsed.get("findCompletedItemsResponse") or [{}])[0]
        sr = (resp0.get("searchResult") or [{}])[0]
        parsed_count = len(sr.get("item") or [])
    except Exception:
        pass

    return {
        "http_status": r.status_code,
        "ok": r.is_success,
        "isbn": isbn_clean,
        "days_requested": days,
        "date_filter_applied": days <= 90,
        "condition": condition,
        "params_sent": safe_params,
        "parsed_count": parsed_count,
        "body_raw": body_text[:3000],
    }


@app.get("/ebay/debug/finding-backoff")
async def ebay_debug_finding_backoff_status():
    """Finding API rate-limit backoff durumu."""
    from app import finding_cache as _fc
    return _fc.rate_limit_status()


@app.delete("/ebay/debug/finding-backoff")
async def ebay_debug_finding_backoff_clear():
    """Finding API rate-limit backoff'u temizle."""
    from app import finding_cache as _fc
    _fc.clear_rate_limit()
    return {"cleared": True, "status": _fc.rate_limit_status()}


@app.get("/ebay/debug/search")
async def ebay_debug_search(isbn: str, limit: int = 5, strict: bool = False):
    """
    Ham Browse API sonuçlarını döndürür — field keşfi ve filtre testi için.

    Parametreler:
      isbn  : 10 veya 13 haneli ISBN (zorunlu)
      limit : kaç item dönülsün (max 20, varsayılan 5)
      strict: strict_filter debug'ı için (varsayılan False)

    Dönüş:
      variants        : test edilen ISBN varyantları
      raw_count       : eBay'den dönen toplam item sayısı
      items           : her item için ham fields + hesaplanan değerler
    """
    limit = max(1, min(limit, 20))
    variants = _isbn_variants(isbn)

    async with httpx.AsyncClient(timeout=20) as client:
        raw_items = await _browse_search(client, isbn, limit=limit, strict=strict)

    items_out = []
    for it in raw_items:
        total = _item_total_price(it)
        bucket = _norm_cond(it.get("condition"), it.get("conditionId"))
        strict_pass = _strict_match(it, variants)

        items_out.append({
            # Kimlik / başlık
            "itemId": it.get("itemId"),
            "title": it.get("title"),
            # Fiyat
            "price": it.get("price"),
            "shippingOptions": it.get("shippingOptions"),
            "computed_total": total,
            # Condition
            "condition": it.get("condition"),
            "conditionId": it.get("conditionId"),
            "condition_bucket": bucket,
            # ISBN tanımlama (strict filter için)
            "gtin": it.get("gtin"),
            "epid": it.get("epid"),
            "isbn_field": it.get("isbn"),
            "localizedAspects": it.get("localizedAspects"),  # search'te genellikle None
            "strict_filter_pass": strict_pass,
            # Alım seçenekleri
            "buyingOptions": it.get("buyingOptions"),
            # Debug: tüm keys
            "_all_keys": sorted(it.keys()),
        })

    return {
        "ok": True,
        "isbn": isbn,
        "variants": variants,
        "raw_count": len(raw_items),
        "strict": strict,
        "items": items_out,
    }


# ---- SP-API offers proxy (legacy, nginx panel_api) ----
@app.get("/offers/top2")
async def offers_top2(asin: str, marketplace_id: str = "ATVPDKIKX0DER"):
    url = "http://127.0.0.1/spapi/offers/top2"
    params = {"asin": asin, "marketplace_id": marketplace_id}
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(url, params=params)
    try:
        return r.json()
    except Exception:
        return {"upstream_status": r.status_code, "body": r.text}


# ---- Static files: serve React panel build (production) ----
from pathlib import Path as _Path
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

_panel_dist = _Path(__file__).resolve().parent.parent / "panel" / "dist"
if _panel_dist.exists():
    app.mount("/assets", StaticFiles(directory=str(_panel_dist / "assets")), name="panel-assets")

    @app.get("/panel")
    @app.get("/panel/{rest:path}")
    def serve_panel(rest: str = ""):
        return FileResponse(str(_panel_dist / "index.html"))
