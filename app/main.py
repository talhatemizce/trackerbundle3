"""
TrackerBundle Domain API
========================
Bot'un (port 8000) bağlandığı ana API.
ISBN yönetimi, rules, watchlist endpointleri.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
import csv
import io
from typing import Dict, List, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from pydantic import BaseModel, Field

from app import isbn_store
from app import rules_store

app = FastAPI(title="TrackerBundle API", version="0.2.0")

# CORS (panel dev mode port 3000 + production)
from fastapi.middleware.cors import CORSMiddleware
import logging
import time as _time
import time
logger = logging.getLogger("trackerbundle.main")
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
    from app.core.config import get_settings as _gs2
    import time as _t
    s = _gs2()

    # BookFinder IP block durumu
    try:
        from app.bookfinder_client import _bookfinder_ip_blocked, _bookfinder_block_until
        bf_blocked = _bookfinder_ip_blocked and _t.time() < _bookfinder_block_until
        bf_block_remaining = max(0, int(_bookfinder_block_until - _t.time())) if bf_blocked else 0
    except Exception:
        bf_blocked = False
        bf_block_remaining = 0

    # eBay Browse API backoff durumu
    try:
        from app.ebay_client import _browse_backoff_until
        ebay_backoff = _t.time() < _browse_backoff_until
        ebay_backoff_remaining = max(0, int(_browse_backoff_until - _t.time())) if ebay_backoff else 0
    except Exception:
        ebay_backoff = False
        ebay_backoff_remaining = 0

    return {
        "ok": True,
        "service": "trackerbundle-api",
        "time_utc": datetime.now(timezone.utc).isoformat(),
        "has_bot_token": bool(os.getenv("TELEGRAM_BOT_TOKEN")),
        "isbn_count": len(isbns),
        "sched_tick_seconds": int(s.sched_tick_seconds),
        "bookfinder_enabled": getattr(s, "bookfinder_enabled", True),
        "bookfinder_ip_blocked": bf_blocked,
        "bookfinder_block_remaining_s": bf_block_remaining,
        "ebay_browse_backoff": ebay_backoff,
        "ebay_browse_backoff_remaining_s": ebay_backoff_remaining,
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
from app import smart_dedup as _smart_dedup

@app.get("/alerts/stats")
def alerts_stats():
    return {"ok": True, "stats": _smart_dedup.get_stats()}

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
    smart_dedup.json (aktif) + notified.json (legacy) ikisi de temizlenir."""
    smart_count = _smart_dedup.clear_isbn(isbn)
    legacy_count = _alert_store.clear_isbn(isbn)
    return {"ok": True, "isbn": isbn, "dedup_cleared": smart_count, "legacy_cleared": legacy_count}


@app.delete("/alerts/{isbn}")
def clear_alerts(isbn: str):
    """Hem dedup store'u hem history store'u temizler."""
    _smart_dedup.clear_isbn(isbn)  # aktif dedup store
    _alert_store.clear_isbn(isbn)  # legacy store
    _alert_history.clear_isbn(isbn)
    return {"ok": True, "isbn": isbn}

# ── Alert details — drawer için, disk-backed 30 günlük cache ─────────────────
_details_cache: dict = {}  # key: isbn → {ts, data} — in-memory, capped at 200 entries
_DETAILS_TTL     = 30 * 24 * 3600
_DETAILS_TTL_OK  = 30 * 24 * 3600
_DETAILS_TTL_ERR = 30 * 24 * 3600
_DETAILS_MAX     = 200  # max entries — LRU evict oldest when exceeded

def _details_cache_set(isbn: str, data: dict) -> None:
    global _details_cache
    _details_cache[isbn] = {"ts": time.time(), "data": data}
    # Evict oldest entries if over cap
    if len(_details_cache) > _DETAILS_MAX:
        oldest = sorted(_details_cache.items(), key=lambda x: x[1]["ts"])
        for k, _ in oldest[:len(_details_cache) - _DETAILS_MAX]:
            del _details_cache[k]

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
    # Stale cache var ama TTL geçmiş — eBay hata verirse stale döndür
    _stale = _details_cache.get(isbn_clean)

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
        # eBay hata verdi → stale cache'den eBay verisini al
        if _stale and _stale["data"].get("ebay", {}).get("ok"):
            _stale_ebay = _stale["data"]["ebay"]
            ebay_data = {**_stale_ebay, "stale": True, "stale_age_h": round((now - _stale["ts"])/3600, 1)}

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
    # ISBN-10 = ASIN for books on Amazon — use directly
    amazon_data: dict = {"available": False, "reason": "not_configured"}
    try:
        from app import amazon_client as _az
        az_cfg = s.lwa_refresh_token  # LWA_REFRESH_TOKEN env var
        if not az_cfg:
            amazon_data["reason"] = "not_configured"
        else:
            # ISBN-10 is 10 chars; ISBN-13 needs conversion. Try ISBN-10 directly as ASIN.
            asin_candidate = isbn_clean
            if len(isbn_clean) == 13 and isbn_clean.startswith("978"):
                core = isbn_clean[3:12]
                total = sum((10 - i) * int(c) for i, c in enumerate(core))
                check = (11 - (total % 11)) % 11
                asin_candidate = core + ("X" if check == 10 else str(check))
            try:
                az_result = await _az.get_top2_prices(asin_candidate)
                amazon_data = {
                    "available": True,
                    "asin": asin_candidate,
                    "new": az_result.get("new"),
                    "used": az_result.get("used"),
                }
            except Exception as az_exc:
                amazon_data = {"available": False, "reason": "api_error", "note": str(az_exc), "asin": asin_candidate}
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
    # Başarılı sonuç: cache'e kaydet (30 gün)
    # eBay stale ise cache timestamp'i güncelleme — sadece fresh eBay verisi timestamp yeniler
    _ebay_fresh = ebay_data.get("ok") and not ebay_data.get("stale")
    _cache_ts = now if _ebay_fresh else (_stale["ts"] if _stale else now)
    _details_cache_set(isbn_clean, result)
    return result


