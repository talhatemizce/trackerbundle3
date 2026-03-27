"""
CSV Arbitrage Scanner
=====================
Bir CSV/metin listesindeki ISBN'leri alır:
  - Amazon SP-API'den anlık NEW buybox + USED buybox çeker
  - eBay / bookfinder_client kaynaklarından alım fiyatı çeker
  - Strict mode (default): NEW→NEW, USED→USED (cross fallback YOK)
  - Her satır için profit / ROI hesaplar
  - Filtreler uygular, accepted + rejected döndürür

Sonuç satırı şeması:
  isbn, asin, source, source_condition, buy_price,
  amazon_sell_price, buybox_type, match_type,
  referral_fee, closing_fee, fulfillment, inbound, total_fees,
  profit, roi_pct, viable, roi_tier, reason
"""
from __future__ import annotations
from enum import Enum

import asyncio
import logging
import math
import time
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.profit_calc import FeeConfig, DEFAULT_FEES, _tier
try:
    from app.analytics import (
        bsr_to_velocity, bsr_to_days_to_sell,
        compute_confidence, confidence_tier,
        compute_ev, compute_scenarios,
        seasonal_velocity_mult,
        lc_class_to_category, dewey_to_category, subjects_to_textbook_score,
    )
    _ANALYTICS_OK = True
except Exception as _analytics_err:
    import logging as _al; _al.getLogger("trackerbundle").warning("analytics import failed: %s", _analytics_err)
    _ANALYTICS_OK = False
    def bsr_to_velocity(b): return None
    def bsr_to_days_to_sell(b): return None
    def compute_confidence(d): return 0
    def confidence_tier(s): return "uncertain"
    def compute_ev(p, v, c): return None
    def compute_scenarios(*a, **k): return {}
    def seasonal_velocity_mult(*a, **k): return 1.0
    def lc_class_to_category(lc): return {}
    def dewey_to_category(d): return {}
    def subjects_to_textbook_score(s): return 0.0


def _calc_bsr_score(bsr: int, tiers: List[Tuple[int, int]]) -> int:
    """Kullanıcı tanımlı BSR kademelerine göre 0-100 puan hesapla.
    tiers = [(max_bsr, score), ...] — sıralama önemsiz, küçük BSR'dan büyüğe işlenir.
    BSR tüm max_bsr değerlerini aşarsa 0 döner.
    """
    for max_bsr, score in sorted(tiers, key=lambda x: x[0]):
        if bsr <= max_bsr:
            return max(0, min(100, score))
    return 0

logger = logging.getLogger("trackerbundle.csv_arb_scanner")

# ── ISBN dönüşüm ──────────────────────────────────────────────────────────────

def _isbn13_to_asin(isbn: str) -> Optional[str]:
    """ISBN-13 → ISBN-10 (= Amazon ASIN for books). 978 prefix only.
    Non-ISBN input (ASIN like B00xxx, random text) → None."""
    s = isbn.replace("-", "").replace(" ", "").strip().upper()
    # Sadece rakam + X (ISBN-10 check digit) kabul et
    if not all(c.isdigit() or (c == "X" and i == 9) for i, c in enumerate(s)):
        return None
    if len(s) == 10:
        # ISBN-10 checksum doğrula
        try:
            total = sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(s))
            if total % 11 != 0:
                return None
        except (ValueError, TypeError):
            return None
        return s
    if len(s) == 13 and s.startswith("978"):
        core = s[3:12]
        try:
            total = sum((10 - i) * int(c) for i, c in enumerate(core))
            check = (11 - (total % 11)) % 11
            return core + ("X" if check == 10 else str(check))
        except Exception:
            return None
    return None


# ── Profit hesabı (strict — condition bazlı) ──────────────────────────────────

@dataclass
class ArbResult:
    isbn: str
    asin: Optional[str]
    source: str                  # "ebay" | "thriftbooks" | "abebooks" | ...
    source_condition: str        # "new" | "used"
    buy_price: float             # kaynak alım fiyatı (item + ship)
    amazon_sell_price: Optional[float] = None
    buybox_type: Optional[str] = None    # "new" | "used"
    match_type: Optional[str] = None     # "NEW→NEW" | "USED→USED" | "NEW→USED(fallback)" etc.
    referral_fee: float = 0.0
    closing_fee: float = 0.0
    fulfillment: float = 0.0
    inbound: float = 0.0
    total_fees: float = 0.0
    profit: float = 0.0
    roi_pct: float = 0.0
    viable: bool = False
    roi_tier: str = "loss"
    reason: str = ""             # reject sebebi (boşsa accepted)
    accepted: bool = False
    # ── Analitik alanlar (analytics.py) ──────────────────────────────
    bsr: Optional[int] = None
    velocity: Optional[float] = None       # aylık tahmini satış adet
    days_to_sell: Optional[int] = None
    confidence: Optional[int] = None       # 0-100
    confidence_tier: Optional[str] = None  # high/medium/low/very_low
    ev_score: Optional[float] = None       # monthly EV ($)
    sell_source: str = ""                  # used_buybox/new_top1 etc.
    # Senaryo alanları
    best_case_profit: Optional[float] = None
    best_case_roi: Optional[float] = None
    base_case_profit: Optional[float] = None
    base_case_roi: Optional[float] = None
    worst_case_profit: Optional[float] = None
    worst_case_roi: Optional[float] = None
    worst_cut_pct: Optional[float] = None
    # ── eBay listing metadata (AI doğrulama için) ─────────────────────────────
    ebay_item_id: str = ""
    ebay_title: str = ""
    ebay_url: str = ""
    ebay_image_url: str = ""
    ebay_description: str = ""  # kısa açıklama / condition notes
    ebay_seller_name: str = ""
    ebay_seller_feedback: Optional[float] = None  # % pozitif
    # ── Amazon seller analizi ─────────────────────────────────────────────────
    amazon_seller_count: Optional[int] = None  # toplam satıcı sayısı
    amazon_is_sold_by_amazon: bool = False     # Amazon kendisi satıyor mu?
    # ── Ek analizler ──────────────────────────────────────────────────────────
    edition_year: Optional[int] = None         # Google Books'tan yayın yılı
    has_newer_edition: Optional[bool] = None   # daha yeni baskı var mı?
    price_volatility: str = ""                 # "LOW"|"MEDIUM"|"HIGH"
    seasonality_mult: Optional[float] = None   # bu aydaki çarpan
    # ── Buyback kanalı (BookScouter/BooksRun) ─────────────────────────────────
    buyback_cash: Optional[float] = None        # en iyi buyback teklifi ($)
    buyback_trend: Optional[str] = None         # "rising"|"falling"|"stable"|"unknown"
    buyback_trend_note: Optional[str] = None    # açıklama
    # eBay match metadata — stats counting için gerekli
    match_quality: str = ""          # CONFIRMED | UNVERIFIED_SUPER_DEAL | UNVERIFIED_KEYWORD | UNVERIFIED_INPUT
    match_reason: str = ""           # gtins_match | keyword_only | gtin_search | etc.
    query_mode: str = ""             # gtin | keyword_fallback | csv_input
    # NYT Bestseller sinyali
    nyt_bestseller: bool = False                 # NYT listesinde yer aldı mı?
    nyt_weeks: int = 0                           # toplam liste haftası
    nyt_rank: Optional[int] = None               # en yüksek rank (1=birinci)
    nyt_note: str = ""                           # özet açıklama
    # Kitap sınıflandırma (HathiTrust/LoC/Analytics'ten)
    is_textbook_likely: bool = False             # DDC/LC/subjects → textbook sınıfı
    textbook_score: float = 0.0                  # 0.0–1.0
    has_newer_edition: Optional[bool] = None     # Open Library Work API'den
    dewey: Optional[str] = None                  # Dewey Decimal
    lc_class: Optional[str] = None              # LC Call Number
    buyback_vendor: str = ""                    # en iyi vendor adı
    buyback_url: str = ""                       # vendor URL
    buyback_profit: Optional[float] = None      # buyback_cash - buy_price - $3.99 nakliye
    buyback_roi: Optional[float] = None         # % ROI buyback kanalında
    # BSR puanı (kullanıcı tanımlı kademeler)
    bsr_score: Optional[int] = None             # 0-100, None = BSR yok

    def to_dict(self) -> dict:
        return asdict(self)


