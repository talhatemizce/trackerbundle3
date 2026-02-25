"""
Multi-source book price comparison — on-demand, button-triggered.
GET /bookfinder/{isbn}

Sources (tried in parallel):
  1. BookFinder.com  — RSC / JSON-LD parsing
  2. AbeBooks        — HTML price extraction
  3. ThriftBooks     — search API
  4. BetterWorldBooks — search page
  5. Biblio.com      — search page
  6. Alibris         — search page
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
]

_SELLER_NAMES = {
    "EBAY": "eBay", "ABEBOOKS": "AbeBooks", "ALIBRIS": "Alibris",
    "BIBLIO": "Biblio", "BETTERWORLDBOOKS": "BetterWorldBooks",
    "AMAZON_USA": "Amazon US", "THRIFTBOOKS": "ThriftBooks",
    "VALOREBOOKS": "ValoreBooks",
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

def _stats(offers: list) -> Optional[dict]:
    if not offers: return None
    totals = [o["total"] for o in offers]
    return {"count": len(totals), "min": round(min(totals),2),
            "avg": round(sum(totals)/len(totals),2), "offers": offers}

def _ua() -> str:
    return random.choice(_UA)

def _hdrs(referer: str = "https://www.google.com/") -> dict:
    return {
        "User-Agent": _ua(),
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Cache-Control": "max-age=0",
        "Referer": referer,
    }

def _offer(price: float, ship: float, seller: str, sid: str, cond: str, desc: str = "") -> dict:
    return {"price": round(price,2), "shipping": round(ship,2),
            "total": round(price+ship,2), "seller": seller, "seller_id": sid,
            "condition": cond, "binding": "", "desc": desc[:120]}

def _split_new_used(offers: list) -> tuple[list, list]:
    new_o  = sorted([o for o in offers if o["condition"] in ("NEW","new","New")], key=lambda x:x["total"])
    used_o = sorted([o for o in offers if o["condition"] not in ("NEW","new","New")], key=lambda x:x["total"])
    return new_o[:_MAX_OFFERS], used_o[:_MAX_OFFERS]

# ── Source 1: BookFinder ─────────────────────────────────────────────────────

def _bf_rsc(html: str) -> Optional[dict]:
    chunks = re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', html, re.DOTALL)
    for chunk in chunks:
        if "newOffers" not in chunk and "usedOffers" not in chunk:
            continue
        try:
            u = chunk.encode().decode("unicode_escape")
            idx = u.find("newOffers")
            if idx < 0: continue
            start = u.rfind("{", 0, idx)
            if start < 0: continue
            depth = 0
            for i, c in enumerate(u[start:], start):
                if c == "{": depth += 1
                elif c == "}": depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(u[start:i+1])
                        if obj.get("newOffers") is not None or obj.get("usedOffers") is not None:
                            return obj
                    except Exception:
                        break
        except Exception:
            continue
    return None

async def _src_bookfinder(client: httpx.AsyncClient, isbn: str) -> Optional[dict]:
    urls = [
        f"https://www.bookfinder.com/isbn/{isbn}/",
        f"https://www.bookfinder.com/search/?keywords={isbn}&currency=USD&destination=us&mode=basic&lang=en&st=sh&ac=qr",
        f"https://www.bookfinder.com/search/?isbn={isbn}&new_used=*&destination=us&currency=USD&mode=basic&st=sh&ac=qr",
    ]
    for url in urls:
        try:
            r = await client.get(url, headers=_hdrs(), timeout=18)
            if r.status_code != 200:
                continue
            sr = _bf_rsc(r.text)
            if sr:
                raw_new  = [o for o in (sr.get("newOffers")  or []) if float(o.get("priceInUsd") or 0) > 0]
                raw_used = [o for o in (sr.get("usedOffers") or []) if float(o.get("priceInUsd") or 0) > 0]
                def _po(o, cond):
                    p = float(o.get("priceInUsd") or 0)
                    s = float(o.get("shippingPriceInUsd") or 0)
                    aff = str(o.get("affiliate","BF"))
                    return _offer(p, s, _SELLER_NAMES.get(aff, aff.title()), aff, cond, str(o.get("conditionText","")[:80]))
                new_o  = [_po(o,"NEW")  for o in raw_new]
                used_o = [_po(o,"USED") for o in raw_used]
                if new_o or used_o:
                    return {"source":"bookfinder","new":_stats(sorted(new_o,key=lambda x:x["total"])[:_MAX_OFFERS]),"used":_stats(sorted(used_o,key=lambda x:x["total"])[:_MAX_OFFERS]),"url":url}
            # JSON-LD fallback
            for blob in re.findall(r'<script type="application/ld\+json">(.*?)</script>', r.text, re.DOTALL):
                try:
                    d = json.loads(blob)
                    offers_raw = d.get("offers",[])
                    if isinstance(offers_raw, dict): offers_raw = [offers_raw]
                    all_o = []
                    for o in offers_raw:
                        p = float(o.get("price",0) or 0)
                        if p <= 0: continue
                        cond = "NEW" if "new" in str(o.get("itemCondition","")).lower() else "USED"
                        sel = (o.get("seller") or {}).get("name","BookFinder") if isinstance(o.get("seller"),dict) else "BookFinder"
                        all_o.append(_offer(p, 0, sel, "BF", cond))
                    if all_o:
                        new_o, used_o = _split_new_used(all_o)
                        return {"source":"bookfinder_jsonld","new":_stats(new_o),"used":_stats(used_o),"url":url}
                except Exception:
                    continue
        except Exception as e:
            logger.debug("bookfinder url=%s err=%s", url, e)
    return None

# ── Source 2: AbeBooks ───────────────────────────────────────────────────────

async def _src_abebooks(client: httpx.AsyncClient, isbn: str) -> Optional[dict]:
    url = f"https://www.abebooks.com/servlet/SearchResults?isbn={isbn}&n=100121503&cm_sp=snippet-_-srp1-_-title1"
    try:
        r = await client.get(url, headers=_hdrs("https://www.abebooks.com/"), timeout=18)
        if r.status_code != 200: return None
        html = r.text
        all_o = []
        # utag_data JSON
        m = re.search(r'window\.utag_data\s*=\s*(\{.*?\});\s*</script>', html, re.DOTALL)
        if m:
            try:
                d = json.loads(m.group(1))
                prices = d.get("product_price",[])
                ships  = d.get("product_shipping",[])
                conds  = d.get("product_condition",[])
                for i, ps in enumerate(prices):
                    try:
                        p  = float(str(ps).replace(",","").replace("$",""))
                        s  = float(str(ships[i] if i<len(ships) else 0).replace(",","").replace("$",""))
                        cd = str(conds[i] if i<len(conds) else "used").lower()
                        if p > 0:
                            all_o.append(_offer(p, s, "AbeBooks", "ABEBOOKS", "NEW" if "new" in cd else "USED"))
                    except Exception:
                        continue
            except Exception:
                pass
        # itemprop fallback
        if not all_o:
            for m2 in re.finditer(r'itemprop="price"[^>]*content="([\d.]+)"', html):
                p = float(m2.group(1))
                if p > 0:
                    all_o.append(_offer(p, 3.99, "AbeBooks", "ABEBOOKS", "USED"))
        if not all_o: return None
        new_o, used_o = _split_new_used(all_o)
        return {"source":"abebooks","new":_stats(new_o),"used":_stats(used_o),
                "url":f"https://www.abebooks.com/servlet/SearchResults?isbn={isbn}"}
    except Exception as e:
        logger.debug("abebooks err=%s", e)
        return None

# ── Source 3: ThriftBooks ────────────────────────────────────────────────────

async def _src_thriftbooks(client: httpx.AsyncClient, isbn: str) -> Optional[dict]:
    url = f"https://www.thriftbooks.com/browse/?b.search={isbn}"
    try:
        r = await client.get(url, headers=_hdrs("https://www.thriftbooks.com/"), timeout=18)
        if r.status_code != 200: return None
        html = r.text
        all_o = []
        # JSON-LD
        for blob in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL):
            try:
                d = json.loads(blob)
                offers_raw = d.get("offers",[])
                if isinstance(offers_raw, dict): offers_raw = [offers_raw]
                for o in offers_raw:
                    p = float(o.get("price",0) or 0)
                    if p <= 0: continue
                    cond = "NEW" if "new" in str(o.get("itemCondition","")).lower() else "USED"
                    all_o.append(_offer(p, 0, "ThriftBooks", "THRIFTBOOKS", cond))
            except Exception:
                continue
        # data-price fallback
        if not all_o:
            for m in re.finditer(r'"price"\s*:\s*"?([\d.]+)"?', html):
                p = float(m.group(1))
                if 0.5 < p < 500:
                    all_o.append(_offer(p, 0, "ThriftBooks", "THRIFTBOOKS", "USED"))
                    if len(all_o) >= 5: break
        if not all_o: return None
        new_o, used_o = _split_new_used(all_o)
        return {"source":"thriftbooks","new":_stats(new_o),"used":_stats(used_o),"url":url}
    except Exception as e:
        logger.debug("thriftbooks err=%s", e)
        return None

# ── Source 4: BetterWorldBooks ───────────────────────────────────────────────

async def _src_bwb(client: httpx.AsyncClient, isbn: str) -> Optional[dict]:
    url = f"https://www.betterworldbooks.com/search/results?q={isbn}"
    try:
        r = await client.get(url, headers=_hdrs("https://www.betterworldbooks.com/"), timeout=18)
        if r.status_code != 200: return None
        html = r.text
        all_o = []
        for blob in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL):
            try:
                d = json.loads(blob)
                offers_raw = d.get("offers",[])
                if isinstance(offers_raw, dict): offers_raw = [offers_raw]
                for o in offers_raw:
                    p = float(o.get("price",0) or 0)
                    if p <= 0: continue
                    cond = "NEW" if "new" in str(o.get("itemCondition","")).lower() else "USED"
                    all_o.append(_offer(p, 0, "BetterWorldBooks", "BETTERWORLDBOOKS", cond))
            except Exception:
                continue
        if not all_o:
            for m in re.finditer(r'data-price=["\']?([\d.]+)["\']?', html):
                p = float(m.group(1))
                if 0.5 < p < 500:
                    all_o.append(_offer(p, 0, "BetterWorldBooks", "BETTERWORLDBOOKS", "USED"))
                    if len(all_o) >= 5: break
        if not all_o: return None
        new_o, used_o = _split_new_used(all_o)
        return {"source":"bwb","new":_stats(new_o),"used":_stats(used_o),"url":url}
    except Exception as e:
        logger.debug("bwb err=%s", e)
        return None

# ── Source 5: Biblio ─────────────────────────────────────────────────────────

async def _src_biblio(client: httpx.AsyncClient, isbn: str) -> Optional[dict]:
    url = f"https://www.biblio.com/search/?q={isbn}&type=isbn"
    try:
        r = await client.get(url, headers=_hdrs("https://www.biblio.com/"), timeout=18)
        if r.status_code != 200: return None
        html = r.text
        all_o = []
        for blob in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL):
            try:
                d = json.loads(blob)
                offers_raw = d.get("offers",[])
                if isinstance(offers_raw, dict): offers_raw = [offers_raw]
                for o in offers_raw:
                    p = float(o.get("price",0) or 0)
                    if p <= 0: continue
                    cond = "NEW" if "new" in str(o.get("itemCondition","")).lower() else "USED"
                    all_o.append(_offer(p, 0, "Biblio", "BIBLIO", cond))
            except Exception:
                continue
        if not all_o:
            for m in re.finditer(r'class="[^"]*price[^"]*"[^>]*>\s*\$?([\d.]+)', html):
                p = float(m.group(1))
                if 0.5 < p < 500:
                    all_o.append(_offer(p, 0, "Biblio", "BIBLIO", "USED"))
                    if len(all_o) >= 5: break
        if not all_o: return None
        new_o, used_o = _split_new_used(all_o)
        return {"source":"biblio","new":_stats(new_o),"used":_stats(used_o),"url":url}
    except Exception as e:
        logger.debug("biblio err=%s", e)
        return None

# ── Source 6: Alibris ────────────────────────────────────────────────────────

async def _src_alibris(client: httpx.AsyncClient, isbn: str) -> Optional[dict]:
    url = f"https://www.alibris.com/search/books/isbn/{isbn}"
    try:
        r = await client.get(url, headers=_hdrs("https://www.alibris.com/"), timeout=18)
        if r.status_code != 200: return None
        html = r.text
        all_o = []
        for blob in re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL):
            try:
                d = json.loads(blob)
                offers_raw = d.get("offers",[])
                if isinstance(offers_raw, dict): offers_raw = [offers_raw]
                for o in offers_raw:
                    p = float(o.get("price",0) or 0)
                    if p <= 0: continue
                    cond = "NEW" if "new" in str(o.get("itemCondition","")).lower() else "USED"
                    all_o.append(_offer(p, 0, "Alibris", "ALIBRIS", cond))
            except Exception:
                continue
        if not all_o:
            for m in re.finditer(r'"price":\s*"?([\d.]+)"?', html):
                p = float(m.group(1))
                if 0.5 < p < 500:
                    all_o.append(_offer(p, 0, "Alibris", "ALIBRIS", "USED"))
                    if len(all_o) >= 5: break
        if not all_o: return None
        new_o, used_o = _split_new_used(all_o)
        return {"source":"alibris","new":_stats(new_o),"used":_stats(used_o),"url":url}
    except Exception as e:
        logger.debug("alibris err=%s", e)
        return None

# ── Merge results from all sources ───────────────────────────────────────────

def _merge(results: list[dict]) -> tuple[list, list]:
    """Combine offers from all sources, deduplicate by price+seller."""
    new_all, used_all = [], []
    seen = set()
    for r in results:
        if not r: continue
        for o in (r.get("new") or {}).get("offers", []):
            k = (o["seller_id"], o["total"])
            if k not in seen:
                seen.add(k); new_all.append(o)
        for o in (r.get("used") or {}).get("offers", []):
            k = (o["seller_id"], o["total"])
            if k not in seen:
                seen.add(k); used_all.append(o)
    new_all.sort(key=lambda x: x["total"])
    used_all.sort(key=lambda x: x["total"])
    return new_all[:_MAX_OFFERS], used_all[:_MAX_OFFERS]

# ── Main entry ───────────────────────────────────────────────────────────────

async def fetch_bookfinder(isbn: str) -> dict:
    isbn_clean = re.sub(r"[^0-9X]", "", isbn.upper().strip())

    cached = _cache_get(isbn_clean)
    if cached:
        age = int(time.time() - cached.get("ts", time.time()))
        return {**cached, "cached": True, "cache_age_s": age}

    await asyncio.sleep(random.uniform(0.2, 0.6))

    bf_url = f"https://www.bookfinder.com/isbn/{isbn_clean}/"

    async with httpx.AsyncClient(follow_redirects=True, timeout=25) as client:
        # Run all sources in parallel
        tasks = await asyncio.gather(
            _src_bookfinder(client, isbn_clean),
            _src_abebooks(client, isbn_clean),
            _src_thriftbooks(client, isbn_clean),
            _src_bwb(client, isbn_clean),
            _src_biblio(client, isbn_clean),
            _src_alibris(client, isbn_clean),
            return_exceptions=True,
        )

    results = [r for r in tasks if isinstance(r, dict)]
    sources_ok = [r["source"] for r in results if r]
    logger.info("bookfinder isbn=%s sources_ok=%s", isbn_clean, sources_ok)

    if not results:
        return {"ok": False, "error": "Tüm kaynaklar başarısız", "isbn": isbn_clean,
                "sources_tried": ["bookfinder","abebooks","thriftbooks","bwb","biblio","alibris"]}

    new_o, used_o = _merge(results)

    if not new_o and not used_o:
        return {"ok": False, "error": "Fiyat verisi bulunamadı", "isbn": isbn_clean, "sources_ok": sources_ok}

    all_t = [o["total"] for o in new_o + used_o]
    out = {
        "ok":           True,
        "isbn":         isbn_clean,
        "new":          _stats(new_o),
        "used":         _stats(used_o),
        "all_avg":      round(sum(all_t)/len(all_t),2) if all_t else None,
        "total_offers": len(new_o) + len(used_o),
        "bookfinder_url": bf_url,
        "sources":      sources_ok,
        "cached":       False,
        "cache_age_s":  0,
    }
    _cache_set(isbn_clean, out)
    return out
