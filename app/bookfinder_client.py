"""
Multi-source book price comparison — on-demand, button-triggered.
GET /bookfinder/{isbn}?condition=all|new|used

Sources (parallel):
  BookFinder · AbeBooks · ThriftBooks · BetterWorldBooks
  Biblio · Alibris · GoodwillBooks · HPB (Half Price Books)

Cache: 24 hours per ISBN
"""
from __future__ import annotations
import asyncio, json, logging, random, re, time
from pathlib import Path
from typing import Optional
import httpx
from app.core.config import get_settings
from app.core.json_store import file_lock, _read_unsafe, _write_unsafe

logger = logging.getLogger("trackerbundle.bookfinder")
_CACHE_TTL_S = 24 * 3600  # 24 saat
_MAX_OFFERS  = 25

_UA = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
]

SOURCE_LABELS = {
    "bookfinder":     "📚 BookFinder",
    "abebooks":       "📖 AbeBooks",
    "thriftbooks":    "♻️ ThriftBooks",
    "bwb":            "🌍 BetterWorldBooks",
    "biblio":         "📗 Biblio",
    "alibris":        "📕 Alibris",
    "goodwill":       "💛 GoodwillBooks",
    "hpb":            "🔴 HPB",
    # Bulk sellers
    "bookpal":        "📦 BookPal",
    "bookdepot":      "🏭 BookDepot",
    "textbookrush":   "⚡ TextbookRush",
    "campusbooks":    "🎓 CampusBooks",
    "chegg":          "🎒 Chegg",
    "valorebooks":    "📒 ValoreBooks",
    # Resale marketplaces (URL-only, no scraping)
    "mercari":        "🟠 Mercari",
    "depop":          "🔵 Depop",
    "poshmark":       "🩷 Poshmark",
    "etsy":           "🟡 Etsy",
}

def _source_urls(isbn: str) -> dict:
    """Search URLs per source for a given ISBN."""
    return {
        "bookfinder":   f"https://www.bookfinder.com/isbn/{isbn}/",
        "abebooks":     f"https://www.abebooks.com/servlet/SearchResults?isbn={isbn}&n=100121503",
        "thriftbooks":  f"https://www.thriftbooks.com/browse/?b.search={isbn}",
        "bwb":          f"https://www.betterworldbooks.com/search/results?q={isbn}",
        "biblio":       f"https://www.biblio.com/search/?q={isbn}&type=isbn",
        "alibris":      f"https://www.alibris.com/search/books/isbn/{isbn}",
        "goodwill":     f"https://www.goodwillbooks.com/search?query={isbn}",
        "hpb":          f"https://www.hpb.com/search?q={isbn}&type=product",
        # Bulk sellers
        "bookpal":      f"https://www.bookpal.com/search?q={isbn}",
        "bookdepot":    f"https://www.bookdepot.com/Store/Search.aspx?q={isbn}",
        "textbookrush": f"https://www.textbookrush.com/search?q={isbn}",
        "campusbooks":  f"https://www.campusbooks.com/search/{isbn}",
        "chegg":        f"https://www.chegg.com/search?q={isbn}",
        "valorebooks":  f"https://www.valorebooks.com/search?q={isbn}",
        # Resale marketplaces (search by ISBN)
        "mercari":      f"https://www.mercari.com/search/?keyword={isbn}",
        "depop":        f"https://www.depop.com/search/?q={isbn}",
        "poshmark":     f"https://poshmark.com/search?query={isbn}&type=listings",
        "etsy":         f"https://www.etsy.com/search?q={isbn}",
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

def _ua() -> str: return random.choice(_UA)

def _hdrs(ref: str = "https://www.google.com/") -> dict:
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
        "Referer": ref,
    }

def _o(price: float, ship: float, seller: str, sid: str, cond: str, url: str = "", desc: str = "") -> dict:
    return {"price": round(price,2), "shipping": round(ship,2),
            "total": round(price+ship,2), "seller": seller, "seller_id": sid,
            "condition": cond, "url": url, "desc": desc[:100]}

def _is_new(cond_str: str) -> bool:
    s = cond_str.lower()
    # schema.org URIs: NewCondition, UsedCondition, etc.
    if "usedcondition" in s or "goodcondition" in s or "acceptablecondition" in s or "likenewcondition" in s:
        return False
    if "newcondition" in s or "/new" in s:
        return True
    # Plain text
    used_words = ["used","good","acceptable","very good","like new","fair","poor"]
    if any(w in s for w in used_words):
        return False
    return "new" in s