def _calc_profit_strict(
    buy_price: float,
    source_condition: str,   # "new" | "used"
    amazon_data: Dict[str, Any],
    strict_mode: bool,
    fees: FeeConfig = DEFAULT_FEES,
) -> Tuple[Optional[float], Optional[str], Optional[str], str]:
    """
    Returns: (sell_price, buybox_type, match_type, reason)
    strict_mode=True: NEW item → only new buybox, USED item → only used buybox
    strict_mode=False: fallback allowed
    """
    new_bb  = (amazon_data.get("new")  or {}).get("buybox")
    used_bb = (amazon_data.get("used") or {}).get("buybox")

    new_price  = float(new_bb["total"])  if new_bb  and new_bb.get("total")  else None
    used_price = float(used_bb["total"]) if used_bb and used_bb.get("total") else None

    cond = source_condition.lower()

    if cond == "new":
        if new_price is not None:
            return new_price, "new", "NEW→NEW", ""
        if not strict_mode and used_price is not None:
            return used_price, "used", "NEW→USED(fallback)", ""
        return None, None, None, "missing_new_buybox"

    elif cond == "used":
        if used_price is not None:
            return used_price, "used", "USED→USED", ""
        if not strict_mode and new_price is not None:
            return new_price, "new", "USED→NEW(fallback)", ""
        return None, None, None, "missing_used_buybox"

    return None, None, None, f"unknown_condition:{cond}"


def _apply_profit(
    result: ArbResult,
    sell_price: float,
    buybox_type: str,
    match_type: str,
    fees: FeeConfig,
) -> None:
    """Profit alanlarını doldur."""
    referral = max(1.00, sell_price * fees.referral_pct)
    total_fees = referral + fees.closing_fee + fees.fulfillment + fees.inbound
    profit = sell_price - total_fees - result.buy_price
    roi_pct = (profit / result.buy_price * 100) if result.buy_price > 0 else 0.0

    result.amazon_sell_price = round(sell_price, 2)
    result.buybox_type = buybox_type
    result.match_type = match_type
    result.referral_fee = round(referral, 2)
    result.closing_fee = fees.closing_fee
    result.fulfillment = fees.fulfillment
    result.inbound = fees.inbound
    result.total_fees = round(total_fees, 2)
    result.profit = round(profit, 2)
    result.roi_pct = round(roi_pct, 1)
    result.viable = profit > 0
    result.roi_tier = _tier(roi_pct)


# ── Filtre ────────────────────────────────────────────────────────────────────

class IsbnMatchPolicy(str, Enum):
    PRECISION = "precision"   # sadece CONFIRMED (GTIN eşleşmesi)
    BALANCED  = "balanced"    # CONFIRMED + UNVERIFIED_SUPER_DEAL (default)
    RECALL    = "recall"      # her şeyi dahil et


class InvalidIsbnPolicy(str, Enum):
    REJECT      = "reject"      # geçersiz ISBN'i reddet
    BEST_EFFORT = "best_effort" # keyword ara ama UNVERIFIED_INPUT işaretle


@dataclass
class ScanFilters:
    min_roi_pct: Optional[float] = None
    max_roi_pct: Optional[float] = None
    min_profit_usd: Optional[float] = None
    min_amazon_price: Optional[float] = None
    max_amazon_price: Optional[float] = None
    min_buy_price: Optional[float] = None
    max_buy_price: Optional[float] = None
    max_buy_ratio_pct: Optional[float] = None  # alım fiyatı amazon fiyatının max %X'i (örn. 50 = max %50)
    condition_in: Optional[List[str]] = None   # ["new","used"] veya sadece biri
    source_in: Optional[List[str]] = None      # ["ebay","thriftbooks","abebooks",...]
    only_viable: bool = True
    strict_mode: bool = True
    isbn_match_policy: IsbnMatchPolicy = IsbnMatchPolicy.BALANCED
    invalid_isbn_policy: InvalidIsbnPolicy = InvalidIsbnPolicy.BEST_EFFORT
    # Buyback kanalı filtresi
    min_buyback_profit: Optional[float] = None  # buyback_profit >= X ($) olanları göster
    buyback_only: bool = False                  # sadece buyback kanalında kârlı olanlar
    # Amazon used buybox condition filtresi
    amazon_condition_in: Optional[List[str]] = None  # ["acceptable","good","very_good","like_new"]
    # BookDepot-only mod: eBay/BookFinder/buyback/metadata atla (sadece BD vs Amazon)
    bookdepot_only: bool = False
    # BSR puan kademeleri: [(max_bsr, score), ...] azalan BSR sırasında
    # Örn: [(100000, 100), (500000, 70), (1000000, 40)] → üzeri 0
    bsr_score_tiers: Optional[List[Tuple[int, int]]] = None
    min_bsr_score: Optional[int] = None  # bu puanın altındakileri ele


