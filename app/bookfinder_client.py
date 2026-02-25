"""
Multi-source book price comparison — on-demand, button-triggered.
GET /bookfinder/{isbn}

Sources (tried in order):
  1. BookFinder.com  — /isbn/{isbn}/ then search fallback
  2. AbeBooks        — /servlet/SearchResults?isbn=...
  3. Open Library    — price data from availability API
"""
from __future__ import annotations

import asyncio, json, logging, random, re, time
from pathlib import Path
from typing import Optional

import httpx

from app.core.config import get_settings
from app.core.json_store import file_lock, _read_unsafe, _write_unsafe

logger = logging.getLogger("trackerbundle.bookfinder")

_CACHE_TTL_S = 30 * 60
_MAX_OFFERS  = 20

_UA = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
]

_SELLER_NAMES = {
    "EBAY": "eBay", "ABEBOOKS": "AbeBooks", "ALIBRIS": "Alibris",
    "BIBLIO": "Biblio", "BETTERWORLDBOOKS": "BetterWorldBooks",
    "AMAZON_USA": "Amazon US", "AMAZON_CAN": "Amazon CA", "AMAZON_UK": "Amazon UK",
    "THRIFTBOOKS": "ThriftBooks", "VALOREBOOKS": "ValoreBooks",
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
            data["entries"] = {k: v for k, v in data.get("entries", {}).items()
                               if now - v.get("ts", 0) < _CACHE_TTL_S * 4}
            data["entries"][isbn] = {**result, "ts": int(now)}
            _write_unsafe(p, data)
    except Exception:
        pass

def _stats(offers: list[dict]) -> Optional[dict]:
    if not offers:
        return None
    totals = [o["total"] for o in offers]
    return {"count": len(totals), "min": round(min(totals),2),
            "max": round(max(totals),2), "avg": round(sum(totals)/len(totals),2),
            "offers": offers}

def _make_headers(referer: str = "https://www.bookfinder.com/") -> dict:
    return {
        "User-Agent":                random.choice(_UA),
        "Accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language":           "en-US,en;q=0.9",
        "Accept-Encoding":           "gzip, deflate, br",
        "Connection":                "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest":            "document",
        "Sec-Fetch-Mode":            "navigate",
        "Sec-Fetch-Site":            "none",
        "Cache-Control":             "max-age=0",
        "Referer":                   referer,
    }

# ── Source 1: BookFinder ─────────────────────────────────────────────────────

def _bf_extract_rsc(html: str) -> Optional[dict]:
    """Extract offers from Next.js RSC chunks."""
    chunks = re.findall(r'self\\.__next_f\\.push\\(\\[1,"(.*?)"\\]\\)', html, re.DOTALL)
    for chunk in chunks:
        if "newOffers" not in chunk and "usedOffers" not in chunk:
            continue
        try:
            unescaped = chunk.encode().decode("unicode_escape")
            idx = unescaped.find("newOffers")
            if idx < 0:
                continue
            start = unescaped.rfind("{", 0, idx)
            if start < 0:
                continue
            depth = 0
            for i, c in enumerate(unescaped[start:], start):
                if c == "{": depth += 1
                elif c == "}": depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(unescaped[start:i+1])
                        if obj.get("newOffers") is not None or obj.get("usedOffers") is not None:
                            return obj
                    except Exception:
                        break
        except Exception:
            continue
    return None

def _bf_parse_offer(o: dict, seller_override: str = "") -> Optional[dict]:
    try:
        price    = float(o.get("priceInUsd") or o.get("price") or 0)
        shipping = float(o.get("shippingPriceInUsd") or o.get("shippingPrice") or o.get("shipping") or 0)
        if price <= 0:
            return None
        affiliate = str(o.get("affiliate") or seller_override or "UNKNOWN")
        return {
            "price":     round(price, 2),
            "shipping":  round(shipping, 2),
            "total":     round(price + shipping, 2),
            "seller":    _SELLER_NAMES.get(affiliate, affiliate.replace("_"," ").title()),
            "seller_id": affiliate,
            "condition": str(o.get("condition") or "UNKNOWN"),
            "binding":   str(o.get("binding") or ""),
            "desc":      str(o.get("conditionText") or "")[:120],
        }
    except Exception:
        return None

async def _fetch_bookfinder(client: httpx.AsyncClient, isbn: str) -> Optional[dict]:
    """Try BookFinder URLs. Returns parsed result or None."""
    urls = [
        f"https://www.bookfinder.com/isbn/{isbn}/",
        f"https://www.bookfinder.com/search/?keywords={isbn}&currency=USD&destination=us&mode=basic&lang=en&st=sh&ac=qr&submit=Find+Books",
        f"https://www.bookfinder.com/search/?isbn={isbn}&new_used=*&destination=us&currency=USD&mode=basic&st=sh&ac=qr&submit=Find+Books",
    ]
    for url in urls:
        try:
            r = await client.get(url, headers=_make_headers(), timeout=20)
            logger.debug("bookfinder %s → %s (len=%s)", url[-50:], r.status_code, len(r.text) if r.status_code==200 else "-")
            if r.status_code != 200:
                await asyncio.sleep(0.3)
                continue
            sr = _bf_extract_rsc(r.text)
            if not sr:
                # Try JSON-LD
                for blob in re.findall(r'<script type="application/ld\\+json">(.*?)</script>', r.text, re.DOTALL):
                    try:
                        d = json.loads(blob)
                        offers_raw = d.get("offers", [])
                        if isinstance(offers_raw, dict): offers_raw = [offers_raw]
                        if offers_raw:
                            new_o, used_o = [], []
                            for o in offers_raw:
                                price = float(o.get("price",0) or 0)
                                if price <= 0: continue
                                cond = str(o.get("itemCondition","")).lower()
                                seller = (o.get("seller",{}) or {}).get("name","Unknown") if isinstance(o.get("seller"),dict) else str(o.get("seller",""))
                                entry = {"price":price,"shipping":0.0,"total":price,"seller":seller,"seller_id":"JSONLD","condition":"NEW" if "new" in cond else "USED","binding":"","desc":""}
                                (new_o if "new" in cond else used_o).append(entry)
                            if new_o or used_o:
                                new_o.sort(key=lambda x:x["total"]); used_o.sort(key=lambda x:x["total"])
                                return {"source":"bookfinder_jsonld","new":_stats(new_o[:_MAX_OFFERS]),"used":_stats(used_o[:_MAX_OFFERS]),"bookfinder_url":url}
                    except Exception:
                        continue
                logger.debug("bookfinder: no RSC/JSON-LD offers in html (len=%d, has_next=%s)", len(r.text), "__next_f" in r.text)
                continue
            new_raw  = sr.get("newOffers")  or []
            used_raw = sr.get("usedOffers") or []
            new_o  = [x for x in (_bf_parse_offer(o) for o in new_raw)  if x]
            used_o = [x for x in (_bf_parse_offer(o) for o in used_raw) if x]
            if not new_o and not used_o:
                continue
            new_o.sort(key=lambda x:x["total"]); used_o.sort(key=lambda x:x["total"])
            return {"source":"bookfinder","new":_stats(new_o[:_MAX_OFFERS]),"used":_stats(used_o[:_MAX_OFFERS]),"bookfinder_url":url}
        except Exception as e:
            logger.debug("bookfinder url error %s: %s", url, e)
            continue
    return None

# ── Source 2: AbeBooks ───────────────────────────────────────────────────────

def _abe_parse_html(html: str, isbn: str) -> Optional[dict]:
    """Extract prices from AbeBooks search results HTML."""
    new_o, used_o = [], []
    
    # AbeBooks embeds item data in JSON script tags
    for blob in re.findall(r'window\\.utag_data\\s*=\\s*(\\{.*?\\});\\s*</script>', html, re.DOTALL):
        try:
            d = json.loads(blob)
            prices = d.get("product_price",[])
            conds  = d.get("product_condition",[])
            ships  = d.get("product_shipping",[])
            for i, price_str in enumerate(prices):
                try:
                    price = float(str(price_str).replace(",","").replace("$",""))
                    ship  = float(str(ships[i] if i < len(ships) else 0).replace(",","").replace("$",""))
                    cond  = str(conds[i] if i < len(conds) else "used").lower()
                    if price <= 0: continue
                    entry = {"price":round(price,2),"shipping":round(ship,2),"total":round(price+ship,2),
                             "seller":"AbeBooks","seller_id":"ABEBOOKS",
                             "condition":"NEW" if "new" in cond else "USED","binding":"","desc":""}
                    (new_o if "new" in cond else used_o).append(entry)
                except Exception:
                    continue
        except Exception:
            continue
    
    # Fallback: regex price extraction from listing HTML
    if not new_o and not used_o:
        price_pattern = re.compile(r'data-price="([\d.]+)"|"salePrice":"([\d.]+)"|itemprop="price"[^>]*content="([\d.]+)"')
        for m in price_pattern.finditer(html):
            price = float(next(x for x in m.groups() if x))
            if price > 0:
                # default to used since AbeBooks is mostly used
                used_o.append({"price":round(price,2),"shipping":3.99,"total":round(price+3.99,2),
                               "seller":"AbeBooks","seller_id":"ABEBOOKS","condition":"USED","binding":"","desc":""})
    
    if not new_o and not used_o:
        return None
    new_o.sort(key=lambda x:x["total"]); used_o.sort(key=lambda x:x["total"])
    return {"source":"abebooks","new":_stats(new_o[:_MAX_OFFERS]),"used":_stats(used_o[:_MAX_OFFERS]),
            "bookfinder_url":f"https://www.abebooks.com/servlet/SearchResults?isbn={isbn}"}

async def _fetch_abebooks(client: httpx.AsyncClient, isbn: str) -> Optional[dict]:
    url = f"https://www.abebooks.com/servlet/SearchResults?isbn={isbn}&n=100121503&cm_sp=snippet-_-srp1-_-title1"
    try:
        r = await client.get(url, headers=_make_headers("https://www.abebooks.com/"), timeout=20)
        logger.debug("abebooks %s → %s", isbn, r.status_code)
        if r.status_code != 200:
            return None
        return _abe_parse_html(r.text, isbn)
    except Exception as e:
        logger.debug("abebooks error %s: %s", isbn, e)
        return None

# ── Main entry ───────────────────────────────────────────────────────────────

async def fetch_bookfinder(isbn: str) -> dict:
    isbn_clean = re.sub(r"[^0-9X]", "", isbn.upper().strip())

    cached = _cache_get(isbn_clean)
    if cached:
        age = int(time.time() - cached.get("ts", time.time()))
        return {**cached, "cached": True, "cache_age_s": age}

    await asyncio.sleep(random.uniform(0.3, 0.8))

    bf_url = f"https://www.bookfinder.com/isbn/{isbn_clean}/"
    result = None
    source_tried = []

    async with httpx.AsyncClient(follow_redirects=True, timeout=25) as client:
        # Source 1: BookFinder
        source_tried.append("bookfinder")
        result = await _fetch_bookfinder(client, isbn_clean)

        # Source 2: AbeBooks fallback
        if not result:
            source_tried.append("abebooks")
            await asyncio.sleep(0.3)
            result = await _fetch_abebooks(client, isbn_clean)

    if not result:
        logger.warning("bookfinder: all sources failed for isbn=%s tried=%s", isbn_clean, source_tried)
        return {"ok": False, "error": f"Tüm kaynaklar başarısız ({', '.join(source_tried)})", "isbn": isbn_clean}

    out = {
        "ok":           True,
        "isbn":         isbn_clean,
        "new":          result.get("new"),
        "used":         result.get("used"),
        "all_avg":      None,
        "total_offers": (result.get("new") or {}).get("count",0) + (result.get("used") or {}).get("count",0),
        "bookfinder_url": result.get("bookfinder_url", bf_url),
        "source":       result.get("source","unknown"),
        "cached":       False,
        "cache_age_s":  0,
    }
    all_t = []
    if out["new"]:  all_t += [o["total"] for o in out["new"].get("offers",[])]
    if out["used"]: all_t += [o["total"] for o in out["used"].get("offers",[])]
    out["all_avg"] = round(sum(all_t)/len(all_t),2) if all_t else None

    _cache_set(isbn_clean, out)
    logger.info("bookfinder isbn=%s source=%s new=%s used=%s avg=%s",
                isbn_clean, out["source"],
                out["new"]["count"] if out["new"] else 0,
                out["used"]["count"] if out["used"] else 0,
                out["all_avg"])
    return out
