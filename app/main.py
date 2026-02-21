"""
TrackerBundle Domain API
========================
Bot'un (port 8000) bağlandığı ana API.
ISBN yönetimi, rules, watchlist endpointleri.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

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
    rules_store.set_interval(isbn, payload.interval_seconds)
    return {"ok": True, "isbn": isbn, "interval_seconds": payload.interval_seconds}


@app.put("/rules/{isbn}/override")
def set_isbn_override_endpoint(isbn: str, payload: OverridePayload):
    rules_store.set_override(isbn, new_max=payload.new_max, used_all_max=payload.used_all_max)
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
