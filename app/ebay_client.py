from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import get_settings
from app.core.json_store import read_json, write_json

logger = logging.getLogger("trackerbundle.ebay_client")

BOOKS_CATEGORY_ID = "267"

_token_lock = asyncio.Lock()
_token_cache: Dict[str, Any] = {}


def _oauth_url() -> str:
    s = get_settings()
    return "https://api.sandbox.ebay.com/identity/v1/oauth2/token" if s.ebay_env == "sandbox" else "https://api.ebay.com/identity/v1/oauth2/token"


def _browse_base() -> str:
    s = get_settings()
    return "https://api.sandbox.ebay.com/buy/browse/v1" if s.ebay_env == "sandbox" else "https://api.ebay.com/buy/browse/v1"


def _token_valid(tok: Dict[str, Any]) -> bool:
    return bool(tok.get("access_token")) and float(tok.get("expires_at", 0)) > time.time() + 60


def _load_token_from_disk() -> Dict[str, Any]:
    try:
        s = get_settings()
        return read_json(s.resolved_ebay_token_file(), default={})
    except Exception:
        return {}


def _save_token_to_disk(tok: Dict[str, Any]) -> None:
    try:
        s = get_settings()
        write_json(s.resolved_ebay_token_file(), tok)
    except Exception as e:
        logger.warning("Token disk'e yazılamadı: %s", e)


async def get_app_token(client: httpx.AsyncClient) -> str:
    global _token_cache

    if _token_valid(_token_cache):
        return _token_cache["access_token"]

    async with _token_lock:
        if _token_valid(_token_cache):
            return _token_cache["access_token"]

        disk_tok = _load_token_from_disk()
        if _token_valid(disk_tok):
            _token_cache = disk_tok
            return _token_cache["access_token"]

        s = get_settings()
        if not s.ebay_client_id or not s.ebay_client_secret:
            raise RuntimeError("EBAY_CLIENT_ID / EBAY_CLIENT_SECRET eksik")

        r = await client.post(
            _oauth_url(),
            data={"grant_type": "client_credentials", "scope": "https://api.ebay.com/oauth/api_scope"},
            auth=(s.ebay_client_id, s.ebay_client_secret),
            timeout=20,
        )
        r.raise_for_status()
        j = r.json()

        new_tok = {
            "access_token": j["access_token"],
            "expires_at": time.time() + int(j.get("expires_in", 7200)),
        }
        _token_cache = new_tok

    _save_token_to_disk(new_tok)
    logger.info("Yeni eBay token alındı")
    return new_tok["access_token"]


def normalize_condition(cond_text: Optional[str], condition_id: Optional[int | str]) -> str:
    # conditionId öncelikli
    try:
        cid = int(condition_id) if condition_id is not None else None
    except (ValueError, TypeError):
        cid = None

    if cid is not None:
        mapping = {
            1000: "brand_new",
            1500: "brand_new",
            1750: "brand_new",
            2000: "like_new",
            2500: "like_new",
            2750: "like_new",
            3000: "very_good",
            4000: "good",
            5000: "acceptable",
            6000: "acceptable",
        }
        if cid in mapping:
            return mapping[cid]

    t = (cond_text or "").lower().strip()
    if not t:
        return "used_all"
    if "brand" in t and "new" in t:
        return "brand_new"
    if "like new" in t:
        return "like_new"
    if "very good" in t:
        return "very_good"
    if "good" in t:
        return "good"
    if "acceptable" in t:
        return "acceptable"
    if "used" in t or "pre-owned" in t:
        return "used_all"
    if "new" in t:
        return "brand_new"
    return "used_all"


# ── ISBN-10 ↔ ISBN-13 dönüşüm ──────────────────────────────────────────────────

def isbn10_to_isbn13(isbn10: str) -> Optional[str]:
    """ISBN-10'u ISBN-13'e (978-prefix) çevir. Checksum doğrulanır."""
    s = isbn10.replace("-", "").replace(" ", "").upper().strip()
    if len(s) != 10:
        return None
    raw = "978" + s[:9]
    total = sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(raw))
    check = (10 - (total % 10)) % 10
    return raw + str(check)


def isbn13_to_isbn10(isbn13: str) -> Optional[str]:
    """978-prefix'li ISBN-13'ü ISBN-10'a çevir. Checksum doğrulanır."""
    s = isbn13.replace("-", "").replace(" ", "").strip()
    if len(s) != 13 or not s.startswith("978"):
        return None
    body = s[3:12]
    total = sum(int(c) * (10 - i) for i, c in enumerate(body))
    check = (11 - (total % 11)) % 11
    check_char = "X" if check == 10 else str(check)
    return body + check_char


def isbn_variants(isbn: str) -> List[str]:
    """ISBN için tüm normalize formları döndür (13 varsa 10'unu da ekle, vs.)."""
    s = isbn.replace("-", "").replace(" ", "").upper().strip()
    variants = {s}
    if len(s) == 13:
        alt = isbn13_to_isbn10(s)
        if alt:
            variants.add(alt)
    elif len(s) == 10:
        alt = isbn10_to_isbn13(s)
        if alt:
            variants.add(alt)
    return list(variants)


