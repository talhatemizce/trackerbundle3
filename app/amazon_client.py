"""
Amazon SP-API client — async, with LWA token caching + SigV4 signing.
Top 2 cheapest New + Used offers with A (FBA) / M (FBM) label.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import get_settings

logger = logging.getLogger("trackerbundle.amazon_client")

# ---- LWA token cache (same pattern as eBay) ----
_lwa_lock = asyncio.Lock()
_lwa_cache: Dict[str, Any] = {}

LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"


def _lwa_valid(tok: Dict[str, Any]) -> bool:
    return bool(tok.get("access_token")) and float(tok.get("expires_at", 0)) > time.time() + 60


async def _get_lwa_token(client: httpx.AsyncClient) -> str:
    global _lwa_cache

    if _lwa_valid(_lwa_cache):
        return _lwa_cache["access_token"]

    async with _lwa_lock:
        if _lwa_valid(_lwa_cache):
            return _lwa_cache["access_token"]

        s = get_settings()
        if not s.lwa_client_id or not s.lwa_client_secret or not s.lwa_refresh_token:
            raise RuntimeError("LWA_CLIENT_ID / LWA_CLIENT_SECRET / LWA_REFRESH_TOKEN eksik")

        r = await client.post(
            LWA_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": s.lwa_refresh_token,
                "client_id": s.lwa_client_id,
                "client_secret": s.lwa_client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
            timeout=30,
        )
        r.raise_for_status()
        j = r.json()

        _lwa_cache = {
            "access_token": j["access_token"],
            "expires_at": time.time() + int(j.get("expires_in", 3600)),
        }

    logger.info("Yeni LWA token alındı")
    return _lwa_cache["access_token"]


# ---- SigV4 signing (sync, runs in thread) ----
def _sign_request(method: str, url: str, headers: Dict[str, str]) -> Dict[str, str]:
    """SigV4 ile imzala. botocore gerekli."""
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest
    from botocore.credentials import Credentials

    s = get_settings()
    if not s.aws_access_key_id or not s.aws_secret_access_key:
        raise RuntimeError("AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY eksik")

    creds = Credentials(s.aws_access_key_id, s.aws_secret_access_key)
    req = AWSRequest(method=method, url=url, data=None, headers=headers)
    SigV4Auth(creds, "execute-api", s.aws_region).add_auth(req)
    return dict(req.headers)


# ---- Helpers ----
def _money(x: Any) -> float:
    if not x:
        return 0.0
    return float(x.get("Amount", 0.0) or 0.0)


def _safe_int(x: float) -> int:
    return int(round(x))


def _parse_offers(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """SP-API offers payload'ından normalize edilmiş offer listesi döndürür."""
    offers = payload.get("Offers") or []
    rows: List[Dict[str, Any]] = []

    for o in offers:
        lp = _money(o.get("ListingPrice"))
        ship = _money(o.get("Shipping"))
        total = lp + ship
        fba = bool(o.get("IsFulfilledByAmazon"))

        sub_cond = (o.get("ConditionInfo") or {}).get("SubCondition", "").lower().replace(" ", "_")
        rows.append({
            "total": round(total, 2),
            "total_int": _safe_int(total),
            "price": round(lp, 2),
            "ship": round(ship, 2),
            "fba": fba,
            "label": "A" if fba else "M",  # A=Amazon/FBA, M=Merchant/FBM
            "buybox": bool(o.get("IsBuyBoxWinner")),
            "prime": bool((o.get("PrimeInformation") or {}).get("IsPrime")),
            "seller_id": o.get("SellerId"),
            "sub_condition": sub_cond,  # "like_new" | "very_good" | "good" | "acceptable"
        })

    rows.sort(key=lambda x: x["total"])
    return rows