@app.post("/debug/inject-history")
def inject_test_history():
    if not os.getenv("DEBUG"):
        raise HTTPException(status_code=404, detail="Not found")
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

_amz_price_cache: dict = {}  # asin → {ts, data}
_AMZ_PRICE_TTL = 20 * 60  # 20 dakika

@app.get("/amazon/prices/{asin}")
async def amazon_prices(asin: str):
    """Top 2 New + Used fiyatlar — 20 dakika cache."""
    cached = _amz_price_cache.get(asin)
    if cached and time.time() - cached["ts"] < _AMZ_PRICE_TTL:
        return {"ok": True, **cached["data"], "cached": True}
    try:
        data = await _amz.get_top2_prices(asin)
        _amz_price_cache[asin] = {"ts": time.time(), "data": data}
        return {"ok": True, **data}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

@app.get("/amazon/prices/{asin}/telegram")
async def amazon_prices_telegram(asin: str):
    """Telegram-ready formatted string — 20 dakika cache."""
    cached = _amz_price_cache.get(asin)
    if cached and time.time() - cached["ts"] < _AMZ_PRICE_TTL:
        data = cached["data"]
    else:
        try:
            data = await _amz.get_top2_prices(asin)
            _amz_price_cache[asin] = {"ts": time.time(), "data": data}
        except Exception as e:
            raise HTTPException(status_code=502, detail=str(e))
    return {"ok": True, "text": _amz.format_telegram(data)}


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


@app.get("/bookfinder/debug/{isbn}")
async def bookfinder_debug(isbn: str):
    """Raw HTML debug — sunucudan BookFinder response'unu göster."""
    import random
    isbn_clean = isbn.replace("-","").replace(" ","").strip()
    urls = [
        f"https://www.bookfinder.com/isbn/{isbn_clean}/",
        f"https://www.bookfinder.com/search/?keywords={isbn_clean}&currency=USD&destination=us&mode=basic&lang=en&st=sh&ac=qr",
        f"https://www.bookfinder.com/search/?isbn={isbn_clean}&new_used=*&destination=us&currency=USD&mode=basic&st=sh&ac=qr",
    ]
    ua_list = [
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    ]
    results = []
    import httpx as _hx
    async with _hx.AsyncClient(follow_redirects=False, timeout=15) as c:
        for url in urls:
            for ua in ua_list[:1]:
                hdrs = {
                    "User-Agent": ua,
                    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept-Encoding": "gzip, deflate, br",
                    "Connection": "keep-alive",
                    "Upgrade-Insecure-Requests": "1",
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "none",
                    "Cache-Control": "max-age=0",
                }
                try:
                    r = await c.get(url, headers=hdrs)
                    results.append({
                        "url": url, "status": r.status_code,
                        "content_type": r.headers.get("content-type",""),
                        "location": r.headers.get("location",""),
                        "server": r.headers.get("server",""),
                        "cf_ray": r.headers.get("cf-ray",""),
                        "html_len": len(r.text),
                        "has_rsc": "__next_f" in r.text,
                        "has_offers": "newOffers" in r.text or "usedOffers" in r.text,
                        "html_preview": r.text[:800],
                    })
                except Exception as e:
                    results.append({"url": url, "error": str(e)})
    return {"isbn": isbn_clean, "results": results}


@app.get("/bookfinder/{isbn}")
async def bookfinder_prices(isbn: str, condition: str = "all", force: bool = False):
    """
    Multi-source book price comparison (8 sources, parallel).
    condition: all | new | used
    force: bypass cache
    Sources: BookFinder, AbeBooks, ThriftBooks, BetterWorldBooks,
             Biblio, Alibris, GoodwillBooks, HPB
    """
    import traceback as _tb
    try:
        from app.bookfinder_client import fetch_bookfinder
        return await fetch_bookfinder(isbn, condition=condition, force=force)
    except Exception as e:
        _tb.print_exc()
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@app.get("/buyback/{isbn}")
async def buyback_prices(isbn: str, force: bool = False):
    """
    BookScouter + BooksRun buyback fiyatlarını çek.
    En yüksek nakit teklif, tüm vendor listesi ve kâr hesabı döner.

    Kullanım:
      GET /buyback/9781565332751
      GET /buyback/9781565332751?force=true  (cache bypass)

    API keylerini /etc/trackerbundle.env'e ekle:
      BOOKSCOUTER_API_KEY=...   (api.bookscouter.com — ücretsiz kayıt)
      BOOKSRUN_API_KEY=...      (booksrun.com/page/api-reference — ücretsiz)
    """
    try:
        from app.buyback_client import fetch_buyback_prices
        return await fetch_buyback_prices(isbn, force=force)
    except Exception as e:
        logger.error("buyback error isbn=%s: %s", isbn, e)
        return {"ok": False, "error": str(e)}


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