def _filter_result(r: ArbResult, f: ScanFilters) -> str:
    """'' döner = geçti. Dolu string = reject sebebi."""
    if f.min_buy_price  is not None and r.buy_price < f.min_buy_price:
        return f"buy_price_below_min(${f.min_buy_price})"
    if f.max_buy_price  is not None and r.buy_price > f.max_buy_price:
        return f"buy_price_above_max(${f.max_buy_price})"
    if r.amazon_sell_price is None:
        return r.reason or "no_amazon_price"
    # Ratio filtresi: alım fiyatı amazon buybox fiyatının max %X'i olmalı
    if f.max_buy_ratio_pct is not None and r.amazon_sell_price > 0:
        max_allowed = r.amazon_sell_price * f.max_buy_ratio_pct / 100
        if r.buy_price > max_allowed:
            return f"buy_ratio_too_high({round(r.buy_price/r.amazon_sell_price*100)}%>max{f.max_buy_ratio_pct}%)"
    if f.min_amazon_price is not None and r.amazon_sell_price < f.min_amazon_price:
        return f"amazon_price_below_min(${f.min_amazon_price})"
    if f.max_amazon_price is not None and r.amazon_sell_price > f.max_amazon_price:
        return f"amazon_price_above_max(${f.max_amazon_price})"
    if f.only_viable and not r.viable:
        return "not_viable"
    if f.min_profit_usd is not None and r.profit < f.min_profit_usd:
        return f"profit_below_min(${f.min_profit_usd})"
    if f.min_roi_pct is not None and r.roi_pct < f.min_roi_pct:
        return f"roi_below_min({f.min_roi_pct}%)"
    if f.max_roi_pct is not None and r.roi_pct > f.max_roi_pct:
        return f"roi_above_max({f.max_roi_pct}%)"
    if f.condition_in and r.source_condition not in f.condition_in:
        return f"condition_not_in({f.condition_in})"
    if f.source_in and r.source not in f.source_in:
        return f"source_not_in({f.source_in})"
    # Buyback filtresi — sadece buyback kanalı kârlıysa göster
    if f.buyback_only and (r.buyback_profit is None or r.buyback_profit <= 0):
        return "buyback_not_profitable"
    if f.min_buyback_profit is not None and (r.buyback_profit is None or r.buyback_profit < f.min_buyback_profit):
        return f"buyback_profit_below_min(${f.min_buyback_profit})"
    if f.min_bsr_score is not None and (r.bsr_score is None or r.bsr_score < f.min_bsr_score):
        return f"bsr_score_below_min({r.bsr_score}/{f.min_bsr_score})"
    return ""


# ── Amazon fiyat çekimi (cache'li) ───────────────────────────────────────────

import json as _json_mod
from pathlib import Path as _Path

_amz_cache: Dict[str, Tuple[float, Dict]] = {}  # asin → (ts, data)
_AMZ_TTL = 7 * 24 * 3600  # 1 hafta
_AMZ_DISK_CACHE = _Path(__file__).resolve().parent / "data" / "amazon_price_cache.json"
_amz_disk_loaded = False

def _load_amz_disk_cache() -> None:
    """Uygulama başlarken disk cache'i belleğe yükle."""
    global _amz_disk_loaded
    if _amz_disk_loaded:
        return
    _amz_disk_loaded = True
    if not _AMZ_DISK_CACHE.exists():
        return
    try:
        raw = _json_mod.loads(_AMZ_DISK_CACHE.read_text())
        now = time.time()
        for asin, (ts, data) in raw.items():
            if now - ts < _AMZ_TTL:
                _amz_cache[asin] = (ts, data)
    except Exception as e:
        logger.warning("amazon_price_cache load failed: %s", e)

def _save_amz_disk_cache() -> None:
    """Bellek cache'ini diske yaz (atomik)."""
    try:
        _AMZ_DISK_CACHE.parent.mkdir(exist_ok=True)
        tmp = _AMZ_DISK_CACHE.with_suffix(".tmp")
        tmp.write_text(_json_mod.dumps(_amz_cache))
        tmp.replace(_AMZ_DISK_CACHE)
    except Exception as e:
        logger.warning("amazon_price_cache save failed: %s", e)


_catalog_cache_sc: Dict[str, tuple] = {}
_CATALOG_TTL_SC = 3600 * 2

async def _get_amazon_prices(asin: str) -> Dict[str, Any]:
    """get_top2_prices + getCatalogItem (BSR) paralel çek, 1 hafta cache'le (disk kalıcı)."""
    _load_amz_disk_cache()
    now = time.time()
    if asin in _amz_cache:
        ts, data = _amz_cache[asin]
        if now - ts < _AMZ_TTL:
            return data

    from app import amazon_client as _amz
    try:
        # Paralel: fiyat + BSR/catalog metadata
        prices, catalog = await asyncio.gather(
            _amz.get_top2_prices(asin),
            _amz.get_catalog_item(asin),
            return_exceptions=True,
        )
        if isinstance(prices,  Exception): prices  = {}
        if isinstance(catalog, Exception): catalog = {}

        # BSR'ı fiyat verisine ekle — scanner ve UI bunu kullanır
        if isinstance(prices, dict) and isinstance(catalog, dict):
            bsr = catalog.get("bsr") or catalog.get("bsr_all")
            if bsr:
                # BSR'ı hem used hem new bloğuna ekle (hangisi varsa)
                for cond in ("used", "new"):
                    if prices.get(cond):
                        prices[cond]["bsr"] = bsr
                prices["bsr"] = bsr
                prices["list_price"] = catalog.get("list_price")

        data = prices if isinstance(prices, dict) else {}
        _amz_cache[asin] = (now, data)
        _save_amz_disk_cache()
        return data
    except Exception as e:
        logger.warning("Amazon prices failed asin=%s: %s", asin, e)
        await asyncio.sleep(0.3)  # kısa backoff, 2s değil
        return {}


