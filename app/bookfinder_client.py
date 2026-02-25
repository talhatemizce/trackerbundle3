"""
BookFinder.com price comparison scraper — on-demand, button-triggered.
GET /bookfinder/{isbn}

Strategy:
  - Tries /isbn/<ISBN>/ then search URL
  - Extracts prices from both Next.js RSC payload AND plain HTML
  - 30-min per-ISBN cache, on-demand only (button click)
  - Full debug mode: returns raw_html snippet on parse failure
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from pathlib import Path
from typing import Optional

import httpx

from app.core.config import get_settings
from app.core.json_store import file_lock, _read_unsafe, _write_unsafe

logger = logging.getLogger("trackerbundle.bookfinder")

_CACHE_TTL_S = 30 * 60
_MAX_OFFERS  = 20

_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
]

_SELLER_NAMES = {
    "EBAY": "eBay", "ABEBOOKS": "AbeBooks", "ALIBRIS": "Alibris",
    "BIBLIO": "Biblio", "BETTERWORLDBOOKS": "BetterWorldBooks",
    "AMAZON_USA": "Amazon US", "AMAZON_CAN": "Amazon CA", "AMAZON_UK": "Amazon UK",
    "THRIFTBOOKS": "ThriftBooks", "VALOREBOOKS": "ValoreBooks",
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
            data["entries"] = {
                k: v for k, v in data.get("entries", {}).items()
                if now - v.get("ts", 0) < _CACHE_TTL_S * 4
            }
            data["entries"][isbn] = {**result, "ts": int(now)}
            _write_unsafe(p, data)
    except Exception:
        pass


def _extract_rsc(html: str) -> Optional[dict]:
    """Extract searchResults from Next.js RSC stream."""
    chunks = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', html, re.DOTALL)
    for chunk in chunks:
        if 'searchResults' not in chunk and 'newOffers' not in chunk:
            continue
        try:
            unescaped = chunk.encode().decode('unicode_escape')
            # Try to find searchResults object
            for pattern in [
                r'\{"activeSearchOffersType".*',
                r'"searchResults":\s*(\{.*?)\}\s*,',
                r'newOffers.*?usedOffers',
            ]:
                m = re.search(pattern, unescaped, re.DOTALL)
                if not m:
                    continue
                raw = m.group(0)
                # Balance braces
                depth = 0
                for i, c in enumerate(raw):
                    if c == '{': depth += 1
                    elif c == '}': depth -= 1
                    if depth == 0:
                        try:
                            obj = json.loads(raw[:i+1])
                            sr = obj.get("searchResults") or obj
                            if sr.get("newOffers") is not None or sr.get("usedOffers") is not None:
                                return sr
                        except Exception:
                            break
        except (UnicodeDecodeError, Exception):
            continue

    # Try direct JSON extraction from script tags
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
    for s in scripts:
        if 'newOffers' in s or 'usedOffers' in s:
            try:
                # Find JSON object containing offers
                m = re.search(r'\{[^{}]*"newOffers"[^{}]*\}', s)
                if m:
                    return json.loads(m.group(0))
                # Find larger context
                idx = s.find('newOffers')
                if idx >= 0:
                    start = s.rfind('{', 0, idx)
                    if start >= 0:
                        depth = 0
                        for i, c in enumerate(s[start:], start):
                            if c == '{': depth += 1
                            elif c == '}': depth -= 1
                            if depth == 0:
                                try:
                                    obj = json.loads(s[start:i+1])
                                    if obj.get("newOffers") is not None:
                                        return obj
                                except Exception:
                                    break
            except Exception:
                continue

    return None


def _extract_html_prices(html: str) -> tuple[list[dict], list[dict]]:
    """Fallback: extract prices from rendered HTML."""
    new_offers, used_offers = [], []
    
    # Look for price patterns in HTML — common formats
    # Pattern: data-price="X.XX" or class="price">$X.XX
    price_blocks = re.findall(
        r'(?:price[">][^<]*?\$?(\d+\.\d{2})|data-price="(\d+\.\d{2})")',
        html, re.IGNORECASE
    )
    
    # Try JSON-LD structured data
    jsonld = re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL)
    for blob in jsonld:
        try:
            data = json.loads(blob)
            offers = data.get("offers", [])
            if isinstance(offers, dict):
                offers = [offers]
            for o in offers:
                price = float(o.get("price", 0) or 0)
                if price <= 0:
                    continue
                condition = str(o.get("itemCondition", "")).lower()
                is_new = "new" in condition
                entry = {"price": price, "shipping": 0.0, "total": price,
                         "seller": o.get("seller", {}).get("name", "Unknown") if isinstance(o.get("seller"), dict) else str(o.get("seller", "Unknown")),
                         "seller_id": "JSONLD", "condition": "NEW" if is_new else "USED",
                         "binding": "", "desc": ""}
                (new_offers if is_new else used_offers).append(entry)
        except Exception:
            continue

    new_offers.sort(key=lambda x: x["total"])
    used_offers.sort(key=lambda x: x["total"])
    return new_offers[:_MAX_OFFERS], used_offers[:_MAX_OFFERS]


def _parse_offers(raw_offers: list) -> list[dict]:
    """Parse raw BookFinder offer objects."""
    offers = []
    for o in (raw_offers or []):
        try:
            price    = float(o.get("priceInUsd") or o.get("price") or 0)
            shipping = float(o.get("shippingPriceInUsd") or o.get("shippingPrice") or o.get("shipping") or 0)
            if price <= 0:
                continue
            affiliate = str(o.get("affiliate") or o.get("seller_id") or "UNKNOWN")
            offers.append({
                "price":     round(price, 2),
                "shipping":  round(shipping, 2),
                "total":     round(price + shipping, 2),
                "seller":    _SELLER_NAMES.get(affiliate, affiliate.replace("_", " ").title()),
                "seller_id": affiliate,
                "condition": str(o.get("condition") or "UNKNOWN"),
                "binding":   str(o.get("binding") or ""),
                "desc":      str(o.get("conditionText") or "")[:120],
            })
        except (TypeError, ValueError):
            continue
    offers.sort(key=lambda x: x["total"])
    return offers[:_MAX_OFFERS]


def _stats(offers: list[dict]) -> Optional[dict]:
    if not offers:
        return None
    totals = [o["total"] for o in offers]
    return {
        "count": len(totals),
        "min":   round(min(totals), 2),
        "max":   round(max(totals), 2),
        "avg":   round(sum(totals) / len(totals), 2),
        "offers": offers,
    }


async def fetch_bookfinder(isbn: str) -> dict:
    isbn_clean = re.sub(r"[^0-9X]", "", isbn.upper().strip())

    cached = _cache_get(isbn_clean)
    if cached:
        age = int(time.time() - cached.get("ts", time.time()))
        return {**cached, "cached": True, "cache_age_s": age}

    await asyncio.sleep(random.uniform(0.4, 1.0))

    urls = [
        f"https://www.bookfinder.com/isbn/{isbn_clean}/",
        f"https://www.bookfinder.com/search/?keywords={isbn_clean}&currency=USD&destination=us&mode=basic&lang=en&st=sh&ac=qr&submit=Find+Books",
        f"https://www.bookfinder.com/search/?author=&title=&lang=en&isbn={isbn_clean}&new_used=*&destination=us&currency=USD&mode=basic&st=sh&ac=qr&submit=Find+Books",
    ]

    bf_url = urls[0]
    html = None
    last_status = None

    hdrs = {
        "User-Agent":                random.choice(_USER_AGENTS),
        "Accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language":           "en-US,en;q=0.9",
        "Accept-Encoding":           "gzip, deflate, br",
        "Connection":                "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest":            "document",
        "Sec-Fetch-Mode":            "navigate",
        "Sec-Fetch-Site":            "none",
        "Cache-Control":             "max-age=0",
    }

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=25) as client:
            for url in urls:
                try:
                    r = await client.get(url, headers=hdrs)
                    last_status = r.status_code
                    if r.status_code == 200:
                        html = r.text
                        bf_url = url
                        break
                    logger.debug("bookfinder %s → %s", url, r.status_code)
                    await asyncio.sleep(0.3)
                except Exception as e:
                    logger.debug("bookfinder url error %s: %s", url, e)
                    continue

        if html is None:
            return {"ok": False, "error": f"HTTP {last_status or 'timeout'}", "isbn": isbn_clean}

        # Try RSC extraction first
        sr = _extract_rsc(html)
        if sr:
            new_offers  = _parse_offers(sr.get("newOffers") or [])
            used_offers = _parse_offers(sr.get("usedOffers") or [])
        else:
            # Fallback: HTML structured data extraction
            new_offers, used_offers = _extract_html_prices(html)

        if not new_offers and not used_offers:
            # Return debug info so we can diagnose
            logger.warning(
                "bookfinder isbn=%s: no offers found. html_len=%d has_rsc=%s",
                isbn_clean, len(html), '__next_f' in html
            )
            return {
                "ok": False,
                "error": "Fiyat verisi bulunamadı",
                "isbn": isbn_clean,
                "debug": {
                    "html_len": len(html),
                    "has_rsc": '__next_f' in html,
                    "has_sr_key": 'searchResults' in html,
                    "has_offers_key": 'newOffers' in html or 'usedOffers' in html,
                    "url_used": bf_url,
                }
            }

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
        logger.info("bookfinder isbn=%s new=%d used=%d avg=%s", isbn_clean, len(new_offers), len(used_offers), all_avg)
        return result

    except Exception as exc:
        logger.warning("bookfinder error isbn=%s: %s", isbn_clean, exc)
        return {"ok": False, "error": str(exc), "isbn": isbn_clean}