# ── CSV Arbitrage Scanner ─────────────────────────────────────────────────────
from app.csv_arb_scanner import ScanFilters, scan_isbn_list, suggest_max_buy, IsbnMatchPolicy, InvalidIsbnPolicy
from app.profit_calc import FeeConfig

class CsvArbRequest(BaseModel):
    isbns: List[str] = Field(..., description="ISBN listesi (10 veya 13 hane)")
    strict_mode: bool = Field(default=True, description="True: NEW→NEW, USED→USED only")
    isbn_buy_prices: Optional[dict] = Field(default=None, description="Opsiyonel: {isbn: buy_price} — kullanıcı alım fiyatları")
    isbn_amazon_prices: Optional[dict] = Field(default=None, description="Opsiyonel: {asin: avg_price} — Amazon Business Report'tan hesaplanan ortalama satış fiyatı")
    min_roi_pct: Optional[float] = None
    max_roi_pct: Optional[float] = None
    min_profit_usd: Optional[float] = None
    min_amazon_price: Optional[float] = None
    max_amazon_price: Optional[float] = None
    min_buy_price: Optional[float] = None
    max_buy_price: Optional[float] = None
    max_buy_ratio_pct: Optional[float] = Field(default=None, description="Alım fiyatı amazon buybox'ının max %X'i olmalı (örn. 50 = max %50)")
    condition_in: Optional[List[str]] = None   # ["new"] | ["used"] | ["new","used"]
    source_in: Optional[List[str]] = None      # ["ebay","thriftbooks","abebooks",...]
    only_viable: bool = True
    concurrency: int = Field(default=5, ge=1, le=8)
    # ISBN match reliability policies
    isbn_match_policy: str = Field(default="balanced", description="precision|balanced|recall")
    invalid_isbn_policy: str = Field(default="best_effort", description="reject|best_effort")
    # Fee overrides (opsiyonel)
    fee_referral_pct: Optional[float] = None
    fee_closing: Optional[float] = None
    fee_fulfillment: Optional[float] = None
    fee_inbound: Optional[float] = None
    # Buyback filtresi
    buyback_only: bool = Field(default=False, description="Sadece buyback kanalında kârlı olanlar")
    min_buyback_profit: Optional[float] = Field(default=None, description="Min buyback kârı ($)")


@app.post("/discover/csv-arb")
async def csv_arb_scan(req: CsvArbRequest, background_tasks: BackgroundTasks):
    """
    ISBN listesini arka planda tara. job_id döner.
    İlerlemeyi /discover/csv-arb/progress/{job_id} ile takip et.
    """
    from app.scan_job_store import create_job, update_progress, finish_job, fail_job
    if not req.isbns:
        raise HTTPException(status_code=422, detail="ISBN listesi boş")
    if len(req.isbns) > 1000:
        raise HTTPException(status_code=422, detail="Max 1000 ISBN")

    filters = ScanFilters(
        min_roi_pct=req.min_roi_pct,
        max_roi_pct=req.max_roi_pct,
        min_profit_usd=req.min_profit_usd,
        min_amazon_price=req.min_amazon_price,
        max_amazon_price=req.max_amazon_price,
        min_buy_price=req.min_buy_price,
        max_buy_price=req.max_buy_price,
        max_buy_ratio_pct=req.max_buy_ratio_pct,
        condition_in=req.condition_in,
        source_in=req.source_in,
        only_viable=req.only_viable,   # Kullanıcının filtre seçimi korunuyor
        strict_mode=req.strict_mode,
        isbn_match_policy=IsbnMatchPolicy(req.isbn_match_policy),
        invalid_isbn_policy=InvalidIsbnPolicy(req.invalid_isbn_policy),
        buyback_only=req.buyback_only,
        min_buyback_profit=req.min_buyback_profit,
    )

    fees = FeeConfig(
        referral_pct=req.fee_referral_pct if req.fee_referral_pct is not None else 0.15,
        closing_fee=req.fee_closing if req.fee_closing is not None else 1.80,
        fulfillment=req.fee_fulfillment if req.fee_fulfillment is not None else 3.50,
        inbound=req.fee_inbound if req.fee_inbound is not None else 0.60,
    )

    # Kullanıcının gerçek filtre kriterleri (background'da uygulanacak)
    user_filters = dict(
        only_viable=req.only_viable,
        min_roi_pct=req.min_roi_pct,
        max_roi_pct=req.max_roi_pct,
        min_profit_usd=req.min_profit_usd,
        min_amazon_price=req.min_amazon_price,
        max_amazon_price=req.max_amazon_price,
        min_buy_price=req.min_buy_price,
        max_buy_price=req.max_buy_price,
    )

    # Aynı anda sadece 1 aktif job — 2. istek "queued" olarak döner
    from app.scan_job_store import _jobs as _all_jobs
    active = [j for j in _all_jobs.values() if j.get("status") == "running"]
    if active:
        return {
            "ok": False,
            "queued": True,
            "message": f"Şu an aktif bir tarama var ({active[0]['id']}). "
                       f"Tamamlanmasını bekle veya farklı bir sekme aç.",
            "active_job_id": active[0]["id"],
            "active_job_progress": f"{active[0].get('progress',0)}/{active[0].get('total',0)}",
        }

    job_id = create_job(len(req.isbns))

    async def _run():
        import time as _t
        from app.scan_job_store import _jobs, append_result
        _jobs[job_id]["status"] = "running"
        _jobs[job_id]["started_at"] = _t.time()
        try:
            def _on_isbn_done(done: int, total: int, new_accepted: list, new_rejected: list):
                """Her ISBN bittikçe çağrılır — anlık sonuçlar job'a eklenir."""
                update_progress(job_id, done)
                append_result(job_id, new_accepted, new_rejected)

            from app.scan_job_store import get_pause_event, get_cancel_event
            result = await scan_isbn_list(
                isbns=req.isbns,
                filters=filters,
                fees=fees,
                concurrency=req.concurrency,
                isbn_buy_prices=req.isbn_buy_prices or {},
                isbn_amazon_prices=req.isbn_amazon_prices or {},
                on_progress=_on_isbn_done,
                pause_event=get_pause_event(job_id),
                cancel_event=get_cancel_event(job_id),
            )
            # Post-filter: amazon_unavailable olanları göster ama ayrı tut
            all_accepted = result["accepted"]
            all_rejected = result["rejected"]
            stats = result["stats"]
            stats["amazon_unavailable"] = sum(1 for r in all_rejected if r.get("reason","").startswith("amazon_unavailable"))
            finish_job(job_id, all_accepted, all_rejected, stats)
        except Exception as e:
            # Tarama yarıda kalsın — partial_accepted/rejected korunur
            from app.scan_job_store import _jobs
            partial_acc = _jobs.get(job_id, {}).get("partial_accepted", [])
            partial_rej = _jobs.get(job_id, {}).get("partial_rejected", [])
            if partial_acc or partial_rej:
                # Partial sonuçları final olarak kaydet — kullanıcı kaybetmesin
                finish_job(job_id, partial_acc, partial_rej, {"partial": True, "error": str(e)[:200]})
            else:
                fail_job(job_id, str(e))
            logger.error("csv_arb job %s failed: %s", job_id, e)

    background_tasks.add_task(_run)
    # Tahmini süre: ~4s/ISBN ÷ concurrency
    est = round(len(req.isbns) * 4 / req.concurrency)
    return {"ok": True, "job_id": job_id, "total": len(req.isbns), "estimated_seconds": est}