# ── eBay fiyat çekimi (mevcut Finding API'yi kullan) ─────────────────────────

async def _get_ebay_offers(isbn: str, filters: "ScanFilters | None" = None) -> List[Dict]:
    """eBay'den ISBN için aktif listingleri çek (Browse API). ISBN doğrulaması + policy uygulaması."""
    # isbn_info try dışında tanımla — scoping bug'ını önler
    from app.isbn_utils import parse_isbn as _pi, IsbnValidationReason as _ivr
    isbn_info = _pi(isbn)
    match_policy = (filters.isbn_match_policy if filters else "balanced")
    invalid_policy = (filters.invalid_isbn_policy if filters else "best_effort")
    try:
        from app.ebay_client import browse_search_isbn, item_total_price, normalize_condition
        from app.core.config import get_settings
        s = get_settings()
        # CALCULATED_SHIP_ESTIMATE_USD yoksa $3.99 default kullan — yoksa tüm ilanlar skip olur
        calc_est = s.calculated_ship_estimate_usd if s.calculated_ship_estimate_usd > 0 else 3.99

        async with httpx.AsyncClient(timeout=20) as client:
            from app.ebay_client import hybrid_verify_items

            if not isbn_info.valid:
                logger.info(
                    "ISBN geçersiz isbn=%s reason=%s policy=%s",
                    isbn, isbn_info.reason.value, invalid_policy,
                )
                if str(invalid_policy) == "reject":
                    return [{"_error": f"invalid_isbn:{isbn_info.reason.value}",
                             "isbn_valid": False,
                             "isbn_validation_reason": isbn_info.reason.value,
                             "match_quality": "UNVERIFIED_INPUT"}]
                # best_effort: devam et ama sonuçlara UNVERIFIED_INPUT işaretle

            items = await browse_search_isbn(
                client, isbn,
                isbn_match_policy=str(match_policy),
            )
        # eBay rate limit: scan_isbn_list._isbn_delay ile yönetiliyor

        if not items:
            logger.debug("eBay browse_search_isbn isbn=%s returned 0 items", isbn)

        offers = []
        price_skipped = 0   # shipping unknown → fiyat çıkarılamadı
        policy_skipped = 0  # policy filter → dropped
        for it in (items or []):
            total = item_total_price(it, calc_ship_est=calc_est)
            if total is None or total <= 0:
                logger.debug("eBay item_total_price=None isbn=%s item=%s", isbn, it.get("itemId","?"))
                price_skipped += 1
                continue
            # Browse API item_summary: condition = plain string, conditionId = plain string
            # e.g. {"condition": "Very Good", "conditionId": "4000"}
            cond_text = it.get("condition") or ""
            cond_id   = it.get("conditionId")
            if not isinstance(cond_text, str):
                cond_text = str(cond_text)
            cond = normalize_condition(cond_text, cond_id)
            # brand_new + like_new → Amazon NEW olarak listelenebilir
            # very_good / good / acceptable → Amazon USED
            source_cond = "new" if cond in ("brand_new", "like_new") else "used"
            # Image URL — Browse API returns thumbnailImages list or image object
            img = ""
            thumb = it.get("thumbnailImages") or it.get("image") or {}
            if isinstance(thumb, list) and thumb:
                img = thumb[0].get("imageUrl", "")
            elif isinstance(thumb, dict):
                img = thumb.get("imageUrl", "")

            # Seller info
            seller = it.get("seller") or {}
            seller_name = seller.get("username", "") if isinstance(seller, dict) else ""
            feedback_pct = None
            fp = seller.get("feedbackPercentage") if isinstance(seller, dict) else None
            if fp is not None:
                try: feedback_pct = float(fp)
                except Exception: pass

            # match_quality from hybrid verify (may be set on item)
            mq = it.get("_match_quality", "UNVERIFIED_KEYWORD")
            mr = it.get("_verification_reason", "")
            qm = it.get("_query_mode", "keyword_fallback")
            # If ISBN was invalid, override to UNVERIFIED_INPUT
            if not isbn_info.valid:
                mq = "UNVERIFIED_INPUT"
                mr = isbn_info.reason.value

            # Policy filter: drop items that don't meet match policy
            # Use .value for str(Enum) — str(IsbnMatchPolicy.PRECISION) gives
            # "IsbnMatchPolicy.PRECISION", not "precision"
            policy_val = match_policy.value if hasattr(match_policy, "value") else str(match_policy)
            if policy_val == "precision" and mq != "CONFIRMED":
                logger.debug("isbn=%s item=%s DROPPED (precision policy, mq=%s)", isbn, it.get("itemId","?"), mq)
                policy_skipped += 1
                continue
            elif policy_val == "balanced" and mq not in ("CONFIRMED", "UNVERIFIED_SUPER_DEAL"):
                logger.debug("isbn=%s item=%s DROPPED (balanced policy, mq=%s)", isbn, it.get("itemId","?"), mq)
                policy_skipped += 1
                continue
            # recall: keep everything

            offers.append({
                "source": "ebay",
                "source_condition": source_cond,
                "buy_price": round(total, 2),
                "item_id": it.get("itemId", ""),
                "title": (it.get("title") or "")[:120],
                "url": it.get("itemWebUrl", ""),
                "image_url": img,
                "description": (it.get("shortDescription") or it.get("condition") or "")[:200],
                "seller_name": seller_name,
                "seller_feedback": feedback_pct,
                "match_quality": mq,
                "match_reason": mr,
                "query_mode": qm,
                "isbn_normalized": isbn_info.normalized or isbn,
                "isbn_valid": isbn_info.valid,
                "isbn_validation_reason": isbn_info.reason.value,
            })
        # En ucuz new + en ucuz used döndür
        best: Dict[str, Dict] = {}
        for o in sorted(offers, key=lambda x: x["buy_price"]):
            c = o["source_condition"]
            if c not in best:
                best[c] = o
        return list(best.values())
    except Exception as e:
        err_msg = str(e)
        if "401" in err_msg or "Unauthorized" in err_msg:
            logger.warning("eBay token hatası isbn=%s: %s", isbn, e)
        elif "EBAY_CLIENT_ID" in err_msg:
            logger.warning("eBay credentials eksik isbn=%s", isbn)
        else:
            logger.warning("eBay offers failed isbn=%s: %s", isbn, e)
        return [{"_error": err_msg}]  # hata bilgisini taşı



