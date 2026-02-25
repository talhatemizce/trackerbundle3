"""
BookFinder.com price comparison scraper — on-demand, button-triggered.
GET /bookfinder/{isbn}

Returns:
  {
    ok, isbn,
    new:  { count, min, max, avg, offers: [{price, shipping, total, seller, condition, binding}] },
    used: { count, min, max, avg, offers: [...] },
    all_avg, bookfinder_url,
    cached, cache_age_s
  }

Strategy:
  - Fetches bookfinder.com/isbn/<ISBN> which returns Next.js RSC payload
  - Extracts structured JSON from React Server Component stream
  - 30-min per-ISBN cache
  - On-demand only (button click) → no ban risk
  - User-agent rotation + polite delay on cache miss
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import re
import time
from pathlib import Path
from typing import Optional

import httpx

from app.core.config import get_settings
from app.core.json_store import file_lock, _read_unsafe, _write_unsafe

logger = logging.getLogger("trackerbundle.bookfinder")

_CACHE_TTL_S = 30 * 60   # 30 min
_MAX_OFFERS  = 20         # keep top N per condition for panel display

_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
]

# Seller name mapping for cleaner display
_SELLER_NAMES = {
    "EBAY": "eBay",
    "ABEBOOKS": "AbeBooks",
    "ALIBRIS": "Alibris",
    "BIBLIO": "Biblio",
    "BETTERWORLDBOOKS": "BetterWorldBooks",
    "AMAZON_USA": "Amazon US",
    "AMAZON_CAN": "Amazon CA",
    "AMAZON_UK": "Amazon UK",
    "AMAZON_BRA": "Amazon BR",
    "AMAZON_DEU": "Amazon DE",
    "THRIFTBOOKS": "ThriftBooks",
    "VALOREBOOKS": "ValoreBooks",
    "TEXTBOOKRUSH": "TextbookRush",
}


def _cache_path() -> Path:
    return get_settings().resolved_data_dir() / "bookfinder_cache.json"


def _cache_get(isbn: str) -> Optional[dict]:
    try:
        data = _read_unsafe(_cache_path(), default={"entries": {}})
        entry = data.get("entries", {}).get(isbn)
        if entry and time.time() - entry.get("ts", 0) < _CACHE_TTL_S:
            return entry
    except Exception:
        pass
    return None


def _cache_set(isbn: str, result: dict) -> None:
    p = _cache_path()
    try:
        with file_lock(p):
            data = _read_unsafe(p, default={"entries": {}})
            now = time.time()
            # Evict stale entries
            data["entries"] = {
                k: v for k, v in data.get("entries", {}).items()
                if now - v.get("ts", 0) < _CACHE_TTL_S * 4
            }
            data["entries"][isbn] = {**result, "ts": int(now)}
            _write_unsafe(p, data)
    except Exception:
        pass


def _extract_search_results(html: str) -> Optional[dict]:
    """Extract searchResults JSON from Next.js RSC stream embedded in HTML."""
    # Find RSC payload chunks
    chunks = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', html, re.DOTALL)
    for chunk in chunks:
        # RSC chunks contain escaped quotes (\"), so check without outer quotes
        if 'searchResults' not in chunk:
            continue
        try:
            # Unescape the JSON string (RSC payloads use JS string escaping)
            unescaped = chunk.encode().decode('unicode_escape')
            # Find the props object containing searchResults
            m = re.search(r'\{"activeSearchOffersType".*', unescaped)
            if not m:
                continue
            raw = m.group(0)
            # Find matching braces to extract complete JSON object
            depth = 0
            for i, c in enumerate(raw):
                if c == '{':
                    depth += 1
                elif c == '}':
                    depth -= 1
                if depth == 0:
                    obj_str = raw[:i + 1]
                    break
            else:
                continue
            data = json.loads(obj_str)
            return data.get("searchResults")
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
    return None


def _parse_offers(raw_offers: list[dict]) -> list[dict]:
    """Parse raw BookFinder offer objects into clean dicts."""
    offers = []
    for o in raw_offers:
        try:
            price = float(o.get("priceInUsd") or o.get("price") or 0)
            shipping = float(o.get("shippingPriceInUsd") or o.get("shippingPrice") or 0)
            if price <= 0:
                continue
            total = round(price + shipping, 2)
            affiliate = o.get("affiliate", "UNKNOWN")
            offers.append({
                "price":     round(price, 2),
                "shipping":  round(shipping, 2),
                "total":     total,
                "seller":    _SELLER_NAMES.get(affiliate, affiliate.title()),
                "seller_id": affiliate,
                "condition": o.get("condition", "UNKNOWN"),
                "binding":   o.get("binding", ""),
                "desc":      (o.get("conditionText") or "")[:120],
            })
        except (TypeError, ValueError):
            continue
    # Sort by total ascending
    offers.sort(key=lambda x: x["total"])
    return offers[:_MAX_OFFERS]


def _stats(offers: list[dict]) -> Optional[dict]:
    """Compute summary stats for a list of parsed offers."""
    if not offers:
        return None
    totals = [o["total"] for o in offers]
    n = len(totals)
    return {
        "count": n,
        "min":   round(min(totals), 2),
        "max":   round(max(totals), 2),
        "avg":   round(sum(totals) / n, 2),
        "offers": offers,
    }


async def fetch_bookfinder(isbn: str) -> dict:
    """
    Fetch price comparison from BookFinder.com for a given ISBN.
    Returns cached if available. On-demand only (button click).
    """
    isbn_clean = isbn.replace("-", "").replace(" ", "").strip()

    cached = _cache_get(isbn_clean)
    if cached:
        age = int(time.time() - cached.get("ts", time.time()))
        return {**cached, "cached": True, "cache_age_s": age}

    # Human-like delay on live fetch
    await asyncio.sleep(random.uniform(0.5, 1.2))

    # BookFinder URL: try /isbn/ first, fall back to search
    bf_url = f"https://www.bookfinder.com/isbn/{isbn_clean}/"
    bf_search_url = (
        f"https://www.bookfinder.com/search/?keywords={isbn_clean}"
        f"&currency=USD&destination=us&mode=basic&lang=en&st=sh&ac=qr&submit=Find+Books"
    )
    try:
        hdrs = {
            "User-Agent":      random.choice(_USER_AGENTS),
            "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer":         "https://www.bookfinder.com/",
        }
        async with httpx.AsyncClient(follow_redirects=True, timeout=25) as client:
            r = await client.get(bf_url, headers=hdrs)
            # If /isbn/ returns 405 or 404, try the search URL
            if r.status_code in (404, 405, 301, 302, 400):
                await asyncio.sleep(0.3)
                r = await client.get(bf_search_url, headers=hdrs)

        if r.status_code != 200:
            return {"ok": False, "error": f"HTTP {r.status_code}", "isbn": isbn_clean}

        sr = _extract_search_results(r.text)
        if not sr:
            return {"ok": False, "error": "BookFinder verisi bulunamadı", "isbn": isbn_clean}

        new_offers  = _parse_offers(sr.get("newOffers") or [])
        used_offers = _parse_offers(sr.get("usedOffers") or [])

        all_totals = [o["total"] for o in new_offers + used_offers]
        all_avg = round(sum(all_totals) / len(all_totals), 2) if all_totals else None

        result = {
            "ok":             True,
            "isbn":           isbn_clean,
            "new":            _stats(new_offers),
            "used":           _stats(used_offers),
            "all_avg":        all_avg,
            "total_offers":   len(new_offers) + len(used_offers),
            "bookfinder_url": bf_url,
            "cached":         False,
            "cache_age_s":    0,
        }

        _cache_set(isbn_clean, result)
        logger.info(
            "bookfinder isbn=%s new=%d used=%d avg=%s",
            isbn_clean, len(new_offers), len(used_offers), all_avg,
        )
        return result

    except Exception as exc:
        logger.warning("bookfinder error isbn=%s: %s", isbn_clean, exc)
        return {"ok": False, "error": str(exc), "isbn": isbn_clean}
