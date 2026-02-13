from __future__ import annotations

import os
import json
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
import base64
import httpx

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
TOKEN_FILE = DATA_DIR / "ebay_app_token.json"

EBAY_ENV = os.getenv("EBAY_ENV", "production").strip().lower()
EBAY_CLIENT_ID = os.getenv("EBAY_CLIENT_ID", "").strip()
EBAY_CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET", "").strip()
EBAY_APP_ID = os.getenv("EBAY_APP_ID", "").strip()  # Finding API

def _browse_base() -> str:
    # production: https://api.ebay.com , sandbox: https://api.sandbox.ebay.com
    return "https://api.sandbox.ebay.com" if EBAY_ENV == "sandbox" else "https://api.ebay.com"

def _safe_int(x: Any) -> Optional[int]:
    try:
        return int(round(float(x)))
    except Exception:
        return None

def _read_token() -> Optional[Dict[str, Any]]:
    try:
        if TOKEN_FILE.exists():
            return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None
    return None

def _write_token(data: Dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

def _token_valid(tok: Dict[str, Any]) -> bool:
    # tok: {"access_token": "...", "expires_at": epoch_seconds}
    try:
        return bool(tok.get("access_token")) and int(tok.get("expires_at", 0)) > int(time.time()) + 60
    except Exception:
        return False

async def get_app_token(client: httpx.AsyncClient) -> str:
    tok = _read_token()
    if tok and _token_valid(tok):
        return str(tok["access_token"])

    if not EBAY_CLIENT_ID or not EBAY_CLIENT_SECRET:
        raise RuntimeError("EBAY_CLIENT_ID/EBAY_CLIENT_SECRET not set")

    auth = base64.b64encode(f"{EBAY_CLIENT_ID}:{EBAY_CLIENT_SECRET}".encode("utf-8")).decode("ascii")

    url = f"{_browse_base()}/identity/v1/oauth2/token"
    headers = {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {
        "grant_type": "client_credentials",
        "scope": "https://api.ebay.com/oauth/api_scope",
    }

    r = await client.post(url, headers=headers, data=data)
    r.raise_for_status()
    j = r.json()
    access_token = j.get("access_token")
    expires_in = int(j.get("expires_in", 0))  # seconds
    if not access_token or expires_in <= 0:
        raise RuntimeError("Could not obtain eBay app token")

    tok = {
        "access_token": access_token,
        "expires_at": int(time.time()) + expires_in,
    }
    _write_token(tok)
    return access_token

async def browse_get_item(client: httpx.AsyncClient, item_id: str) -> Dict[str, Any]:
    """
    Returns normalized payload:
    {site, item_id, title, url, price, ship, total, currency, available}
    All prices are int-rounded (telegram style)
    """
    token = await get_app_token(client)
    url = f"{_browse_base()}/buy/browse/v1/item/{item_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    r = await client.get(url, headers=headers)
    j = r.json() if r.text else {}
    if r.status_code < 200 or r.status_code >= 300:
        return {
            "site": "ebay",
            "item_id": item_id,
            "error": f"http_{r.status_code}",
            "raw": j if isinstance(j, dict) else None,
        }

    title = j.get("title")
    item_web_url = j.get("itemWebUrl") or j.get("itemWebUrl".lower())
    price_val = None
    currency = None
    try:
        price = (j.get("price") or {})
        price_val = price.get("value")
        currency = price.get("currency")
    except Exception:
        pass

    ship_val = None
    try:
        ship = (j.get("shippingOptions") or [])
        # pick cheapest shipping option if exists
        best = None
        for opt in ship:
            s = ((opt or {}).get("shippingCost") or {}).get("value")
            if s is None:
                continue
            try:
                v = float(s)
                best = v if best is None else min(best, v)
            except Exception:
                continue
        ship_val = best
    except Exception:
        ship_val = None

    # availability heuristic
    available = True
    try:
        avail = (j.get("availability") or {}).get("availabilityStatus")
        if isinstance(avail, str) and avail.upper() not in ("IN_STOCK", "AVAILABLE"):
            available = False
    except Exception:
        pass

    price_i = _safe_int(price_val)
    ship_i = _safe_int(ship_val) if ship_val is not None else 0
    total_i = None if price_i is None else int(price_i + (ship_i or 0))

    return {
        "site": "ebay",
        "item_id": item_id,
        "title": title,
        "url": item_web_url,
        "price": price_i,
        "ship": ship_i,
        "total": total_i,
        "currency": currency,
        "available": available,
    }

async def finding_sold_stats(client: httpx.AsyncClient, keywords: str) -> Dict[str, Any]:
    """
    Finding API (findCompletedItems) with AppID.
    Returns: {site, keywords, sold_count, sold_min, sold_max, sold_avg}
    """
    if not EBAY_APP_ID:
        raise RuntimeError("EBAY_APP_ID not set")

    base = "https://svcs.ebay.com/services/search/FindingService/v1"
    params = {
        "OPERATION-NAME": "findCompletedItems",
        "SERVICE-VERSION": "1.13.0",
        "SECURITY-APPNAME": EBAY_APP_ID,
        "RESPONSE-DATA-FORMAT": "JSON",
        "REST-PAYLOAD": "true",
        "keywords": keywords,
        "paginationInput.entriesPerPage": "50",
        # Sold items only:
        "itemFilter(0).name": "SoldItemsOnly",
        "itemFilter(0).value": "true",
    }

    r = await client.get(base, params=params)
    j = r.json() if r.text else {}
    if r.status_code < 200 or r.status_code >= 300:
        return {"site": "ebay", "keywords": keywords, "error": f"http_{r.status_code}", "raw": j}

    # Parse JSON structure (Finding JSON is nested)
    totals = []
    try:
        resp = (j.get("findCompletedItemsResponse") or [])[0]
        sr = (resp.get("searchResult") or [])[0]
        items = sr.get("item") or []
        for it in items:
            selling = (it.get("sellingStatus") or [])[0]
            cur = (selling.get("currentPrice") or [])[0]
            v = cur.get("__value__")
            if v is None:
                continue
            try:
                totals.append(float(v))
            except Exception:
                continue
    except Exception:
        totals = []

    if not totals:
        return {
            "site": "ebay",
            "keywords": keywords,
            "sold_count": 0,
            "sold_min": None,
            "sold_max": None,
            "sold_avg": None,
        }

    sold_min = _safe_int(min(totals))
    sold_max = _safe_int(max(totals))
    sold_avg = _safe_int(sum(totals) / len(totals))

    return {
        "site": "ebay",
        "keywords": keywords,
        "sold_count": int(len(totals)),
        "sold_min": sold_min,
        "sold_max": sold_max,
        "sold_avg": sold_avg,
    }