async def _get_buyback_prices(isbn: str) -> Dict:
    """BookScouter/BooksRun'dan buyback fiyatları çek."""
    try:
        from app.buyback_client import fetch_buyback_prices
        return await fetch_buyback_prices(isbn)
    except Exception as e:
        logger.warning("Buyback fetch failed isbn=%s: %s", isbn, e)
        return {}


async def _get_bookfinder_offers(isbn: str) -> List[Dict]:
    """BookFinder kaynaklarından (AbeBooks, ThriftBooks...) fiyat çek."""
    try:
        from app.bookfinder_client import fetch_bookfinder
        result = await fetch_bookfinder(isbn, condition="all")
        if not result.get("ok"):
            return []

        offers = []
        for cond_key, cond_label in [("new", "new"), ("used", "used")]:
            block = result.get(cond_key)
            if not block:
                continue
            for o in (block.get("offers") or []):
                buy = o.get("total", 0)
                if buy <= 0:
                    continue
                source_raw = o.get("seller", "bookfinder").lower()
                source = "abebooks" if "abe" in source_raw else \
                         "thriftbooks" if "thrift" in source_raw else \
                         "betterworldbooks" if "better" in source_raw else \
                         source_raw
                offers.append({
                    "source": source,
                    "source_condition": cond_label,
                    "buy_price": round(float(buy), 2),
                    "item_id": o.get("sid", ""),
                    "title": "",
                    "url": o.get("url", ""),
                })
        # Her source × condition için en ucuz
        best: Dict[Tuple[str, str], Dict] = {}
        for o in sorted(offers, key=lambda x: x["buy_price"]):
            key = (o["source"], o["source_condition"])
            if key not in best:
                best[key] = o
        return list(best.values())
    except Exception as e:
        logger.warning("BookFinder offers failed isbn=%s: %s", isbn, e)
        return []


async def _get_bookdepot_offers(isbn: str) -> List[Dict]:
    """BookDepot envanterinden alım fiyatı çek (lokal JSON store)."""
    try:
        from app.core.json_store import _read_unsafe
        from app.core.config import get_settings
        p = get_settings().resolved_data_dir() / "bookdepot_inventory.json"
        data = _read_unsafe(p, default={"items": {}})
        store = data.get("items", {})
        item = store.get(isbn)
        if not item or not item.get("price") or item["price"] <= 0:
            return []
        return [{
            "source": "bookdepot",
            "source_condition": "used",
            "buy_price": round(float(item["price"]) + 0.60, 2),  # +$0.60 kitap başı kargo
            "item_id": f"bd_{isbn}",
            "title": item.get("title", "")[:120],
            "url": item.get("url", ""),
        }]
    except Exception as e:
        logger.warning("BookDepot offers failed isbn=%s: %s", isbn, e)
        return []


# ── Ana tarayıcı ─────────────────────────────────────────────────────────────