@app.get("/discover/csv-arb/progress/{job_id}")
async def csv_arb_progress(job_id: str):
    """Job ilerleme durumu — her 1-2 saniyede poll et."""
    from app.scan_job_store import get_job_progress
    prog = get_job_progress(job_id)
    if not prog:
        raise HTTPException(status_code=404, detail="Job bulunamadı")
    return {"ok": True, **prog}


@app.get("/discover/csv-arb/result/{job_id}")
async def csv_arb_result(job_id: str):
    """Tamamlanmış job'un tam sonucu."""
    from app.scan_job_store import get_job
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job bulunamadı")
    if job["status"] != "done":
        raise HTTPException(status_code=409, detail=f"Job henüz bitmedi: {job['status']}")
    return {"ok": True, "accepted": job["accepted"], "rejected": job["rejected"], "stats": job["stats"]}


@app.post("/discover/csv-arb/pause/{job_id}")
async def csv_arb_pause(job_id: str):
    from app.scan_job_store import pause_job
    ok = pause_job(job_id)
    if not ok:
        raise HTTPException(status_code=409, detail="Job duraklatılamadı (bulunamadı veya zaten bitti)")
    return {"ok": True, "status": "paused"}

@app.post("/discover/csv-arb/resume/{job_id}")
async def csv_arb_resume(job_id: str):
    from app.scan_job_store import resume_job
    ok = resume_job(job_id)
    if not ok:
        raise HTTPException(status_code=409, detail="Job devam ettirilemedi (bulunamadı veya paused değil)")
    return {"ok": True, "status": "running"}

@app.post("/discover/csv-arb/cancel/{job_id}")
async def csv_arb_cancel(job_id: str):
    from app.scan_job_store import cancel_job, _jobs
    ok = cancel_job(job_id)
    if not ok:
        raise HTTPException(status_code=409, detail="Job iptal edilemedi")
    # Partial sonuçları döndür
    job = _jobs.get(job_id, {})
    return {
        "ok": True,
        "status": "cancelled",
        "accepted": job.get("partial_accepted", []),
        "rejected": job.get("partial_rejected", [])[:50],
        "stats": {"cancelled": True, "done": job.get("progress", 0), "total": job.get("total", 0)},
    }

@app.get("/discover/history")
async def scan_history():
    """Geçmiş tarama sonuçları."""
    from app.scan_job_store import get_history
    return {"ok": True, "history": get_history()}