# ── Shipping-aware total price ────────────────────────────────────────────────

def item_total_price(item: Dict[str, Any]) -> Optional[float]:
    """
    Item toplam fiyatı = fiyat + shipping.
    Shipping 'Calculated' veya bilinmiyorsa None döner → item SKIP edilir.
    """
    try:
        price = float(item.get("price", {}).get("value", 0) or 0)
    except (TypeError, ValueError):
        return None

    opts = item.get("shippingOptions")

    # shippingOptions field tamamen yok → bilinmiyor, skip
    if opts is None:
        return None

    ship = 0.0
    if opts:
        opt = opts[0]
        # "Calculated" veya "LOCAL_PICKUP" → kesin maliyet yok, skip
        ship_type = (opt.get("shippingServiceType") or opt.get("shippingType") or "").upper()
        if "CALCULATED" in ship_type or "LOCAL" in ship_type or "PICKUP" in ship_type:
            return None

        cost_val = opt.get("shippingCost", {}).get("value")
        if cost_val is None:
            # Opsiyon var ama fiyat yok → bilinmiyor, skip
            return None
        try:
            ship = float(cost_val)
        except (TypeError, ValueError):
            return None
    # opts == [] → free shipping (Browse API davranışı)

    return round(price + ship, 2)


def _safe_int(x: Any) -> Optional[int]:
    try:
        return int(round(float(x)))
    except Exception:
        return None


async def finding_sold_stats(
    client: httpx.AsyncClient,
    isbn: str,
    *,
    condition_filter: Optional[str] = None,
    max_entries: int = 50,
) -> Dict[str, Any]:
    """
    Finding API (findCompletedItems) ile satılmış item istatistikleri.
    condition_filter: None=hepsi, "used"=sadece used, "new"=sadece new.

    Returns:
        {
            "isbn": str,
            "sold_count": int,
            "sold_min": int|None,
            "sold_max": int|None,
            "sold_avg": int|None,
            "by_condition": {bucket: {"count": N, "avg": M, "min": X, "max": Y}},
        }
    """
    s = get_settings()
    app_id = s.ebay_app_id or s.ebay_client_id
    if not app_id:
        raise RuntimeError("EBAY_APP_ID (veya EBAY_CLIENT_ID) eksik")

    isbn_clean = isbn.replace("-", "").replace(" ", "").strip()

    params: Dict[str, str] = {
        "OPERATION-NAME": "findCompletedItems",
        "SERVICE-VERSION": "1.13.0",
        "SECURITY-APPNAME": app_id,
        "RESPONSE-DATA-FORMAT": "JSON",
        "REST-PAYLOAD": "true",
        "keywords": isbn_clean,
        "categoryId": BOOKS_CATEGORY_ID,
        "paginationInput.entriesPerPage": str(max(1, min(max_entries, 100))),
        "itemFilter(0).name": "SoldItemsOnly",
        "itemFilter(0).value": "true",
    }

    # condition filtre (opsiyonel)
    filter_idx = 1
    if condition_filter == "new":
        params[f"itemFilter({filter_idx}).name"] = "Condition"
        params[f"itemFilter({filter_idx}).value"] = "New"
        filter_idx += 1
    elif condition_filter == "used":
        params[f"itemFilter({filter_idx}).name"] = "Condition"
        params[f"itemFilter({filter_idx}).value(0)"] = "Used"
        params[f"itemFilter({filter_idx}).value(1)"] = "Good"
        params[f"itemFilter({filter_idx}).value(2)"] = "Very Good"
        params[f"itemFilter({filter_idx}).value(3)"] = "Acceptable"
        filter_idx += 1

    r = await client.get(
        "https://svcs.ebay.com/services/search/FindingService/v1",
        params=params,
        timeout=20,
    )
    r.raise_for_status()
    j = r.json()

    # Parse nested Finding API JSON
    totals: List[float] = []
    by_cond: Dict[str, List[float]] = {}

    try:
        resp = (j.get("findCompletedItemsResponse") or [{}])[0]
        sr = (resp.get("searchResult") or [{}])[0]
        items = sr.get("item") or []

        for it in items:
            # selling price
            selling = (it.get("sellingStatus") or [{}])[0]
            cur = (selling.get("currentPrice") or [{}])[0]
            price_val = cur.get("__value__")
            if price_val is None:
                continue

            try:
                price_f = float(price_val)
            except (TypeError, ValueError):
                continue

            # shipping
            ship_f = 0.0
            try:
                ship_info = (it.get("shippingInfo") or [{}])[0]
                ship_cost = (ship_info.get("shippingServiceCost") or [{}])[0]
                sv = ship_cost.get("__value__")
                if sv is not None:
                    ship_f = float(sv)
            except Exception:
                ship_f = 0.0

            total_f = price_f + ship_f
            totals.append(total_f)

            # condition bucket
            cond_raw = ""
            try:
                cond_info = (it.get("condition") or [{}])[0]
                cond_raw = (cond_info.get("conditionDisplayName") or [""]) [0]
            except Exception:
                pass
            bucket = normalize_condition(cond_raw, None)
            by_cond.setdefault(bucket, []).append(total_f)
    except Exception:
        logger.exception("Finding API parse error for isbn=%s", isbn)

    result: Dict[str, Any] = {
        "isbn": isbn_clean,
        "sold_count": len(totals),
        "sold_min": _safe_int(min(totals)) if totals else None,
        "sold_max": _safe_int(max(totals)) if totals else None,
        "sold_avg": _safe_int(sum(totals) / len(totals)) if totals else None,
        "by_condition": {},
    }

    for bucket, vals in by_cond.items():
        result["by_condition"][bucket] = {
            "count": len(vals),
            "avg": _safe_int(sum(vals) / len(vals)),
            "min": _safe_int(min(vals)),
            "max": _safe_int(max(vals)),
        }

    return result