async def _scan_one(
    isbn: str,
    filters: ScanFilters,
    fees: FeeConfig,
    isbn_buy_prices: Dict[str, float] = {},
    isbn_amazon_prices: Dict[str, float] = {},
) -> List[ArbResult]:
    """Tek ISBN için tüm kaynakları tara, ArbResult listesi döndür."""
    isbn_buy_prices = isbn_buy_prices or {}
    isbn_amazon_prices = isbn_amazon_prices or {}
    asin = _isbn13_to_asin(isbn)
    if not asin:
        r = ArbResult(isbn=isbn, asin=None, source="", source_condition="",
                      buy_price=0, amazon_sell_price=None, buybox_type=None, match_type=None)
        r.reason = "invalid_isbn_or_not_978"
        return [r]

    # Paralel: Amazon + eBay + BookFinder + Buyback + Buyback trend
    async def _get_buyback_trend_safe(isbn):
        try:
            from app.buyback_client import get_buyback_price_trend
            return await asyncio.wait_for(get_buyback_price_trend(isbn), timeout=8.0)
        except Exception:
            return {}

    async def _get_book_meta_safe(isbn):
        """Book metadata + NYT bestseller check — OL Search, HathiTrust, LoC, NYT."""
        try:
            async with httpx.AsyncClient(timeout=5) as _mc:
                from app.ai_analyst import _check_edition
                from app.nyt_client import get_isbn_nyt_history
                meta, nyt = await asyncio.gather(
                    asyncio.wait_for(_check_edition(isbn, _mc), timeout=5.0),
                    asyncio.wait_for(get_isbn_nyt_history(isbn), timeout=5.0),
                    return_exceptions=True,
                )
                if isinstance(meta, Exception): meta = {}
                if isinstance(nyt, Exception):  nyt  = {}
                if isinstance(meta, dict):
                    meta["_nyt"] = nyt if isinstance(nyt, dict) else {}
                return meta
        except Exception:
            return {}

    if filters.bookdepot_only:
        # BD taraması: sadece Amazon fiyatı + BD envanteri çek, gerisi boş
        amazon_data, bd_offers = await asyncio.gather(
            _get_amazon_prices(asin),
            _get_bookdepot_offers(isbn),
            return_exceptions=True,
        )
        ebay_offers = bf_offers = []
        buyback_data = buyback_trend_data = book_meta = {}
    else:
        amazon_data, ebay_offers, bf_offers, bd_offers, buyback_data, buyback_trend_data, book_meta = await asyncio.gather(
            _get_amazon_prices(asin),
            _get_ebay_offers(isbn, filters=filters),
            _get_bookfinder_offers(isbn),
            _get_bookdepot_offers(isbn),
            _get_buyback_prices(isbn),
            _get_buyback_trend_safe(isbn),
            _get_book_meta_safe(isbn),
            return_exceptions=True,
        )

    if isinstance(amazon_data, Exception):
        amazon_data = {}
    if isinstance(ebay_offers, Exception):
        ebay_offers = []
    if isinstance(bf_offers, Exception):
        bf_offers = []
    if isinstance(bd_offers, Exception):
        bd_offers = []
    if isinstance(buyback_data, Exception):
        buyback_data = {}
    if isinstance(buyback_trend_data, Exception):
        buyback_trend_data = {}
    if isinstance(book_meta, Exception):
        book_meta = {}

    # Hata itemlarını filtrele ama reason kaydet
    ebay_error = next((o["_error"] for o in (ebay_offers or []) if "_error" in o), None)
    bf_error   = next((o["_error"] for o in (bf_offers   or []) if "_error" in o), None)
    all_offers = [o for o in (ebay_offers or []) + (bf_offers or []) + (bd_offers or []) if "_error" not in o]

    # Kullanıcı alım fiyatı (generic CSV) → sentetik offer ekle
    # BookDepot envanterinden gelen offer varsa csv_input ekleme (duplicate önle)
    csv_price = isbn_buy_prices.get(isbn) or isbn_buy_prices.get(asin)
    has_bd_offer = any(o.get("source") == "bookdepot" for o in all_offers)
    if csv_price and csv_price > 0 and not has_bd_offer:
        for cond in ["new", "used"]:
            all_offers.append({
                "source": "csv_input",
                "source_condition": cond,
                "buy_price": round(float(csv_price), 2),
                "item_id": "csv",
                "title": "CSV alım fiyatı",
                "url": "",
            })

    # Amazon Business Report ortalama satış fiyatı → amazon_data'yı override et
    amz_report_price = isbn_amazon_prices.get(isbn) or isbn_amazon_prices.get(asin)
    if amz_report_price and amz_report_price > 0:
        # Business Report fiyatını hem new hem used buybox olarak enjekte et
        # (rapordaki fiyat hangi kondisyonda satıldığını bilmiyoruz, iki seçenek de göster)
        bb_entry = {"total": amz_report_price, "price": amz_report_price, "ship": 0.0,
                    "label": "A", "buybox": True, "source": "business_report"}
        if not amazon_data:
            amazon_data = {}
        if not amazon_data.get("new", {}).get("buybox"):
            amazon_data.setdefault("new", {})["buybox"] = bb_entry
        if not amazon_data.get("used", {}).get("buybox"):
            amazon_data.setdefault("used", {})["buybox"] = bb_entry
        logger.debug("Injected business_report price=%.2f for isbn=%s", amz_report_price, isbn)

    if not all_offers:
        reason = "no_ebay_listings"
        if ebay_error and ("401" in ebay_error or "Unauthorized" in ebay_error):
            reason = "ebay_token_error"
        elif ebay_error and "EBAY_CLIENT_ID" in ebay_error:
            reason = "ebay_not_configured"
        elif ebay_error:
            short = ebay_error.replace("\n", " ")[:80]
            reason = f"ebay_error:{short}"
        elif (ebay_offers is not None and len([o for o in (ebay_offers or []) if "_error" not in o]) == 0
              and filters and hasattr(filters, "isbn_match_policy")):
            policy_val = filters.isbn_match_policy.value if hasattr(filters.isbn_match_policy, "value") else str(filters.isbn_match_policy)
            # raw_items: eBay'den dönen ham ilan sayısı (hata olmayanlar)
            raw_items = [o for o in (ebay_offers or []) if "_error" not in o]
            if policy_val == "recall":
                # Recall'da policy drop olamaz → shipping sorunu
                reason = "shipping_unknown:eBay'de ilan var ama kargo fiyatı alınamadı (CALCULATED shipping — env: CALCULATED_SHIP_ESTIMATE_USD)"
            elif raw_items:
                # İlanlar vardı ama policy drop etti
                reason = f"policy_filtered({policy_val}):{len(raw_items)} ilan eşleşme politikasına uymadı — Recall modunu dene"
            else:
                reason = f"no_valid_offers:eBay ilan yok veya tümü elendi ({policy_val})"
        r = ArbResult(isbn=isbn, asin=asin, source="", source_condition="",
                      buy_price=0, amazon_sell_price=None, buybox_type=None, match_type=None)
        r.reason = reason
        return [r]

    if not amazon_data:
        # Google Shopping fallback: SP-API boş döndü, SERP üzerinden Amazon fiyatı dene
        try:
            from app.amazon_price_fallback import get_amazon_price_via_shopping
            from app.core.config import get_settings as _gs
            _s = _gs()
            if _s.serper_api_key or _s.serpapi_key:
                # Title bilgisini all_offers'dan çekmeye çalış
                _fallback_title = next(
                    (o.get("title","") for o in (all_offers or []) if o.get("title")), ""
                )
                # Condition hint: ne arıyoruz?
                _cond_hint = "used" if any(
                    o.get("source_condition","used") == "used" for o in (all_offers or [])
                ) else "new"
                _fb = await get_amazon_price_via_shopping(isbn, _fallback_title, _cond_hint)
                if _fb:
                    amazon_data = _fb
                    logger.info("isbn=%s Amazon fallback via Google Shopping succeeded", isbn)
        except Exception as _fb_err:
            logger.debug("Amazon fallback error isbn=%s: %s", isbn, _fb_err)

    if not amazon_data:
        # buyback_only mode: Amazon yoksa buyback kâr hesabı yapabiliriz — erken çıkma
        if filters.buyback_only or filters.min_buyback_profit is not None:
            logger.info("isbn=%s amazon_unavailable but buyback_only=True — continuing for buyback eval", isbn)
            amazon_data = {}  # boş dict ile devam — buyback loop'u aşağıda çalışacak
        else:
            results = []
            for o in all_offers:
                r = ArbResult(isbn=isbn, asin=asin, **{k: o[k] for k in
                              ["source", "source_condition", "buy_price"]},
                              amazon_sell_price=None, buybox_type=None, match_type=None)
                r.reason = "amazon_unavailable"
                results.append(r)
            return results

    # Amazon used buybox condition filtresi
    if filters.amazon_condition_in and amazon_data:
        used_bb = (amazon_data.get("used") or {}).get("buybox") or {}
        used_bb_cond = used_bb.get("sub_condition", "")
        if used_bb_cond and used_bb_cond not in filters.amazon_condition_in:
            _rej = []
            for o in all_offers:
                r = ArbResult(isbn=isbn, asin=asin, source=o["source"],
                              source_condition=o["source_condition"], buy_price=o["buy_price"],
                              amazon_sell_price=None, buybox_type=None, match_type=None)
                r.reason = f"amazon_condition_mismatch({used_bb_cond})"
                _rej.append(r)
            return _rej

    results = []
    for o in all_offers:
        r = ArbResult(
            isbn=isbn, asin=asin,
            source=o["source"],
            source_condition=o["source_condition"],
            buy_price=o["buy_price"],
            amazon_sell_price=None, buybox_type=None, match_type=None,
            ebay_item_id=o.get("item_id",""),
            ebay_title=o.get("title",""),
            ebay_url=o.get("url",""),
            ebay_image_url=o.get("image_url",""),
            ebay_description=o.get("description",""),
            ebay_seller_name=o.get("seller_name",""),
            ebay_seller_feedback=o.get("seller_feedback"),
            match_quality=o.get("match_quality",""),
            match_reason=o.get("match_reason",""),
            query_mode=o.get("query_mode",""),
        )

        sell_price, bb_type, match_type, reason = _calc_profit_strict(
            o["buy_price"], o["source_condition"], amazon_data,
            strict_mode=filters.strict_mode, fees=fees,
        )

        if sell_price is None:
            r.reason = reason
        else:
            _apply_profit(r, sell_price, bb_type, match_type, fees)

            # ── Analitik Layer ────────────────────────────────────────
            # BSR → velocity → days_to_sell (amazon_data'dan BSR gelebilir)
            _bsr = (amazon_data.get(bb_type or "used") or {}).get("bsr") or                    (amazon_data.get("used") or {}).get("bsr") or                    (amazon_data.get("new") or {}).get("bsr")
            if _bsr:
                r.bsr = int(_bsr)
                r.velocity = bsr_to_velocity(r.bsr)
                r.days_to_sell = bsr_to_days_to_sell(r.bsr)
                if filters.bsr_score_tiers:
                    r.bsr_score = _calc_bsr_score(r.bsr, filters.bsr_score_tiers)

            # Book metadata (textbook classification, newer edition)
            if book_meta and isinstance(book_meta, dict):
                r.is_textbook_likely = bool(book_meta.get("is_textbook_likely", False))
                r.textbook_score     = float(book_meta.get("textbook_score", 0.0))
                r.has_newer_edition  = book_meta.get("has_newer_edition")
                r.dewey              = book_meta.get("dewey")
                r.lc_class           = book_meta.get("lc_class")

            # NYT bestseller sinyali — book_meta_safe içinde nyt_data da geliyor
            if book_meta and isinstance(book_meta, dict):
                nyt = book_meta.get("_nyt") or {}
                if nyt:
                    r.nyt_bestseller = bool(nyt.get("was_bestseller", False))
                    r.nyt_weeks      = int(nyt.get("total_weeks") or 0)
                    r.nyt_rank       = nyt.get("highest_rank")
                    r.nyt_note       = nyt.get("note", "")

            # sell_source: "used_buybox" / "new_top1" formatı (P1 fix)
            _sec = (amazon_data.get(bb_type) or {}) if bb_type else {}
            _has_bb = bool((_sec.get("buybox") or {}).get("total"))
            r.sell_source = f"{bb_type}_buybox" if (_has_bb and bb_type) else                             (f"{bb_type}_top1" if bb_type else "")

            # Confidence score
            _r_dict = r.to_dict()
            r.confidence = compute_confidence(_r_dict)
            r.confidence_tier = confidence_tier(r.confidence)

            # EV score
            r.ev_score = compute_ev(r.profit, r.velocity, r.confidence)

            # Scenario simulator (v2 — dinamik worst-case)
            _amz_report_price = isbn_amazon_prices.get(isbn) or isbn_amazon_prices.get(asin)
            _scen = compute_scenarios(
                buy_price=r.buy_price,
                current_sell=sell_price,
                avg_sell=_amz_report_price if _amz_report_price and _amz_report_price > 0 else None,
                total_fees=r.total_fees or 0,
                velocity=r.velocity,
                bsr=r.bsr,
            )
            if _scen:
                r.best_case_profit  = _scen.get("best_case_profit")
                r.best_case_roi     = _scen.get("best_case_roi")
                r.base_case_profit  = _scen.get("base_case_profit")
                r.base_case_roi     = _scen.get("base_case_roi")
                r.worst_case_profit = _scen.get("worst_case_profit")
                r.worst_case_roi    = _scen.get("worst_case_roi")
                r.worst_cut_pct     = _scen.get("worst_cut_pct")

        # ── Buyback kanalı — filter'dan ÖNCE ekle (buyback_only filtresi için gerekli) ──
        if isinstance(buyback_data, dict) and buyback_data.get("ok"):
            best_cash = buyback_data.get("best_cash")
            if best_cash and best_cash > 0:
                from app.buyback_client import calc_buyback_profit
                bb_calc = calc_buyback_profit(o["buy_price"], best_cash)
                r.buyback_cash   = best_cash
                r.buyback_vendor = buyback_data.get("best_vendor", "")
                r.buyback_url    = buyback_data.get("best_url", "")
                r.buyback_profit = bb_calc["profit"]
                r.buyback_roi    = bb_calc["roi_pct"]
                # Fiyat trendi — falling ise dikkat
                if buyback_trend_data and isinstance(buyback_trend_data, dict):
                    r.buyback_trend      = buyback_trend_data.get("trend")
                    r.buyback_trend_note = buyback_trend_data.get("note")

        # buyback_only mode: Amazon olmadan da kabul et (buyback kârlıysa)
        if filters.buyback_only and r.buyback_profit and r.buyback_profit > 0:
            r.reason = ""
            r.accepted = True
            results.append(r)
            continue

        reject_reason = _filter_result(r, filters)
        if reject_reason:
            r.reason = reject_reason
            r.accepted = False
        else:
            r.accepted = True

        results.append(r)

    return results


