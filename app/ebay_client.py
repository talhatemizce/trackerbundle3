from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import get_settings
from app.core.json_store import read_json, write_json
import app.finding_cache as finding_cache

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
            3000: "like_new",    # eBay Books: Like New
            4000: "very_good",   # eBay Books: Very Good
            5000: "good",        # eBay Books: Good
            6000: "acceptable",  # eBay Books: Acceptable
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

def item_total_price(
    item: Dict[str, Any],
    calc_ship_est: Optional[float] = None,
) -> Optional[float]:
    """
    Item toplam fiyatı = fiyat + shipping.
    CALCULATED/LOCAL/PICKUP shipping:
      - calc_ship_est > 0 → heuristic kullan (env CALCULATED_SHIP_ESTIMATE_USD)
      - calc_ship_est == 0 veya None → None döner, item skip edilir
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
    ship_estimated = False
    if opts:
        opt = opts[0]
        # Browse API uses shippingCostType; shippingServiceType/shippingType are fallbacks
        ship_type = (opt.get("shippingCostType") or opt.get("shippingServiceType") or opt.get("shippingType") or "").upper()
        if "CALCULATED" in ship_type or "LOCAL" in ship_type or "PICKUP" in ship_type or "NOT_SPECIFIED" in ship_type:
            if calc_ship_est and calc_ship_est > 0:
                ship = calc_ship_est
                ship_estimated = True
            else:
                return None

        if not ship_estimated:
            cost_val = opt.get("shippingCost", {}).get("value")
            if cost_val is None:
                # No cost value + no type info → treat as unknown/CALCULATED
                if calc_ship_est and calc_ship_est > 0:
                    ship = calc_ship_est
                    ship_estimated = True
                else:
                    return None
            else:
                try:
                    ship = float(cost_val)
                except (TypeError, ValueError):
                    return None
    # opts == [] → free shipping

    result = round(price + ship, 2)
    # Always explicitly annotate (prevents stale True if dict is reused)
    item["_shipping_estimated"] = ship_estimated
    return result


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
    # ── Backoff kontrolü ─────────────────────────────────────────────────────
    if finding_cache.is_rate_limited():
        st = finding_cache.rate_limit_status()
        logger.warning("Finding API backoff aktif (%.0fs kaldı) — finding_sold_stats skip", st.get("remaining_seconds", 0))
        raise RuntimeError("Finding API rate-limited (backoff aktif)")

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
    if not r.is_success:
        body_text = r.text[:600]
        logger.error("Finding API HTTP %d isbn=%s body=%s", r.status_code, isbn_clean, body_text)
        if r.status_code in (429, 500) and any(
            kw in body_text for kw in ("RateLimiter", "exceeded operation", "rate limit", "quota")
        ):
            finding_cache.set_rate_limited(23)
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
    Summary-level (item_summary) alanlarına bakarak hızlı eşleşme dener.
    UYARI: Browse search summary'de gtin/localizedAspects döndürülmez.
    Güvenilir strict match için _product_isbn_match() + item detail kullan.
    Yalnızca debug endpoint'inde (strict=True testinde) kullanılır.
    """
    vs = {v.replace("-", "").replace(" ", "").upper() for v in variants}

    # Browse summary bazen gtin/epid/isbn döndürür (GTIN search'te garantili)
    for field in ("gtin", "epid", "isbn"):
        val = (item.get(field) or "").replace("-", "").replace(" ", "").upper()
        if val and val in vs:
            return True

    # localizedAspects (GTIN search veya fieldgroups=EXTENDED ile döner)
    for aspect in (item.get("localizedAspects") or []):
        if aspect.get("name", "").upper() in ("ISBN", "EAN", "GTIN", "ISBN-10", "ISBN-13"):
            aval = (aspect.get("value") or "").replace("-", "").replace(" ", "").upper()
            if aval in vs:
                return True

    # Title substring (nadiren çalışır ama fallback)
    title = item.get("title", "").replace("-", "").replace(" ", "").upper()
    return any(v in title for v in vs)