def _jsonld_offers(html: str, seller: str, sid: str, default_ship: float = 0.0) -> list:
    offers = []
    for blob in re.findall(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', html, re.DOTALL):
        try:
            d = json.loads(blob)
            raw = d.get("offers", [])
            if isinstance(raw, dict): raw = [raw]
            for o in raw:
                p = float(o.get("price") or 0)
                if p <= 0: continue
                cond = "NEW" if _is_new(str(o.get("itemCondition",""))) else "USED"
                sel = seller
                if isinstance(o.get("seller"), dict):
                    sel = o["seller"].get("name", seller)
                offers.append(_o(p, default_ship, sel, sid, cond))
        except Exception:
            continue
    return offers

def _price_regex(html: str, seller: str, sid: str, ship: float = 0.0, cond: str = "USED", limit: int = 8) -> list:
    offers = []
    seen = set()
    for m in re.finditer(r'(?:data-price|"price"|itemprop=["\']price["\'])["\s:=]+["\']?([\d]+\.[\d]{2})["\']?', html):
        p = float(m.group(1))
        if 0.5 < p < 500 and p not in seen:
            seen.add(p)
            offers.append(_o(p, ship, seller, sid, cond))
            if len(offers) >= limit: break
    return offers

def _stats(offers: list) -> Optional[dict]:
    if not offers: return None
    offers = sorted(offers, key=lambda x: x["total"])[:_MAX_OFFERS]
    totals = [o["total"] for o in offers]
    return {"count": len(totals), "min": round(min(totals),2),
            "avg": round(sum(totals)/len(totals),2), "offers": offers}

# ── Source 1: BookFinder ─────────────────────────────────────────────────────
def _bf_rsc(html: str) -> Optional[dict]:
    for chunk in re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', html, re.DOTALL):
        if "newOffers" not in chunk and "usedOffers" not in chunk: continue
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
                    except Exception: break
        except Exception: continue
    return None

# Global IP block flag — 403/block tespit edilince diğer ISBN'leri deneme
_bookfinder_ip_blocked = False
_bookfinder_block_until = 0.0

async def _src_bookfinder(c: httpx.AsyncClient, isbn: str) -> Optional[dict]:
    global _bookfinder_ip_blocked, _bookfinder_block_until
    import time as _t

    # IP block aktifse skip et (1 saatlik cooldown)
    if _bookfinder_ip_blocked and _t.time() < _bookfinder_block_until:
        return None

    # Config flag: BOOKFINDER_ENABLED=false ile tamamen kapatılabilir
    s = get_settings()
    if not getattr(s, "bookfinder_enabled", True):
        return None

    for url in [f"https://www.bookfinder.com/isbn/{isbn}/",
                f"https://www.bookfinder.com/search/?keywords={isbn}&currency=USD&destination=us&mode=basic&lang=en&st=sh&ac=qr"]:
        try:
            r = await c.get(url, headers=_hdrs(), timeout=18)
            if r.status_code in (403, 429, 503):
                _bookfinder_ip_blocked = True
                _bookfinder_block_until = _t.time() + 3600  # 1 saat
                logger.warning("BookFinder IP engellendi (HTTP %d) — 1 saat skip edilecek", r.status_code)
                return None
            if r.status_code != 200:
                logger.warning("bookfinder non-200 url=%s status=%s", url, r.status_code)
                continue
            sr = _bf_rsc(r.text)
            if sr:
                def _po(o, cond):
                    p = float(o.get("priceInUsd") or 0)
                    s = float(o.get("shippingPriceInUsd") or 0)
                    if p <= 0: return None
                    aff = str(o.get("affiliate","BF"))
                    return _o(p, s, {"ABEBOOKS":"AbeBooks","ALIBRIS":"Alibris","BIBLIO":"Biblio",
                                     "THRIFTBOOKS":"ThriftBooks","BETTERWORLDBOOKS":"BetterWorldBooks"}.get(aff, aff.title()),
                              aff, cond, "", str(o.get("conditionText",""))[:80])
                new_o  = [x for x in [_po(o,"NEW")  for o in (sr.get("newOffers")  or [])] if x]
                used_o = [x for x in [_po(o,"USED") for o in (sr.get("usedOffers") or [])] if x]
                if new_o or used_o:
                    return {"source":"bookfinder","new":_stats(new_o),"used":_stats(used_o),"url":url}
            jld = _jsonld_offers(r.text, "BookFinder", "BF")
            if jld:
                new_o = [o for o in jld if o["condition"]=="NEW"]
                used_o = [o for o in jld if o["condition"]!="NEW"]
                return {"source":"bookfinder","new":_stats(new_o),"used":_stats(used_o),"url":url}
        except Exception as e:
            logger.warning("bookfinder src err url=%s: %s", url, e)
    return None

# ── Source 2: AbeBooks ───────────────────────────────────────────────────────
async def _src_abebooks(c: httpx.AsyncClient, isbn: str) -> Optional[dict]:
    url = f"https://www.abebooks.com/servlet/SearchResults?isbn={isbn}&n=100121503"
    try:
        r = await c.get(url, headers=_hdrs("https://www.abebooks.com/"), timeout=18)
        if r.status_code != 200: return None
        all_o = []
        m = re.search(r'window\.utag_data\s*=\s*(\{.*?\});\s*</script>', r.text, re.DOTALL)
        if m:
            try:
                d = json.loads(m.group(1))
                for i, ps in enumerate(d.get("product_price", [])):
                    p  = float(str(ps).replace(",","").replace("$",""))
                    s  = float(str((d.get("product_shipping") or [])[i:i+1][0] if d.get("product_shipping") else 0).replace(",","").replace("$",""))
                    raw_cd = str((d.get("product_condition") or [])[i:i+1][0] if d.get("product_condition") else "used")
                    cd = raw_cd.lower()
                    if p > 0: all_o.append(_o(p, s, "AbeBooks", "ABEBOOKS", "NEW" if _is_new(cd) else "USED"))
            except Exception: pass
        if not all_o:
            all_o = _jsonld_offers(r.text, "AbeBooks", "ABEBOOKS")
        if not all_o:
            all_o = _price_regex(r.text, "AbeBooks", "ABEBOOKS", 0.0)
        if not all_o: return None
        return {"source":"abebooks","new":_stats([o for o in all_o if o["condition"]=="NEW"]),
                "used":_stats([o for o in all_o if o["condition"]!="NEW"]),"url":url}
    except Exception as e:
        logger.debug("abebooks err=%s", e); return None

# ── Source 3: ThriftBooks ────────────────────────────────────────────────────
async def _src_thriftbooks(c: httpx.AsyncClient, isbn: str) -> Optional[dict]:
    url = f"https://www.thriftbooks.com/isbn/{isbn}/"
    try:
        r = await c.get(url, headers=_hdrs("https://www.thriftbooks.com/"), timeout=18)
        if r.status_code != 200: return None
        all_o = _jsonld_offers(r.text, "ThriftBooks", "THRIFTBOOKS")
        if not all_o: all_o = _price_regex(r.text, "ThriftBooks", "THRIFTBOOKS", 0)
        if not all_o: return None
        return {"source":"thriftbooks","new":_stats([o for o in all_o if o["condition"]=="NEW"]),
                "used":_stats([o for o in all_o if o["condition"]!="NEW"]),"url":url}
    except Exception as e:
        logger.debug("thriftbooks err=%s", e); return None

# ── Source 4: BetterWorldBooks ───────────────────────────────────────────────
async def _src_bwb(c: httpx.AsyncClient, isbn: str) -> Optional[dict]:
    url = f"https://www.betterworldbooks.com/search/results?q={isbn}"
    try:
        r = await c.get(url, headers=_hdrs("https://www.betterworldbooks.com/"), timeout=18)
        if r.status_code != 200: return None
        all_o = _jsonld_offers(r.text, "BetterWorldBooks", "BETTERWORLDBOOKS")
        if not all_o: all_o = _price_regex(r.text, "BetterWorldBooks", "BETTERWORLDBOOKS", 0)
        if not all_o: return None
        return {"source":"bwb","new":_stats([o for o in all_o if o["condition"]=="NEW"]),
                "used":_stats([o for o in all_o if o["condition"]!="NEW"]),"url":url}
    except Exception as e:
        logger.debug("bwb err=%s", e); return None

# ── Source 5: Biblio ─────────────────────────────────────────────────────────
async def _src_biblio(c: httpx.AsyncClient, isbn: str) -> Optional[dict]:
    url = f"https://www.biblio.com/search/?q={isbn}&type=isbn"
    try:
        r = await c.get(url, headers=_hdrs("https://www.biblio.com/"), timeout=18)
        if r.status_code != 200: return None
        all_o = _jsonld_offers(r.text, "Biblio", "BIBLIO")
        if not all_o: all_o = _price_regex(r.text, "Biblio", "BIBLIO", 0.0)
        if not all_o: return None
        return {"source":"biblio","new":_stats([o for o in all_o if o["condition"]=="NEW"]),
                "used":_stats([o for o in all_o if o["condition"]!="NEW"]),"url":url}
    except Exception as e:
        logger.debug("biblio err=%s", e); return None

# ── Source 6: Alibris ────────────────────────────────────────────────────────
async def _src_alibris(c: httpx.AsyncClient, isbn: str) -> Optional[dict]:
    url = f"https://www.alibris.com/search/books/isbn/{isbn}"
    try:
        r = await c.get(url, headers=_hdrs("https://www.alibris.com/"), timeout=18)
        if r.status_code != 200: return None
        all_o = _jsonld_offers(r.text, "Alibris", "ALIBRIS")
        if not all_o: all_o = _price_regex(r.text, "Alibris", "ALIBRIS", 0)
        if not all_o: return None
        return {"source":"alibris","new":_stats([o for o in all_o if o["condition"]=="NEW"]),
                "used":_stats([o for o in all_o if o["condition"]!="NEW"]),"url":url}
    except Exception as e:
        logger.debug("alibris err=%s", e); return None

# ── Source 7: GoodwillBooks ──────────────────────────────────────────────────
async def _src_goodwill(c: httpx.AsyncClient, isbn: str) -> Optional[dict]:
    url = f"https://www.goodwillbooks.com/search?query={isbn}"
    try:
        r = await c.get(url, headers=_hdrs("https://www.goodwillbooks.com/"), timeout=18)
        if r.status_code != 200: return None
        all_o = _jsonld_offers(r.text, "GoodwillBooks", "GOODWILL")
        if not all_o:
            # Goodwill uses Shopify — look for product JSON
            for m in re.finditer(r'"price":\s*"?(\d+)"?', r.text):
                p = float(m.group(1)) / 100  # Shopify prices in cents
                if 0.5 < p < 500:
                    all_o.append(_o(p, 0.0, "GoodwillBooks", "GOODWILL", "USED"))
                    if len(all_o) >= 6: break
        if not all_o: all_o = _price_regex(r.text, "GoodwillBooks", "GOODWILL", 0.0)
        if not all_o: return None
        return {"source":"goodwill","new":_stats([o for o in all_o if o["condition"]=="NEW"]),
                "used":_stats([o for o in all_o if o["condition"]!="NEW"]),"url":url}
    except Exception as e:
        logger.debug("goodwill err=%s", e); return None

# ── Source 8: HPB (Half Price Books) ────────────────────────────────────────
async def _src_hpb(c: httpx.AsyncClient, isbn: str) -> Optional[dict]:
    url = f"https://www.hpb.com/search?q={isbn}&type=product"
    try:
        r = await c.get(url, headers=_hdrs("https://www.hpb.com/"), timeout=18)
        if r.status_code != 200: return None
        all_o = _jsonld_offers(r.text, "HPB", "HPB")
        if not all_o:
            # HPB Shopify — prices in cents
            for m in re.finditer(r'"price":\s*(\d+)', r.text):
                p = float(m.group(1)) / 100
                if 0.5 < p < 500:
                    all_o.append(_o(p, 0.0, "Half Price Books", "HPB", "USED"))
                    if len(all_o) >= 6: break
        if not all_o: all_o = _price_regex(r.text, "Half Price Books", "HPB", 0.0)
        if not all_o: return None
        return {"source":"hpb","new":_stats([o for o in all_o if o["condition"]=="NEW"]),
                "used":_stats([o for o in all_o if o["condition"]!="NEW"]),"url":url}
    except Exception as e:
        logger.debug("hpb err=%s", e); return None


# ── Bulk book sellers ─────────────────────────────────────────────────────────

async def _src_bookpal(c: httpx.AsyncClient, isbn: str) -> Optional[dict]:
    """BookPal — bulk/wholesale new books, typically case-quantity discounts."""
    url = f"https://www.bookpal.com/search?q={isbn}"
    try:
        r = await c.get(url, headers=_hdrs("https://www.bookpal.com/"), timeout=18)
        if r.status_code != 200: return None
        all_o = _jsonld_offers(r.text, "BookPal", "BOOKPAL")
        if not all_o:
            all_o = _price_regex(r.text, "BookPal", "BOOKPAL", 0.0)
        if not all_o: return None
        return {"source": "bookpal",
                "new":  _stats([o for o in all_o if o["condition"] == "NEW"]),
                "used": _stats([o for o in all_o if o["condition"] != "NEW"]),
                "url":  url}
    except Exception as e:
        logger.debug("bookpal err=%s", e); return None


async def _src_bookdepot(c: httpx.AsyncClient, isbn: str) -> Optional[dict]:
    """BookDepot — Canadian bulk seller, deeply discounted remainders & overstock."""
    url = f"https://www.bookdepot.com/Store/Search.aspx?q={isbn}"
    try:
        r = await c.get(url, headers=_hdrs("https://www.bookdepot.com/"), timeout=18)
        if r.status_code != 200: return None
        all_o = _jsonld_offers(r.text, "BookDepot", "BOOKDEPOT")
        if not all_o:
            # BookDepot uses span class="price" or data-price attrs
            for m in re.finditer(r'\$\s*([\d]+\.[\d]{2})', r.text):
                p = float(m.group(1))
                if 0.5 < p < 300:
                    all_o.append(_o(p, 0.0, "BookDepot", "BOOKDEPOT", "NEW"))
                    if len(all_o) >= 8: break
        if not all_o: return None
        return {"source": "bookdepot",
                "new":  _stats([o for o in all_o if o["condition"] == "NEW"]),
                "used": _stats([o for o in all_o if o["condition"] != "NEW"]),
                "url":  url}
    except Exception as e:
        logger.debug("bookdepot err=%s", e); return None


async def _src_textbookrush(c: httpx.AsyncClient, isbn: str) -> Optional[dict]:
    """TextbookRush — textbook buyback + used textbooks, good for academic ISBNs."""
    url = f"https://www.textbookrush.com/search?q={isbn}"
    try:
        r = await c.get(url, headers=_hdrs("https://www.textbookrush.com/"), timeout=18)
        if r.status_code != 200: return None
        all_o = _jsonld_offers(r.text, "TextbookRush", "TBR")
        if not all_o:
            all_o = _price_regex(r.text, "TextbookRush", "TBR", 3.99)
        if not all_o: return None
        return {"source": "textbookrush",
                "new":  _stats([o for o in all_o if o["condition"] == "NEW"]),
                "used": _stats([o for o in all_o if o["condition"] != "NEW"]),
                "url":  url}
    except Exception as e:
        logger.debug("textbookrush err=%s", e); return None


async def _src_campusbooks(c: httpx.AsyncClient, isbn: str) -> Optional[dict]:
    """CampusBooks — price comparison aggregator for textbooks (new + used + rental)."""
    url = f"https://www.campusbooks.com/search/{isbn}"
    try:
        r = await c.get(url, headers=_hdrs("https://www.campusbooks.com/"), timeout=18)
        if r.status_code != 200: return None
        all_o = _jsonld_offers(r.text, "CampusBooks", "CB")
        if not all_o:
            all_o = _price_regex(r.text, "CampusBooks", "CB", 3.99)
        if not all_o: return None
        return {"source": "campusbooks",
                "new":  _stats([o for o in all_o if o["condition"] == "NEW"]),
                "used": _stats([o for o in all_o if o["condition"] != "NEW"]),
                "url":  url}
    except Exception as e:
        logger.debug("campusbooks err=%s", e); return None


async def _src_chegg(c: httpx.AsyncClient, isbn: str) -> Optional[dict]:
    """Chegg — textbook rental + used sales, strong for college textbooks."""
    url = f"https://www.chegg.com/search?q={isbn}"
    try:
        r = await c.get(url, headers=_hdrs("https://www.chegg.com/"), timeout=18)
        if r.status_code != 200: return None
        all_o = _jsonld_offers(r.text, "Chegg", "CHEGG")
        if not all_o:
            # Chegg uses JSON data in script tags
            for m in re.finditer(r'"price"\s*:\s*"?([\d]+\.[\d]{2})"?', r.text):
                p = float(m.group(1))
                if 0.5 < p < 400:
                    all_o.append(_o(p, 0.0, "Chegg", "CHEGG", "USED"))
                    if len(all_o) >= 6: break
        if not all_o: return None
        return {"source": "chegg",
                "new":  _stats([o for o in all_o if o["condition"] == "NEW"]),
                "used": _stats([o for o in all_o if o["condition"] != "NEW"]),
                "url":  url}
    except Exception as e:
        logger.debug("chegg err=%s", e); return None


# ── Merge ─────────────────────────────────────────────────────────────────────
def _merge(results: list) -> tuple[list, list]:
    new_all, used_all = [], []
    seen = set()
    for r in results:
        if not r: continue
        for o in (r.get("new") or {}).get("offers", []):
            k = (o["seller_id"], o["total"])
            if k not in seen: seen.add(k); new_all.append(o)
        for o in (r.get("used") or {}).get("offers", []):
            k = (o["seller_id"], o["total"])
            if k not in seen: seen.add(k); used_all.append(o)
    return (sorted(new_all, key=lambda x: x["total"])[:_MAX_OFFERS],
            sorted(used_all, key=lambda x: x["total"])[:_MAX_OFFERS])

# ── Main entry ───────────────────────────────────────────────────────────────
async def fetch_bookfinder(isbn: str, condition: str = "all", force: bool = False) -> dict:
    """condition: 'all' | 'new' | 'used'"""
    isbn_clean = re.sub(r"[^0-9X]", "", isbn.upper().strip())

    if not force:
        cached = _cache_get(isbn_clean)
        if cached:
            age = int(time.time() - cached.get("ts", time.time()))
            result = {**cached, "cached": True, "cache_age_s": age}
            # Apply condition filter on cached result too
            if condition == "new":
                result = {**result, "used": None}
            elif condition == "used":
                result = {**result, "new": None}
            # Update cheapest after filter
            all_offers = []
            if result.get("new"): all_offers += result["new"].get("offers", [])
            if result.get("used"): all_offers += result["used"].get("offers", [])
            if all_offers:
                all_offers.sort(key=lambda x: x["total"])
                result["cheapest"] = all_offers[0]["total"]
                result["total_offers"] = len(all_offers)
            return result

    await asyncio.sleep(random.uniform(0.2, 0.5))

    async with httpx.AsyncClient(follow_redirects=True, timeout=25) as client:
        tasks = await asyncio.gather(
            _src_bookfinder(client, isbn_clean),
            _src_abebooks(client, isbn_clean),
            _src_thriftbooks(client, isbn_clean),
            _src_bwb(client, isbn_clean),
            _src_biblio(client, isbn_clean),
            _src_alibris(client, isbn_clean),
            _src_goodwill(client, isbn_clean),
            _src_hpb(client, isbn_clean),
            # Bulk sellers
            _src_bookpal(client, isbn_clean),
            _src_bookdepot(client, isbn_clean),
            _src_textbookrush(client, isbn_clean),
            _src_campusbooks(client, isbn_clean),
            _src_chegg(client, isbn_clean),
            return_exceptions=True,
        )

    results = [r for r in tasks if isinstance(r, dict) and r]
    sources_ok = [r["source"] for r in results]
    logger.info("bookfinder isbn=%s sources=%s", isbn_clean, sources_ok)

    if not results:
        exceptions = [str(r) for r in tasks if isinstance(r, Exception)]
        logger.warning("bookfinder all failed isbn=%s exceptions=%s", isbn_clean, exceptions[:3])
        return {"ok": False, "error": "Tüm kaynaklar başarısız", "isbn": isbn_clean, "tried": 13, "hint": "VPS IP engellenmiş olabilir"}

    new_o, used_o = _merge(results)

    # Filter by condition
    if condition == "new":  used_o = []
    if condition == "used": new_o  = []

    if not new_o and not used_o:
        return {"ok": False, "error": "Fiyat verisi bulunamadı", "isbn": isbn_clean, "sources_ok": sources_ok}

    all_t = [o["total"] for o in new_o + used_o]
    cheapest = min(all_t) if all_t else None

    urls = _source_urls(isbn_clean)
    out = {
        "ok":           True,
        "isbn":         isbn_clean,
        "condition":    condition,
        "new":          _stats(new_o),
        "used":         _stats(used_o),
        "cheapest":     cheapest,
        "all_avg":      round(sum(all_t)/len(all_t),2) if all_t else None,
        "total_offers": len(new_o) + len(used_o),
        "bookfinder_url": f"https://www.bookfinder.com/isbn/{isbn_clean}/",
        "sources":      sources_ok,
        "source_labels": {s: SOURCE_LABELS.get(s, s) for s in sources_ok},
        "source_urls":  {s: urls.get(s, "") for s in sources_ok},
        "cached":       False,
        "cache_age_s":  0,
    }
    _cache_set(isbn_clean, out)
    return out