def _isbn_strict_match(item: Dict[str, Any], variants: List[str]) -> bool:
    """
    Item'ın gerçekten bu ISBN'e ait olduğunu doğrula.
    1. GTIN/ISBN alanlarına bak (en güvenilir)
    2. Title'da herhangi bir variant geçiyor mu kontrol et (post-filter fallback)
    """
    # Browse API bazen epid/gtin alanlarını döndürür
    for field in ("gtin", "epid", "isbn"):
        val = (item.get(field) or "").replace("-", "").replace(" ", "").upper()
        if val and any(val == v for v in variants):
            return True

    # additionalImages, localizedAspects gibi alanlarda da olabilir
    for aspect in (item.get("localizedAspects") or []):
        if aspect.get("name", "").upper() in ("ISBN", "EAN", "GTIN"):
            aval = (aspect.get("value") or "").replace("-", "").replace(" ", "").upper()
            if any(aval == v for v in variants):
                return True

    # Son çare: title substring (en az bir variant geçmeli)
    title = item.get("title", "").replace("-", "").replace(" ", "").upper()
    return any(v in title for v in variants)


async def _browse_fetch_one(
    client: httpx.AsyncClient,
    token: str,
    q: str,
    limit: int,
) -> List[Dict[str, Any]]:
    """Tek bir q= sorgusu çeker."""
    params = {
        "q": q,
        "limit": str(max(1, min(limit, 200))),
        "category_ids": BOOKS_CATEGORY_ID,
        "filter": "buyingOptions:{FIXED_PRICE}",
    }
    headers = {"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": "EBAY_US"}
    r = await client.get(f"{_browse_base()}/item_summary/search", params=params, headers=headers, timeout=20)
    r.raise_for_status()
    return r.json().get("itemSummaries") or []


async def browse_search_isbn(
    client: httpx.AsyncClient,
    isbn: str,
    limit: int = 50,
    strict: bool = False,
) -> List[Dict[str, Any]]:
    """
    ISBN için eBay Browse API araması.

    - Her iki ISBN formatını (10 ve 13 haneli) ayrı sorgularla çeker, birleştirir.
    - strict=False (default): Sonuçlara güven; q=ISBN zaten ISBN'e özel arama yapar.
    - strict=True: GTIN/localizedAspects/title'da ISBN bulunamayan item'ları düşürür.
      UYARI: Browse item_summary/search, GTIN ve localizedAspects döndürmez (item
      detail call gerektirir). Title'da ISBN nadiren yazar. Bu mod pratikte tüm
      sonuçları düşürebilir — yalnızca özel test senaryolarında kullan.
    - Shipping bilinmiyorsa item downstream'de None döner (item_total_price).
    """
    token = await get_app_token(client)
    isbn_clean = isbn.replace("-", "").replace(" ", "").upper().strip()
    variants = isbn_variants(isbn_clean)

    # Her variant için paralel sorgu (10 + 13 hane)
    tasks = [_browse_fetch_one(client, token, v, limit) for v in variants]
    results_per_query = await asyncio.gather(*tasks, return_exceptions=True)

    seen_ids: set[str] = set()
    combined: List[Dict[str, Any]] = []
    for res in results_per_query:
        if isinstance(res, Exception):
            logger.warning("browse_search ISBN variant error: %s", res)
            continue
        for item in res:
            item_id = item.get("itemId") or ""
            if item_id and item_id not in seen_ids:
                seen_ids.add(item_id)
                combined.append(item)

    if strict:
        before = len(combined)
        combined = [it for it in combined if _isbn_strict_match(it, variants)]
        filtered = before - len(combined)
        if filtered:
            logger.info("isbn=%s strict filter dropped %d/%d items", isbn_clean, filtered, before)

    return combined