async def _fetch_offers(
    client: httpx.AsyncClient,
    asin: str,
    condition: str,
    marketplace_id: str,
    access_token: str,
) -> Dict[str, Any]:
    """Tek condition (New/Used) için offers çek."""
    s = get_settings()
    endpoint = s.spapi_endpoint.rstrip("/")
    url = f"{endpoint}/products/pricing/v0/items/{asin}/offers"

    # Full URL with query params (SigV4 imzası için gerekli)
    import urllib.parse
    qs = urllib.parse.urlencode({"MarketplaceId": marketplace_id, "ItemCondition": condition})
    full_url = f"{url}?{qs}"

    # Host header
    host = full_url.split("/")[2]

    base_headers = {
        "host": host,
        "x-amz-access-token": access_token,
        "content-type": "application/json",
    }

    # SigV4 imzala (sync, ama çok hızlı — CPU-bound değil)
    signed = await asyncio.to_thread(_sign_request, "GET", full_url, base_headers)

    # SP-API GetItemOffers: 0.5 req/sn limit — retry with backoff
    import time as _time
    for _attempt in range(3):
        r = await client.get(full_url, headers=signed, timeout=30)
        if r.status_code == 429:
            _wait = float(r.headers.get("x-amzn-RateLimit-Limit", "") or 0)
            _wait = max(_wait, [2, 6, 20][_attempt])
            logger.warning("SP-API 429 asin=%s cond=%s — %.0fs backoff (attempt %d/3)", asin, condition, _wait, _attempt+1)
            await asyncio.sleep(_wait)
            # Re-sign after backoff (token still valid)
            signed = await asyncio.to_thread(_sign_request, "GET", full_url, base_headers)
            continue
        break
    r.raise_for_status()

    payload = (r.json() or {}).get("payload") or {}
    rows = _parse_offers(payload)
    buybox = next((x for x in rows if x["buybox"]), None)

    return {
        "count": len(rows),
        "buybox": buybox,
        "top2": rows[:2],
    }


async def get_top2_prices(
    asin: str,
    marketplace_id: str | None = None,
) -> Dict[str, Any]:
    """
    ASIN için top 2 New + top 2 Used fiyatları döndürür.
    Her offer'da 'label' = 'A' (FBA) veya 'M' (FBM).

    Returns:
        {
            "asin": str,
            "marketplace_id": str,
            "new": {"count": N, "buybox": {...}|None, "top2": [...]},
            "used": {"count": N, "buybox": {...}|None, "top2": [...]},
        }
    """
    s = get_settings()
    mkt = (marketplace_id or s.spapi_marketplace_id).strip()

    async with httpx.AsyncClient(timeout=35) as client:
        access_token = await _get_lwa_token(client)

        # SP-API rate limit: New + Used paralel atınca 429 — sıralı yap
        new_data = await _fetch_offers(client, asin, "New", mkt, access_token)
        await asyncio.sleep(2.0)  # 0.5 req/sn = 2s arası boşluk
        used_data = await _fetch_offers(client, asin, "Used", mkt, access_token)

    return {
        "asin": asin,
        "marketplace_id": mkt,
        "new": new_data,
        "used": used_data,
    }


def format_telegram(data: Dict[str, Any]) -> str:
    """
    Telegram için kısa format:
    ASIN: 0132350884
    Used: $12 M | $15 A
    New: $33 A | $40 M
    """
    asin = data.get("asin", "?")

    def _fmt_top2(section: Dict[str, Any]) -> str:
        top2 = section.get("top2") or []
        if not top2:
            return "-"
        parts = []
        for o in top2:
            parts.append(f"${o['total_int']} {o['label']}")
        return " | ".join(parts)

    used_str = _fmt_top2(data.get("used", {}))
    new_str = _fmt_top2(data.get("new", {}))

    # Buybox bilgisi (varsa)
    used_bb = data.get("used", {}).get("buybox")
    new_bb = data.get("new", {}).get("buybox")
    bb_parts = []
    if used_bb:
        bb_parts.append(f"Used BB: ${used_bb['total_int']} {used_bb['label']}")
    if new_bb:
        bb_parts.append(f"New BB: ${new_bb['total_int']} {new_bb['label']}")
    bb_str = " | ".join(bb_parts) if bb_parts else ""

    msg = (
        f"🛒 <b>ASIN: {asin}</b>\n"
        f"Used: {used_str}\n"
        f"New: {new_str}\n"
    )
    if bb_str:
        msg += f"Buybox: {bb_str}\n"

    return msg


# ── SP-API getCatalogItem — BSR + metadata ─────────────────────────────────────
# Endpoint: GET /catalog/2022-04-01/items/{asin}
# Rate limit: 2 req/sn (daha cömert than getItemOffers)
# BSR: SalesRanks[] field içinde geliyor
# Ücretsiz, mevcut credentials ile çalışır

_catalog_cache: Dict[str, tuple] = {}
_CATALOG_TTL = 3600 * 2  # 2 saat — BSR saatlik güncellenir


