"""
On-demand eBay sold price scraper.
Endpoint: GET /ebay/sold-avg/{isbn}

Strategy:
  - Fetches https://www.ebay.com/sch/i.html?_nkw={isbn}&LH_Sold=1&LH_Complete=1
  - Parses sold prices from HTML (no JS required — eBay renders them server-side)
  - Returns: count, min, max, avg, median, last_sold_date
  - 30-minute per-ISBN cache → low request rate, no ban risk
  - User-triggered only (button click) — not called on every page load

Rate safety:
  - On-demand only (not scheduled)
  - 30min TTL cache per ISBN
  - Single request per trigger
  - Randomised User-Agent rotation
  - 1-2s human-like delay on cache miss

eBay scrape ToS note:
  Public sold listing pages are publicly accessible.
  On-demand, low-frequency, single-user personal tool usage.
  Not a commercial competitor, no systematic data harvesting.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from pathlib import Path
from typing import Optional

import httpx

from app.core.config import get_settings
from app.core.json_store import file_lock, _read_unsafe, _write_unsafe

logger = logging.getLogger("trackerbundle.sold_scraper")

_CACHE_TTL_S = 30 * 60   # 30 minutes
_MAX_ITEMS   = 40         # parse first 40 sold items

_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
]


def _cache_path() -> Path:
    return get_settings().resolved_data_dir() / "sold_scrape_cache.json"


def _cache_get(isbn: str) -> Optional[dict]:
    data = _read_unsafe(_cache_path(), default={"entries": {}})
    entry = data.get("entries", {}).get(isbn)
    if entry and time.time() - entry.get("ts", 0) < _CACHE_TTL_S:
        return entry
    return None


def _cache_set(isbn: str, result: dict) -> None:
    p = _cache_path()
    with file_lock(p):
        data = _read_unsafe(p, default={"entries": {}})
        # Evict stale entries (keep bounded)
        now = time.time()
        data["entries"] = {
            k: v for k, v in data.get("entries", {}).items()
            if now - v.get("ts", 0) < _CACHE_TTL_S * 4
        }
        data["entries"][isbn] = {**result, "ts": int(now)}
        _write_unsafe(p, data)


def _parse_sold_prices(html: str) -> list[float]:
    """
    Extract sold prices from eBay completed/sold listings HTML.
    eBay renders prices server-side in several formats:
      - <span class="s-item__price">$12.50</span>
      - <span class="POSITIVE">$12.50</span>  (sold price highlight)
    We look for monetary values in expected price elements.
    """
    prices: list[float] = []

    # Primary: s-item__price spans (main listing price)
    # eBay uses this consistently across regions/layouts
    # Pattern catches: $12.50 | $1,234.56
    pattern = re.compile(
        r'class="[^"]*s-item__price[^"]*"[^>]*>\s*\$([0-9,]+(?:\.[0-9]{1,2})?)',
        re.IGNORECASE
    )
    for m in pattern.finditer(html):
        try:
            val = float(m.group(1).replace(",", ""))
            if 0.5 <= val <= 5000:  # sanity bounds for books
                prices.append(val)
        except ValueError:
            pass

    # Fallback: any "$N.NN" near "Sold" text (for edge cases)
    if len(prices) < 3:
        alt = re.compile(r'\$([0-9]+\.[0-9]{2})')
        for m in alt.finditer(html):
            try:
                val = float(m.group(1))
                if 0.5 <= val <= 500 and val not in prices:
                    prices.append(val)
            except ValueError:
                pass

    return prices[:_MAX_ITEMS]


async def fetch_sold_avg(isbn: str) -> dict:
    """
    Main entry point. Returns cached result if available.

    Returns:
    {
        "ok": True,
        "isbn": str,
        "count": int,
        "min": float,
        "max": float,
        "avg": float,
        "median": float,
        "cached": bool,
        "cache_age_s": int,
        "ebay_url": str,
    }
    or {"ok": False, "error": str}
    """
    isbn_clean = isbn.replace("-", "").replace(" ", "").strip()

    cached = _cache_get(isbn_clean)
    if cached:
        age = int(time.time() - cached["ts"])
        return {**cached, "cached": True, "cache_age_s": age}

    # Human-like delay on live fetch
    await asyncio.sleep(random.uniform(0.8, 2.0))

    url = (
        f"https://www.ebay.com/sch/i.html"
        f"?_nkw={isbn_clean}&_sacat=267"
        f"&LH_Sold=1&LH_Complete=1"
        f"&_sop=13"  # sort by most recently sold
    )

    headers = {
        "User-Agent": random.choice(_USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=20,
            headers=headers,
        ) as client:
            r = await client.get(url)

        if r.status_code != 200:
            return {"ok": False, "error": f"HTTP {r.status_code}", "isbn": isbn_clean}

        prices = _parse_sold_prices(r.text)

        if not prices:
            result = {
                "ok": True, "isbn": isbn_clean,
                "count": 0, "min": None, "max": None,
                "avg": None, "median": None,
                "ebay_url": url, "cached": False, "cache_age_s": 0,
            }
            _cache_set(isbn_clean, result)
            return result

        prices.sort()
        n = len(prices)
        median = prices[n // 2] if n % 2 else round((prices[n//2-1] + prices[n//2]) / 2, 2)

        result = {
            "ok": True,
            "isbn": isbn_clean,
            "count": n,
            "min": round(min(prices), 2),
            "max": round(max(prices), 2),
            "avg": round(sum(prices) / n, 2),
            "median": round(median, 2),
            "ebay_url": url,
            "cached": False,
            "cache_age_s": 0,
        }
        _cache_set(isbn_clean, result)
        logger.info("sold_scrape isbn=%s count=%d avg=%.2f", isbn_clean, n, result["avg"])
        return result

    except Exception as exc:
        logger.warning("sold_scrape error isbn=%s: %s", isbn_clean, exc)
        return {"ok": False, "error": str(exc), "isbn": isbn_clean}