@app.get("/analytics/summary")
async def analytics_summary():
    """
    Metabase / monitoring için operasyonel özet.
    Scan geçmişi, buyback istatistikleri, hata oranları.
    """
    import time as _time
    from app.scan_job_store import get_history
    from app.alert_history_store import get_entries as get_alert_history
    from app.isbn_store import get_isbns

    history = get_history()
    isbns   = get_isbns()

    # Son 7 günün tarama istatistikleri
    cutoff   = _time.time() - 7 * 86400
    recent   = [h for h in history if h.get("ts", 0) >= cutoff]
    total_accepted = sum(len(h.get("accepted", [])) for h in recent)
    total_scanned  = sum((h.get("stats") or {}).get("total_isbns", 0) for h in recent)

    # Alert istatistikleri (son 7 gün)
    try:
        all_alerts = get_alert_history()
        recent_alerts = [a for a in all_alerts if (a.get("ts") or 0) >= cutoff]
    except Exception:
        recent_alerts = []

    return {
        "ok": True,
        "ts": int(_time.time()),
        "watchlist_size": len(isbns),
        "scans_last_7d": len(recent),
        "isbns_scanned_7d": total_scanned,
        "deals_found_7d": total_accepted,
        "alerts_last_7d": len(recent_alerts),
        "total_scans": len(history),
        "last_scan_ts": history[0].get("ts") if history else None,
    }


@app.get("/discover/nyt-suggestions")
async def nyt_watchlist_suggestions():
    """
    NYT Bestseller listelerinden watchlist önerileri.
    Güncel bestseller'ların ISBN listesini döndürür.
    NYT_API_KEY env var gerekli — developer.nytimes.com (ücretsiz)
    """
    try:
        from app.nyt_client import get_watchlist_suggestions
        books = await get_watchlist_suggestions(max_per_list=5)
        return {"ok": True, "count": len(books), "books": books}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.get("/discover/nyt-check/{isbn}")
async def nyt_isbn_check(isbn: str):
    """ISBN'in NYT bestseller geçmişini kontrol et."""
    try:
        from app.nyt_client import get_isbn_nyt_history
        data = await get_isbn_nyt_history(isbn)
        return {"ok": True, "isbn": isbn, **data}
    except Exception as e:
        return {"ok": False, "error": str(e)}


class MaxBuyRequest(BaseModel):
    sell_price: float = Field(..., gt=0)
    target_roi_pct: float = Field(default=30.0, gt=0)
    fee_referral_pct: Optional[float] = None
    fee_closing: Optional[float] = None
    fee_fulfillment: Optional[float] = None
    fee_inbound: Optional[float] = None