async def scan_isbn_list(
    isbns: List[str],
    filters: ScanFilters,
    fees: FeeConfig = DEFAULT_FEES,
    concurrency: int = 5,
    on_progress: Any = None,  # optional callback(done, total)
    isbn_buy_prices: Dict[str, float] = {},     # opsiyonel: kullanıcı alım fiyatları (generic CSV)
    isbn_amazon_prices: Dict[str, float] = {},  # opsiyonel: Amazon Business Report ortalama satış fiyatı
    pause_event: Any = None,   # asyncio.Event — set iken scanner bekler (pause)
    cancel_event: Any = None,  # asyncio.Event — set iken scanner durur (cancel)
) -> Dict[str, Any]:
    """
    ISBN listesini paralel tara (max `concurrency` aynı anda).
    Returns {accepted, rejected, stats, duration_s}
    """
    isbn_buy_prices = isbn_buy_prices or {}
    isbn_amazon_prices = isbn_amazon_prices or {}
    t0 = time.time()
    sem = asyncio.Semaphore(concurrency)
    done_count = 0
    total = len(isbns)
    accepted: List[Dict] = []
    rejected: List[Dict] = []

    # eBay Browse API rate limit koruması: concurrency=1 ile bile
    # peş peşe istekler 429 alabilir. ISBN başına minimum bekleme.
    _isbn_delay = max(0.15, 0.5 / max(concurrency, 1))  # eBay Browse API toleranslı
    _last_request_lock = asyncio.Lock()
    _last_request_time: list = [0.0]

    async def _run(isbn: str):
        nonlocal done_count
        async with sem:
            # eBay rate limit: ISBN'ler arası minimum bekleme
            async with _last_request_lock:
                elapsed = time.time() - _last_request_time[0]
                if elapsed < _isbn_delay:
                    await asyncio.sleep(_isbn_delay - elapsed)
                _last_request_time[0] = time.time()

            # Cancel check — hemen çık
            if cancel_event and cancel_event.is_set():
                return

            # Pause check — resume gelene kadar bekle
            if pause_event and pause_event.is_set():
                logger.info("scan_isbn_list: paused, waiting for resume...")
                while pause_event.is_set():
                    if cancel_event and cancel_event.is_set():
                        return
                    await asyncio.sleep(0.5)
                logger.info("scan_isbn_list: resumed")

            results = await _scan_one(isbn.strip(), filters, fees, isbn_buy_prices=isbn_buy_prices, isbn_amazon_prices=isbn_amazon_prices)
            new_acc: List[Dict] = []
            new_rej: List[Dict] = []
            for r in results:
                d = r.to_dict()
                if r.accepted:
                    accepted.append(d)
                    new_acc.append(d)
                else:
                    rejected.append(d)
                    new_rej.append(d)
            done_count += 1
            if on_progress:
                try:
                    import inspect
                    sig = inspect.signature(on_progress)
                    if len(sig.parameters) >= 4:
                        on_progress(done_count, total, new_acc, new_rej)
                    else:
                        on_progress(done_count, total)
                except Exception:
                    pass

    await asyncio.gather(*[_run(isbn) for isbn in isbns if isbn.strip()])

    # Accepted'i ROI'ye göre sırala
    accepted.sort(key=lambda x: x.get("roi_pct", 0), reverse=True)

    duration = round(time.time() - t0, 1)
    logger.info("csv_arb scan done: %d ISBN, %d accepted, %d rejected, %.1fs",
                total, len(accepted), len(rejected), duration)

    # ── Observability counters ────────────────────────────────────────────────
    all_results = accepted + rejected
    # match_quality / query_mode now on ArbResult dataclass fields (not only dicts)
    def _mq(r): return r.match_quality if hasattr(r, "match_quality") else (r.get("match_quality","") if isinstance(r, dict) else "")
    def _qm(r): return r.query_mode    if hasattr(r, "query_mode")    else (r.get("query_mode","")    if isinstance(r, dict) else "")
    gtin_hits            = sum(1 for r in all_results if _qm(r) == "gtin")
    keyword_fallback_hits= sum(1 for r in all_results if _qm(r) == "keyword_fallback")
    confirmed_count      = sum(1 for r in all_results if _mq(r) == "CONFIRMED")
    unverified_count     = sum(1 for r in all_results if _mq(r) in ("UNVERIFIED_SUPER_DEAL","UNVERIFIED_KEYWORD"))
    invalid_input_count  = sum(1 for r in all_results if _mq(r) == "UNVERIFIED_INPUT")

    # Count amazon_unavailable
    amazon_unavailable = sum(
        1 for r in rejected
        if "amazon_unavailable" in (r.get("reason") or "")
    )

    return {
        "accepted": accepted,
        "rejected": rejected,
        "stats": {
            "total_isbns": total,
            "accepted_count": len(accepted),
            "rejected_count": len(rejected),
            "duration_s": duration,
            "strict_mode": filters.strict_mode,
            "isbn_match_policy": filters.isbn_match_policy.value,
            "invalid_isbn_policy": filters.invalid_isbn_policy.value,
            "gtin_hits": gtin_hits,
            "keyword_fallback_hits": keyword_fallback_hits,
            "confirmed_count": confirmed_count,
            "unverified_count": unverified_count,
            "invalid_input_count": invalid_input_count,
            "amazon_unavailable": amazon_unavailable,
        },
    }


# ── max_buy_price hesabı (dinamik limit önerisi) ──────────────────────────────

def suggest_max_buy(
    sell_price: float,
    target_roi_pct: float,
    fees: FeeConfig = DEFAULT_FEES,
) -> Optional[float]:
    """
    Hedef ROI için maksimum alım fiyatı:
    max_buy = (sell - fees) / (1 + target_roi/100)
    """
    if sell_price <= 0:
        return None
    referral = max(1.00, sell_price * fees.referral_pct)
    total_fees = referral + fees.closing_fee + fees.fulfillment + fees.inbound
    net = sell_price - total_fees
    if net <= 0:
        return None
    return round(net / (1 + target_roi_pct / 100), 2)