def _product_isbn_match(detail: Dict[str, Any], variants: List[str]) -> bool:
    """
    Item detail response'unda (fieldgroups=PRODUCT) ISBN eşleşmesi kontrol eder.
    Kontrol edilen alanlar:
      1. product.gtins[] — ePID varsa eBay otomatik doldurur
      2. localizedAspects[] — satıcının girdiği "ISBN" / "EAN" / "GTIN" değerleri
    GTIN search'ten gelen item'lar zaten kesin eşleşmedir, bu fonksiyon
    yalnızca keyword fallback item'larını doğrulamak için kullanılır.
    """
    vs = {v.replace("-", "").replace(" ", "").upper() for v in variants}

    # product.gtins (ePID matched items)
    product = detail.get("product") or {}
    for gtin in (product.get("gtins") or []):
        if gtin.replace("-", "").upper() in vs:
            return True

    # localizedAspects (seller-provided)
    for asp in (detail.get("localizedAspects") or []):
        if asp.get("name", "").upper() in ("ISBN", "EAN", "GTIN", "ISBN-10", "ISBN-13", "UPC"):
            val = (asp.get("value") or "").replace("-", "").replace(" ", "").upper()
            if val in vs:
                return True

    return False


async def _get_item_detail(
    client: httpx.AsyncClient,
    token: str,
    item_id: str,
) -> Dict[str, Any]:
    """Item detail'ı fieldgroups=PRODUCT ile çeker (ISBN doğrulaması için)."""
    headers = {
        "Authorization": f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
    }
    r = await client.get(
        f"{_browse_base()}/item/{item_id}",
        params={"fieldgroups": "PRODUCT"},
        headers=headers,
        timeout=15,
    )
    r.raise_for_status()
    return r.json()