@app.post("/discover/suggest-max-buy")
def suggest_max_buy_endpoint(req: MaxBuyRequest):
    """Amazon satış fiyatı + hedef ROI verilen bir alım için max eBay alım fiyatı hesapla."""
    fees = FeeConfig(
        referral_pct=req.fee_referral_pct or 0.15,
        closing_fee=req.fee_closing or 1.80,
        fulfillment=req.fee_fulfillment or 3.50,
        inbound=req.fee_inbound or 0.60,
    )
    max_buy = suggest_max_buy(req.sell_price, req.target_roi_pct, fees)
    if max_buy is None:
        return {"ok": False, "reason": "sell_price_too_low_after_fees"}
    referral = max(1.00, req.sell_price * fees.referral_pct)
    total_fees = referral + fees.closing_fee + fees.fulfillment + fees.inbound
    return {
        "ok": True,
        "sell_price": req.sell_price,
        "target_roi_pct": req.target_roi_pct,
        "max_buy_price": max_buy,
        "total_fees": round(total_fees, 2),
        "net_after_fees": round(req.sell_price - total_fees, 2),
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


# ---- AI ISBN Analyst ----
class AiAnalyzeRequest(BaseModel):
    isbn: str
    candidate: Dict[str, Any] = {}

# ── Simple in-process rate limiter ──────────────────────────────────────────
import time as _time
_ai_requests: list = []          # timestamps of recent /ai/analyze calls
_AI_RATE_WINDOW = 60             # seconds
_AI_RATE_MAX = 20                # max calls per window

def _ai_rate_check() -> bool:
    """True = allowed, False = rate limited."""
    now = _time.time()
    # Evict old
    while _ai_requests and _ai_requests[0] < now - _AI_RATE_WINDOW:
        _ai_requests.pop(0)
    if len(_ai_requests) >= _AI_RATE_MAX:
        return False
    _ai_requests.append(now)
    return True

@app.post("/ai/analyze")
async def ai_analyze(req: AiAnalyzeRequest):
    if not _ai_rate_check():
        raise HTTPException(status_code=429, detail=f"Rate limit: max {_AI_RATE_MAX} AI calls per {_AI_RATE_WINDOW}s")
    from app.ai_analyst import analyze_isbn
    try:
        result = await asyncio.wait_for(analyze_isbn(req.isbn, req.candidate), timeout=90.0)
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        logger.error("AI analyze error isbn=%s: %s", req.isbn, e)
        raise HTTPException(status_code=500, detail=str(e))


# ─── LLM Status ──────────────────────────────────────────────────────────────

@app.get("/llm/status")
async def llm_status():
    """Tüm LLM providerların kota ve durum bilgisi."""
    from app.llm_router import get_status
    return get_status()


# ─── Listing Verify endpoints ─────────────────────────────────────────────────

class VerifyRequest(BaseModel):
    isbn: str
    candidate: Dict[str, Any]


class VerifyBatchRequest(BaseModel):
    items: List[Dict[str, Any]]  # [{"isbn": str, "candidate": {...}, "_index": int}, ...]
    concurrency: int = Field(default=4, ge=1, le=8)


@app.post("/verify/listing")
async def verify_listing_endpoint(req: VerifyRequest):
    """
    Tek bir arbitraj ilanını doğrula (AI yok, saf API).
    eBay item hâlâ var mı? Fiyat değişti mi? ISBN uyuşuyor mu?
    """
    from app.listing_verifier import verify_listing
    try:
        result = await verify_listing(req.candidate, req.isbn)
        return result
    except Exception as e:
        logger.error("verify_listing error isbn=%s: %s", req.isbn, e)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/verify/batch")
async def verify_batch_endpoint(req: VerifyBatchRequest):
    """
    Birden fazla ilanı toplu doğrula (paralel, AI yok).
    items: [{"isbn": str, "candidate": {...}, "_index": int}, ...]
    """
    from app.listing_verifier import verify_batch
    if len(req.items) > 50:
        raise HTTPException(status_code=422, detail="Max 50 item per batch")
    try:
        results = await verify_batch(req.items, concurrency=req.concurrency)
        summary = {
            "verified": sum(1 for r in results if r.get("status") == "VERIFIED"),
            "gone": sum(1 for r in results if r.get("status") == "GONE"),
            "price_up": sum(1 for r in results if r.get("status") == "PRICE_UP"),
            "price_down": sum(1 for r in results if r.get("status") == "PRICE_DOWN"),
            "mismatch": sum(1 for r in results if r.get("status") == "MISMATCH"),
            "error": sum(1 for r in results if r.get("status") == "ERROR"),
            "total": len(results),
        }
        return {"results": results, "summary": summary}
    except Exception as e:
        logger.error("verify_batch error: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


# ── BookDepot Inventory ───────────────────────────────────────────────────────

class BookDepotItem(BaseModel):
    isbn: str
    title: str = ""
    price: Optional[float] = None
    qty: Optional[str] = None
    url: str = ""

class BookDepotImportPayload(BaseModel):
    items: List[BookDepotItem]
    page_url: str = ""

@app.post("/bookdepot/import")
async def bookdepot_import(payload: BookDepotImportPayload):
    """BookDepot bookmarklet'ten gelen scraped data'yı JSON store'a kaydeder."""
    from app.core.json_store import file_lock, _read_unsafe, _write_unsafe
    from app.core.config import get_settings

    items = payload.items
    if not items:
        return {"ok": False, "error": "no_items"}

    p = get_settings().resolved_data_dir() / "bookdepot_inventory.json"
    inserted = 0
    now = int(_time.time())

    with file_lock(p):
        data = _read_unsafe(p, default={"items": {}, "updated_at": 0})
        store = data.get("items", {})
        for item in items:
            isbn = item.isbn.replace("-", "").replace(" ", "").strip()
            if len(isbn) < 10:
                continue
            store[isbn] = {
                "isbn": isbn,
                "title": item.title,
                "price": item.price,
                "qty": item.qty,
                "url": item.url,
                "scraped_at": now,
            }
            inserted += 1
        data["items"] = store
        data["updated_at"] = now
        _write_unsafe(p, data)

    return {"ok": True, "inserted": inserted, "total": len(store)}


@app.get("/bookdepot/inventory")
async def bookdepot_inventory(min_price: Optional[float] = None, max_price: Optional[float] = None):
    """Kayıtlı BookDepot envanterini döndürür. Opsiyonel fiyat filtreleri."""
    from app.core.json_store import _read_unsafe
    from app.core.config import get_settings

    p = get_settings().resolved_data_dir() / "bookdepot_inventory.json"
    data = _read_unsafe(p, default={"items": {}})
    items = list(data.get("items", {}).values())

    if min_price is not None:
        items = [i for i in items if i.get("price") is not None and i["price"] >= min_price]
    if max_price is not None:
        items = [i for i in items if i.get("price") is not None and i["price"] <= max_price]

    items.sort(key=lambda x: x.get("scraped_at", 0), reverse=True)
    return {"ok": True, "count": len(items), "items": items}


class BookDepotScanRequest(BaseModel):
    isbns: Optional[List[str]] = None                    # manuel override — envanter yerine bu listeyi tara
    isbn_buy_prices: Optional[Dict[str, float]] = None   # manuel fiyatlar (isbns ile birlikte kullan)
    min_roi_pct: Optional[float] = None
    max_roi_pct: Optional[float] = None
    min_profit_usd: Optional[float] = None
    min_amazon_price: Optional[float] = None
    max_amazon_price: Optional[float] = None
    compare_with: str = "used"                           # "new" | "used" — Amazon hangi fiyatla karşılaştır
    amazon_condition_in: Optional[List[str]] = None      # ["acceptable","good","very_good","like_new"]
    condition_in: Optional[List[str]] = None
    only_viable: bool = True
    concurrency: int = Field(default=5, ge=1, le=8)

@app.post("/bookdepot/scan")
async def bookdepot_scan(req: BookDepotScanRequest, background_tasks: BackgroundTasks):
    """BookDepot envanterindeki ISBN'leri Amazon fiyatlarıyla karşılaştır."""
    from app.core.json_store import _read_unsafe
    from app.core.config import get_settings
    from app.scan_job_store import create_job, update_progress, finish_job, fail_job, _jobs as _all_jobs
    from app.csv_arb_scanner import scan_isbn_list, ScanFilters, IsbnMatchPolicy, InvalidIsbnPolicy
    from app.profit_calc import FeeConfig, DEFAULT_FEES

    p = get_settings().resolved_data_dir() / "bookdepot_inventory.json"
    data = _read_unsafe(p, default={"items": {}})
    inv_items = data.get("items", {})

    # Manuel ISBN listesi varsa onu kullan, yoksa envanterden al
    if req.isbns:
        isbns = [i.strip() for i in req.isbns if i.strip()][:1000]
        # Manuel fiyatlar varsa kullan, yoksa envanterdeki fiyatlara bak
        isbn_buy_prices = dict(req.isbn_buy_prices) if req.isbn_buy_prices else {}
        for isbn in isbns:
            if isbn not in isbn_buy_prices and isbn in inv_items:
                price = inv_items[isbn].get("price")
                if price and price > 0:
                    isbn_buy_prices[isbn] = price
    else:
        if not inv_items:
            raise HTTPException(status_code=422, detail="BookDepot envanteri boş — önce bookmarklet ile kitap kazıyın veya manuel ISBN girin")
        isbns = list(inv_items.keys())[:1000]
        isbn_buy_prices = {}
        for isbn, item in inv_items.items():
            if item.get("price") and item["price"] > 0:
                isbn_buy_prices[isbn] = item["price"]

    if not isbns:
        raise HTTPException(status_code=422, detail="Taranacak ISBN bulunamadı")

    # Check for active jobs
    active = [j for j in _all_jobs.values() if j.get("status") == "running"]
    if active:
        return {
            "ok": False,
            "queued": True,
            "message": f"Aktif tarama var ({active[0]['id']}). Bitmesini bekleyin.",
            "active_job_id": active[0]["id"],
        }

    # compare_with: "new" → strict_mode=False (USED kitap → NEW Amazon fiyatıyla karşılaştır)
    # compare_with: "used" → strict_mode=True (USED → USED Amazon fiyatıyla karşılaştır)
    strict_mode = req.compare_with != "new"

    # csv_input her ISBN için hem "new" hem "used" offer ekler.
    # compare_with="used" → sadece "used" condition offer'ı tara (new variant'ı filtrele)
    # compare_with="new"  → sadece "new" condition offer'ı tara (used variant'ı filtrele)
    # Kullanıcı condition_in açıkça gönderdiyse onu kullan.
    effective_condition_in = req.condition_in if req.condition_in else [req.compare_with]

    filters = ScanFilters(
        min_roi_pct=req.min_roi_pct,
        max_roi_pct=req.max_roi_pct,
        min_profit_usd=req.min_profit_usd,
        min_amazon_price=req.min_amazon_price,
        max_amazon_price=req.max_amazon_price,
        condition_in=effective_condition_in,
        amazon_condition_in=req.amazon_condition_in,
        only_viable=req.only_viable,
        strict_mode=strict_mode,
        isbn_match_policy=IsbnMatchPolicy.BALANCED,
        invalid_isbn_policy=InvalidIsbnPolicy.BEST_EFFORT,
    )

    job_id = create_job(len(isbns))

    async def _run():
        import time as _t
        from app.scan_job_store import _jobs, append_result, get_pause_event, get_cancel_event
        _jobs[job_id]["status"] = "running"
        _jobs[job_id]["started_at"] = _t.time()
        try:
            def _on_done(done, total, new_acc, new_rej):
                update_progress(job_id, done)
                append_result(job_id, new_acc, new_rej)

            result = await scan_isbn_list(
                isbns=isbns,
                filters=filters,
                fees=DEFAULT_FEES,
                concurrency=req.concurrency,
                isbn_buy_prices=isbn_buy_prices,
                on_progress=_on_done,
                pause_event=get_pause_event(job_id),
                cancel_event=get_cancel_event(job_id),
            )
            stats = result["stats"]
            stats["source"] = "bookdepot"
            finish_job(job_id, result["accepted"], result["rejected"], stats)
        except Exception as e:
            from app.scan_job_store import _jobs
            partial_acc = _jobs.get(job_id, {}).get("partial_accepted", [])
            partial_rej = _jobs.get(job_id, {}).get("partial_rejected", [])
            if partial_acc or partial_rej:
                finish_job(job_id, partial_acc, partial_rej, {"partial": True, "error": str(e)[:200], "source": "bookdepot"})
            else:
                fail_job(job_id, str(e))
            logger.error("bookdepot_scan job %s failed: %s", job_id, e)

    background_tasks.add_task(_run)
    est = round(len(isbns) * 4 / req.concurrency)
    return {"ok": True, "job_id": job_id, "total": len(isbns), "estimated_seconds": est}


@app.delete("/bookdepot/inventory")
async def bookdepot_clear():
    """Tüm BookDepot envanterini temizler."""
    from app.core.json_store import file_lock, _write_unsafe
    from app.core.config import get_settings

    p = get_settings().resolved_data_dir() / "bookdepot_inventory.json"
    with file_lock(p):
        _write_unsafe(p, {"items": {}, "updated_at": int(_time.time())})
    return {"ok": True, "message": "Envanter temizlendi"}


# ── BookDepot Watchlist ───────────────────────────────────────────────────────

def _bd_wl_path():
    from app.core.config import get_settings
    return get_settings().resolved_data_dir() / "bookdepot_watchlist.json"

@app.get("/bookdepot/watchlist")
async def bookdepot_watchlist_get():
    """BookDepot watchlist'ini döndür."""
    from app.core.json_store import _read_unsafe
    data = _read_unsafe(_bd_wl_path(), default={"items": {}})
    items = list(data.get("items", {}).values())
    items.sort(key=lambda x: x.get("added_at", 0), reverse=True)
    return {"ok": True, "count": len(items), "items": items}

class BdWatchlistAddRequest(BaseModel):
    isbn: str
    asin: Optional[str] = None
    title: str = ""
    buy_price: Optional[float] = None
    amazon_price: Optional[float] = None
    profit: Optional[float] = None
    roi_pct: Optional[float] = None
    roi_tier: Optional[str] = None
    bsr: Optional[int] = None
    ebay_url: str = ""
    source: str = ""

@app.post("/bookdepot/watchlist")
async def bookdepot_watchlist_add(req: BdWatchlistAddRequest):
    """BookDepot watchlist'ine ISBN ekle."""
    from app.core.json_store import file_lock, _read_unsafe, _write_unsafe
    isbn = req.isbn.strip()
    if not isbn:
        raise HTTPException(status_code=422, detail="ISBN gerekli")
    p = _bd_wl_path()
    with file_lock(p):
        data = _read_unsafe(p, default={"items": {}})
        already = isbn in data["items"]
        data["items"][isbn] = {
            "isbn": isbn,
            "asin": req.asin,
            "title": req.title,
            "buy_price": req.buy_price,
            "amazon_price": req.amazon_price,
            "profit": req.profit,
            "roi_pct": req.roi_pct,
            "roi_tier": req.roi_tier,
            "bsr": req.bsr,
            "ebay_url": req.ebay_url,
            "source": req.source,
            "added_at": int(_time.time()),
        }
        _write_unsafe(p, data)
    return {"ok": True, "added": not already, "total": len(data["items"])}

@app.delete("/bookdepot/watchlist/{isbn}")
async def bookdepot_watchlist_remove(isbn: str):
    """BookDepot watchlist'inden ISBN sil."""
    from app.core.json_store import file_lock, _read_unsafe, _write_unsafe
    p = _bd_wl_path()
    with file_lock(p):
        data = _read_unsafe(p, default={"items": {}})
        removed = isbn in data["items"]
        data["items"].pop(isbn, None)
        _write_unsafe(p, data)
    return {"ok": True, "removed": removed}

@app.delete("/bookdepot/watchlist")
async def bookdepot_watchlist_clear():
    """BookDepot watchlist'ini tamamen temizle."""
    from app.core.json_store import file_lock, _write_unsafe
    p = _bd_wl_path()
    with file_lock(p):
        _write_unsafe(p, {"items": {}, "updated_at": int(_time.time())})
    return {"ok": True}



async def bookdepot_delete_unprofitable(job_id: str):
    """Verilen tarama job'undaki reddedilen ISBN'leri envanterden sil."""
    from app.scan_job_store import _jobs
    from app.core.json_store import file_lock, _read_unsafe, _write_unsafe
    from app.core.config import get_settings

    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job bulunamadı veya süresi doldu")

    rejected = job.get("rejected") or job.get("partial_rejected") or []
    rejected_isbns = {r.get("isbn") for r in rejected if r.get("isbn")}
    if not rejected_isbns:
        return {"ok": True, "deleted": 0, "message": "Silinecek ISBN yok"}

    p = get_settings().resolved_data_dir() / "bookdepot_inventory.json"
    with file_lock(p):
        data = _read_unsafe(p, default={"items": {}})
        items = data.get("items", {})
        before = len(items)
        items = {k: v for k, v in items.items() if k not in rejected_isbns}
        data["items"] = items
        data["updated_at"] = int(_time.time())
        _write_unsafe(p, data)

    deleted = before - len(items)
    return {"ok": True, "deleted": deleted, "remaining": len(items)}


@app.get("/bookdepot/history")
async def bookdepot_history():
    """BookDepot tarama geçmişi."""
    from app.scan_job_store import get_history
    all_h = get_history()
    bd_h = [h for h in all_h if h.get("stats", {}).get("source") == "bookdepot"]
    return {"ok": True, "history": bd_h}


@app.get("/bookdepot/setup")
async def bookdepot_setup():
    """BookDepot bookmarklet kurulum sayfası."""
    from fastapi.responses import FileResponse as _FR
    import pathlib as _pl
    p = _pl.Path(__file__).parent.parent / "bookdepot_bookmarklet.html"
    return _FR(str(p), media_type="text/html")


# ---- Static files: serve React panel build (production) ----
from pathlib import Path as _Path
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

_panel_dist = _Path(__file__).resolve().parent.parent / "panel" / "dist"
if _panel_dist.exists():
    app.mount("/assets", StaticFiles(directory=str(_panel_dist / "assets")), name="panel-assets")

    @app.get("/panel")
    @app.get("/panel/{rest:path}")
    @app.get("/")
    def serve_panel(rest: str = ""):
        from fastapi.responses import Response
        content = (_panel_dist / "index.html").read_bytes()
        return Response(content=content, media_type="text/html", headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
        })
