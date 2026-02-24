"""
On-demand eBay sold price scraper — new + used split.
GET /ebay/sold-avg/{isbn}

Returns:
  { ok, isbn, new: {count,min,max,avg,median}, used: {count,min,max,avg,median},
    combined: {...}, ebay_url_used, ebay_url_new, cached, cache_age_s }

Strategy:
  - Two parallel requests: one with LH_ItemCondition=1000 (New), one with 3000 (Used)
  - 30-min per-ISBN cache
  - On-demand only (button click), not scheduled → no ban risk
  - Staggered user-agent rotation + 0.8-2s human delay on cache miss
"""
from __future__ import annotations

import asyncio
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

logger = logging.getLogger("trackerbundle.sold_scraper")

_CACHE_TTL_S = 30 * 60
_MAX_ITEMS   = 60

_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
]

# eBay condition filter IDs
_COND_NEW  = "1000"   # New
_COND_USED = "3000"   # Used (covers Used/Good/Very Good/Acceptable)


def _cache_path() -> Path:
    return get_settings().resolved_data_dir() / "sold_scrape_cache.json"


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
            data["entries"] = {
                k: v for k, v in data.get("entries", {}).items()
                if now - v.get("ts", 0) < _CACHE_TTL_S * 4
            }
            data["entries"][isbn] = {**result, "ts": int(now)}
            _write_unsafe(p, data)
    except Exception:
        pass


def _parse_prices(html: str) -> list[float]:
    """Extract sold prices from eBay sold-listings HTML.

    Notes:
      - eBay always inserts a promotional "Shop on eBay" card as the first
        s-item result.  Its price is irrelevant and must be skipped.
      - The old fallback regex (any $XX.XX on the page) was catching UI
        chrome prices (e.g. shipping labels, promo banners) and returning
        false positives ($20 for every ISBN).  Removed entirely.
    """
    prices: list[float] = []
    # Primary: s-item__price spans
    for m in re.finditer(
        r'class="[^"]*s-item__price[^"]*"[^>]*>\s*\$([0-9,]+(?:\.[0-9]{1,2})?)',
        html, re.IGNORECASE
    ):
        try:
            v = float(m.group(1).replace(",", ""))
            if 0.25 <= v <= 5000:
                prices.append(v)
        except ValueError:
            pass
    # Skip the first match — always a promotional / placeholder item
    if prices:
        prices = prices[1:]
    return prices[:_MAX_ITEMS]


def _stats(prices: list[float]) -> Optional[dict]:
    if not prices:
        return None
    n = len(prices)
    s = sorted(prices)
    med = s[n // 2] if n % 2 else round((s[n//2-1] + s[n//2]) / 2, 2)
    return {
        "count":  n,
        "min":    round(min(prices), 2),
        "max":    round(max(prices), 2),
        "avg":    round(sum(prices) / n, 2),
        "median": round(med, 2),
    }


async def _fetch_condition(
    client: httpx.AsyncClient,
    isbn: str,
    cond_id: str,
) -> tuple[list[float], str]:
    """Fetch sold prices for one condition. Returns (prices, url)."""
    url = (
        f"https://www.ebay.com/sch/i.html"
        f"?_nkw={isbn}&_sacat=267"
        f"&LH_Sold=1&LH_Complete=1"
        f"&LH_ItemCondition={cond_id}"
        f"&_sop=13"
    )
    headers = {
        "User-Agent":              random.choice(_USER_AGENTS),
        "Accept":                  "text/html,application/xhtml+xml",
        "Accept-Language":         "en-US,en;q=0.9",
        "Accept-Encoding":         "gzip, deflate, br",
        "Connection":              "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }
    try:
        r = await client.get(url, headers=headers, timeout=18)
        if r.status_code == 200:
            return _parse_prices(r.text), url
    except Exception as exc:
        logger.debug("sold_scrape fetch cond=%s isbn=%s: %s", cond_id, isbn, exc)
    return [], url


async def fetch_sold_avg(isbn: str) -> dict:
    """
    Fetch new + used sold averages in parallel. Returns cached if available.
    """
    isbn_clean = isbn.replace("-", "").replace(" ", "").strip()

    cached = _cache_get(isbn_clean)
    if cached:
        age = int(time.time() - cached.get("ts", time.time()))
        return {**cached, "cached": True, "cache_age_s": age}

    # Human-like delay on live fetch
    await asyncio.sleep(random.uniform(0.6, 1.4))

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=22) as client:
            # Parallel: new + used simultaneously
            (new_prices, new_url), (used_prices, used_url) = await asyncio.gather(
                _fetch_condition(client, isbn_clean, _COND_NEW),
                _fetch_condition(client, isbn_clean, _COND_USED),
            )

        all_prices = list({*new_prices, *used_prices})  # deduplicate across conditions

        result = {
            "ok":        True,
            "isbn":      isbn_clean,
            "new":       _stats(new_prices),
            "used":      _stats(used_prices),
            "combined":  _stats(all_prices) if all_prices else None,
            "ebay_url_new":  new_url,
            "ebay_url_used": used_url,
            "cached":        False,
            "cache_age_s":   0,
        }

        _cache_set(isbn_clean, result)
        logger.info(
            "sold_scrape isbn=%s new=%d used=%d",
            isbn_clean,
            len(new_prices),
            len(used_prices),
        )
        return result

    except Exception as exc:
        logger.warning("sold_scrape error isbn=%s: %s", isbn_clean, exc)
        return {"ok": False, "error": str(exc), "isbn": isbn_clean}