async def _strict_verify(
    client: httpx.AsyncClient,
    token: str,
    items: List[Dict[str, Any]],
    variants: List[str],
    top_n: int = 20,
    concurrency: int = 5,
) -> List[Dict[str, Any]]:
    """
    En ucuz top_n item'ı item detail (fieldgroups=PRODUCT) ile doğrular.
    product.gtins veya localizedAspects'te ISBN yoksa DROP edilir.
    top_n'in ötesindeki item'lar doğrulama maliyeti göz önünde bulundurularak DROP edilir.
    Network hatası durumunda fail-open (item dahil edilir).
    """
    if not items:
        return []

    def _price(it: Dict[str, Any]) -> float:
        try:
            return float(it.get("price", {}).get("value", 9999))
        except Exception:
            return 9999.0

    sorted_items = sorted(items, key=_price)
    to_verify = sorted_items[:top_n]
    sem = asyncio.Semaphore(concurrency)

    async def _check(it: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        item_id = it.get("itemId") or ""
        if not item_id:
            return None
        async with sem:
            try:
                detail = await _get_item_detail(client, token, item_id)
                if _product_isbn_match(detail, variants):
                    return it
                logger.debug("isbn strict FAIL item=%s", item_id)
                return None
            except Exception as e:
                # Network/auth hataları → fail-open (item'ı dahil et)
                logger.warning("item=%s detail fetch error (%s) — fail-open", item_id, e)
                return it

    results = await asyncio.gather(*[_check(it) for it in to_verify])
    verified = [r for r in results if r is not None]
    dropped = len(to_verify) - len(verified)
    if dropped:
        logger.info("Strict verify: %d/%d passed, %d dropped (wrong ISBN)", len(verified), len(to_verify), dropped)
    return verified



# ─── Hybrid Verification (N=15) ────────────────────────────────────────────────

HYBRID_VERIFY_N = 15        # kaç item'ı doğrulayacağız
HYBRID_CONCURRENCY = 5      # paralel getItem çağrısı

# UNVERIFIED kabul eşikleri (total / limit)
UNVERIFIED_USED_RATIO = 0.60
UNVERIFIED_NEW_RATIO  = 0.70
_NEW_BUCKETS = {"brand_new"}


def _unverified_threshold(bucket: str, limit: float) -> float:
    """UNVERIFIED_SUPER_DEAL için maksimum total."""
    ratio = UNVERIFIED_NEW_RATIO if bucket in _NEW_BUCKETS else UNVERIFIED_USED_RATIO
    return limit * ratio


async def hybrid_verify_items(
    client: httpx.AsyncClient,
    isbn: str,
    items: List[Dict[str, Any]],
    limit_map: Dict[str, float],   # {item_id: effective_limit}
    bucket_map: Dict[str, str],    # {item_id: bucket}
    concurrency: int = HYBRID_CONCURRENCY,
) -> List[Dict[str, Any]]:
    """
    Hybrid verification pipeline:
      1. Token al
      2. Items'ı total fiyatına göre sırala, en ucuz N=15 seç
      3. Her biri için GET /item/{id}?fieldgroups=PRODUCT
      4. Karar:
         - CONFIRMED      : product.gtins eşleşiyor   → deal (limit altıysa)
         - UNVERIFIED_SUPER_DEAL : gtins yok/uyuşmuyor ama total <= threshold
         - DROP           : gtins uyuşmuyor + fiyat threshold üstünde

    Her item'a şu field'lar eklenerek döner:
      _match_quality      : "CONFIRMED" | "UNVERIFIED_SUPER_DEAL"
      _verification_reason: "gtins_match" | "gtins_missing" | "gtins_mismatch"
      _verified           : True / False
    """
    if not items:
        return []

    token = await get_app_token(client)
    isbn_clean = isbn.replace("-", "").replace(" ", "").upper().strip()
    variants = isbn_variants(isbn_clean)

    def _price_key(it: Dict[str, Any]) -> float:
        try:
            return float(it.get("price", {}).get("value", 9999))
        except Exception:
            return 9999.0

    sorted_items = sorted(items, key=_price_key)
    to_verify = sorted_items[:HYBRID_VERIFY_N]
    sem = asyncio.Semaphore(concurrency)

    async def _check_one(it: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        item_id = it.get("itemId") or ""
        if not item_id:
            return None

        bucket = bucket_map.get(item_id, "used_all")
        limit  = limit_map.get(item_id, 9999.0)
        threshold = _unverified_threshold(bucket, limit)

        async with sem:
            try:
                detail = await _get_item_detail(client, token, item_id)
            except Exception as e:
                logger.warning("hybrid_verify: item=%s detail fetch failed (%s) — fail-open UNVERIFIED", item_id, e)
                # fail-open: treat as gtins_missing, apply unverified threshold
                detail = {}

        product = detail.get("product") or {}
        gtins   = [g.replace("-", "").upper() for g in (product.get("gtins") or [])]

        if not gtins:
            reason = "gtins_missing"
        else:
            matched = any(g in {v.upper() for v in variants} for g in gtins)
            reason = "gtins_match" if matched else "gtins_mismatch"

        total = _price_key(it)  # approximation for threshold check

        if reason == "gtins_match":
            result = it.copy()
            result["_match_quality"] = "CONFIRMED"
            result["_verification_reason"] = reason
            result["_verified"] = True
            logger.info("hybrid_verify isbn=%s item=%s → CONFIRMED (gtins_match)", isbn, item_id)
            return result
        else:
            # UNVERIFIED path: only accept if total is super cheap
            if total <= threshold:
                result = it.copy()
                result["_match_quality"] = "UNVERIFIED_SUPER_DEAL"
                result["_verification_reason"] = reason
                result["_verified"] = False
                logger.info(
                    "hybrid_verify isbn=%s item=%s → UNVERIFIED_SUPER_DEAL reason=%s total=%.2f threshold=%.2f",
                    isbn, item_id, reason, total, threshold,
                )
                return result
            else:
                logger.debug(
                    "hybrid_verify isbn=%s item=%s → DROP reason=%s total=%.2f > threshold=%.2f",
                    isbn, item_id, reason, total, threshold,
                )
                return None

    results = await asyncio.gather(*[_check_one(it) for it in to_verify])
    accepted = [r for r in results if r is not None]
    logger.info(
        "hybrid_verify isbn=%s: checked=%d accepted=%d (CONFIRMED=%d UNVERIFIED=%d)",
        isbn,
        len(to_verify),
        len(accepted),
        sum(1 for r in accepted if r.get("_match_quality") == "CONFIRMED"),
        sum(1 for r in accepted if r.get("_match_quality") == "UNVERIFIED_SUPER_DEAL"),
    )
    return accepted

async def _browse_fetch_one(
    client: httpx.AsyncClient,
    token: str,
    q: str,
    limit: int,
) -> List[Dict[str, Any]]:
    """Keyword q= sorgusu ile item summary çeker."""
    params = {
        "q": q,
        "limit": str(max(1, min(limit, 200))),
        "category_ids": BOOKS_CATEGORY_ID,
        "filter": "buyingOptions:{FIXED_PRICE|BEST_OFFER}",
    }
    headers = {"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": "EBAY_US"}
    r = await client.get(f"{_browse_base()}/item_summary/search", params=params, headers=headers, timeout=20)
    r.raise_for_status()
    return r.json().get("itemSummaries") or []


async def _browse_gtin_search(
    client: httpx.AsyncClient,
    token: str,
    gtin: str,
    limit: int,
) -> List[Dict[str, Any]]:
    """
    GTIN parametre ile tam eşleşme araması — keyword noise'u tamamen ortadan kaldırır.
    eBay bu aramada yalnızca GTIN'i eşleşen ürünleri döndürür (edition-exact).
    """
    params = {
        "gtin": gtin,
        "limit": str(max(1, min(limit, 200))),
        "category_ids": BOOKS_CATEGORY_ID,
        "filter": "buyingOptions:{FIXED_PRICE|BEST_OFFER}",
    }
    headers = {"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": "EBAY_US"}
    r = await client.get(
        f"{_browse_base()}/item_summary/search",
        params=params,
        headers=headers,
        timeout=20,
    )
    r.raise_for_status()
    return r.json().get("itemSummaries") or []


async def browse_search_isbn(
    client: httpx.AsyncClient,
    isbn: str,
    limit: int = 50,
    strict: bool = False,
) -> List[Dict[str, Any]]:
    """
    ISBN için eBay Browse API araması (GTIN-first, keyword fallback).

    Arama stratejisi:
    1. GTIN search: ?gtin=<isbn13> — edition-exact, keyword noise yok
       GTIN eşleşmesi kesin olduğundan strict=True'da bile verify atlanır.
    2. Keyword fallback: GTIN 0 sonuç dönünce her iki ISBN varyantı ile paralel
       q= sorgusu yapılır (ISBN-13 + ISBN-10).

    strict=False (default):
      GTIN sonuçları zaten kesin; keyword sonuçları eBay aramasına güvenilir.
    strict=True:
      Keyword fallback sonuçları için item detail (fieldgroups=PRODUCT) ile
      product.gtins + localizedAspects üzerinden ISBN doğrulaması yapılır.
      GTIN search hit ise verify atlanır (zaten kesin).
      Top 20 ucuz item'a uygulanır; ötesi DROP edilir.
    """
    token = await get_app_token(client)
    isbn_clean = isbn.replace("-", "").replace(" ", "").upper().strip()
    variants = isbn_variants(isbn_clean)
    isbn13 = isbn_clean if len(isbn_clean) == 13 else isbn10_to_isbn13(isbn_clean)

    # ── Adım 1: GTIN search (en kesin) ────────────────────────────────────────
    combined: List[Dict[str, Any]] = []
    gtin_hit = False

    if isbn13:
        try:
            gtin_items = await _browse_gtin_search(client, token, isbn13, limit)
            if gtin_items:
                gtin_hit = True
                combined = gtin_items
                logger.info("isbn=%s GTIN search: %d items", isbn_clean, len(combined))
        except Exception as e:
            logger.warning("isbn=%s GTIN search error: %s — keyword fallback", isbn_clean, e)

    # ── Adım 2: Keyword fallback (GTIN 0 sonuç döndürdüyse) ───────────────────
    if not combined:
        tasks = [_browse_fetch_one(client, token, v, limit) for v in variants]
        results_per_query = await asyncio.gather(*tasks, return_exceptions=True)

        seen_ids: set[str] = set()
        for res in results_per_query:
            if isinstance(res, Exception):
                logger.warning("isbn=%s keyword search error: %s", isbn_clean, res)
                continue
            for item in res:
                item_id = item.get("itemId") or ""
                if item_id and item_id not in seen_ids:
                    seen_ids.add(item_id)
                    combined.append(item)

        logger.info("isbn=%s keyword fallback: %d items (GTIN=0)", isbn_clean, len(combined))

    # ── Adım 3: Strict verify (yalnızca keyword sonuçlarına) ──────────────────
    if strict and combined and not gtin_hit:
        combined = await _strict_verify(client, token, combined, variants)

    return combined