async def get_catalog_item(asin: str) -> Dict[str, Any]:
    """
    SP-API Catalog Items API v2022-04-01 ile BSR + metadata çek.

    Returns:
        {
          "bsr":          int | None,   # Books kategorisi BSR
          "bsr_all":      int | None,   # tüm kategoriler BSR (en iyi)
          "title":        str,
          "authors":      list[str],
          "publisher":    str,
          "pub_date":     str,
          "page_count":   int | None,
          "binding":      str,          # Paperback / Hardcover / etc.
          "list_price":   float | None, # yayıncı liste fiyatı
        }
    """
    now = time.time()
    if asin in _catalog_cache:
        ts, data = _catalog_cache[asin]
        if now - ts < _CATALOG_TTL:
            return data

    s = get_settings()
    mkt = s.spapi_marketplace_id.strip()
    endpoint = s.spapi_endpoint.rstrip("/")
    url = f"{endpoint}/catalog/2022-04-01/items/{asin}"

    import urllib.parse
    qs = urllib.parse.urlencode({
        "marketplaceIds": mkt,
        "includedData": "attributes,salesRanks,summaries",
    })
    full_url = f"{url}?{qs}"
    host = full_url.split("/")[2]

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            access_token = await _get_lwa_token(client)
            base_headers = {
                "host": host,
                "x-amz-access-token": access_token,
                "content-type": "application/json",
            }
            signed = await asyncio.to_thread(_sign_request, "GET", full_url, base_headers)

            r = await client.get(full_url, headers=signed, timeout=15)
            if r.status_code == 429:
                logger.warning("getCatalogItem 429 asin=%s", asin)
                return {}
            if r.status_code == 404:
                logger.debug("getCatalogItem 404 asin=%s", asin)
                return {}
            if r.status_code != 200:
                logger.debug("getCatalogItem HTTP %d asin=%s", r.status_code, asin)
                return {}

            data = r.json()

            # ── Sales Ranks ──────────────────────────────────────────────────
            bsr_books = None  # Ana "Books" displayGroup BSR (en geniş kategori)
            bsr_all   = None  # Tüm kategorilerdeki en iyi (minimum) rank
            for rank_obj in (data.get("salesRanks") or []):
                # displayGroupRanks: ana kategori (örn. "Books" → #12,560,721)
                for rank in (rank_obj.get("displayGroupRanks") or []):
                    title_lower = (rank.get("title") or "").lower()
                    rk = rank.get("rank")
                    if rk is None:
                        continue
                    rk = int(rk)
                    # Ana "Books" kategorisi (tam eşleşme ya da başlıkta sadece "books")
                    if title_lower in ("books", "kindle store"):
                        if bsr_books is None or rk < bsr_books:
                            bsr_books = rk
                    if bsr_all is None or rk < bsr_all:
                        bsr_all = rk
                # classificationRanks: alt kategoriler (örn. "Children's Programming Books" → #569)
                # bsr_books için kullanma — sadece bsr_all'a yansıt
                for rank in (rank_obj.get("classificationRanks") or []):
                    rk = rank.get("rank")
                    if rk is None:
                        continue
                    rk = int(rk)
                    if bsr_all is None or rk < bsr_all:
                        bsr_all = rk

            # ── Attributes ───────────────────────────────────────────────────
            attrs = data.get("attributes") or {}

            def _attr(key, idx=0, field="value"):
                vals = attrs.get(key) or []
                return vals[idx].get(field) if vals else None

            title      = _attr("item_name")
            publisher  = _attr("publisher")
            pub_date   = _attr("publication_date") or _attr("item_publication_date")
            page_count = _attr("number_of_pages")
            binding    = _attr("binding")
            list_price = None
            lp_raw = attrs.get("list_price") or []
            if lp_raw:
                amt = lp_raw[0].get("amount")
                if amt: list_price = float(amt)

            authors = []
            for c in (attrs.get("contributors") or []):
                if c.get("role", {}).get("value", "").lower() == "author":
                    name = (c.get("name") or [{}])[0].get("value", "")
                    if name: authors.append(name)

            # ── Summaries fallback ────────────────────────────────────────────
            for s_obj in (data.get("summaries") or []):
                if not title:
                    title = s_obj.get("itemName")
                if not publisher:
                    publisher = s_obj.get("brand")

            result = {
                "bsr":        bsr_books,
                "bsr_all":    bsr_all,
                "title":      title or "",
                "authors":    authors,
                "publisher":  publisher or "",
                "pub_date":   pub_date or "",
                "page_count": int(page_count) if page_count else None,
                "binding":    binding or "",
                "list_price": list_price,
            }
            _catalog_cache[asin] = (now, result)
            if bsr_books:
                logger.info("getCatalogItem asin=%s bsr_books=%d", asin, bsr_books)
            return result

    except Exception as e:
        logger.warning("getCatalogItem error asin=%s: %s", asin, e)
        return {}
