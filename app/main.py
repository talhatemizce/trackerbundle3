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
from fastapi import FastAPI, HTTPException
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

    added = isbn_store.add_isbn(isbn)
    count = len(isbn_store.list_isbns())
    return {"ok": True, "added": added, "isbn": isbn, "count": count}


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
    return {"ok": True, "intervals": rules_store.list_intervals()}


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

@app.get("/alerts/stats")
def alerts_stats():
    return {"ok": True, "stats": _alert_store.get_stats()}

@app.delete("/alerts/{isbn}")
def clear_alerts(isbn: str):
    count = _alert_store.clear_isbn(isbn)
    return {"ok": True, "isbn": isbn, "cleared": count}


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
