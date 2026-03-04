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

import asyncio
import logging
import math
import time
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.profit_calc import FeeConfig, DEFAULT_FEES, _tier

logger = logging.getLogger("trackerbundle.csv_arb_scanner")

# ── ISBN dönüşüm ──────────────────────────────────────────────────────────────

def _isbn13_to_asin(isbn: str) -> Optional[str]:
    """ISBN-13 → ISBN-10 (= Amazon ASIN for books). 978 prefix only."""
    s = isbn.replace("-", "").replace(" ", "").strip()
    if len(s) == 10:
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
    amazon_sell_price: Optional[float]
    buybox_type: Optional[str]   # "new" | "used"
    match_type: Optional[str]    # "NEW→NEW" | "USED→USED" | "NEW→USED(fallback)" etc.
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
    return ""


# ── Amazon fiyat çekimi (cache'li) ───────────────────────────────────────────

_amz_cache: Dict[str, Tuple[float, Dict]] = {}  # asin → (ts, data)
_AMZ_TTL = 20 * 60  # 20 dakika


async def _get_amazon_prices(asin: str) -> Dict[str, Any]:
    """get_top2_prices ile Amazon buybox fiyatlarını çek, 20dk cache'le."""
    now = time.time()
    if asin in _amz_cache:
        ts, data = _amz_cache[asin]
        if now - ts < _AMZ_TTL:
            return data

    from app import amazon_client as _amz
    try:
        data = await _amz.get_top2_prices(asin)
        _amz_cache[asin] = (now, data)
        return data
    except Exception as e:
        logger.warning("Amazon prices failed asin=%s: %s", asin, e)
        return {}


# ── eBay fiyat çekimi (mevcut Finding API'yi kullan) ─────────────────────────

async def _get_ebay_offers(isbn: str) -> List[Dict]:
    """eBay'den ISBN için aktif listingleri çek (Finding API)."""
    try:
        from app.ebay_client import browse_search_isbn, item_total_price, normalize_condition
        from app.core.config import get_settings
        s = get_settings()
        calc_est = s.calculated_ship_estimate_usd if s.calculated_ship_estimate_usd > 0 else None

        async with httpx.AsyncClient(timeout=20) as client:
            items = await browse_search_isbn(client, isbn)

        offers = []
        for it in (items or []):
            total = item_total_price(it, calc_ship_est=calc_est)
            if total is None or total <= 0:
                continue
            cond_text = (it.get("condition") or {}).get("conditionDisplayName") or ""
            cond_id = (it.get("condition") or {}).get("conditionId")
            cond = normalize_condition(cond_text, cond_id)
            # normalize → "brand_new" | "like_new" | "very_good" | "good" | "acceptable"
            source_cond = "new" if cond == "brand_new" else "used"
            offers.append({
                "source": "ebay",
                "source_condition": source_cond,
                "buy_price": round(total, 2),
                "item_id": it.get("itemId", ""),
                "title": (it.get("title") or "")[:60],
                "url": it.get("itemWebUrl", ""),
            })
        # En ucuz new + en ucuz used döndür
        best: Dict[str, Dict] = {}
        for o in sorted(offers, key=lambda x: x["buy_price"]):
            c = o["source_condition"]
            if c not in best:
                best[c] = o
        return list(best.values())
    except Exception as e:
        logger.warning("eBay offers failed isbn=%s: %s", isbn, e)
        return []


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


# ── Ana tarayıcı ─────────────────────────────────────────────────────────────

async def _scan_one(
    isbn: str,
    filters: ScanFilters,
    fees: FeeConfig,
    isbn_buy_prices: Dict[str, float] = {},
    isbn_amazon_prices: Dict[str, float] = {},
) -> List[ArbResult]:
    """Tek ISBN için tüm kaynakları tara, ArbResult listesi döndür."""
    asin = _isbn13_to_asin(isbn)
    if not asin:
        r = ArbResult(isbn=isbn, asin=None, source="", source_condition="",
                      buy_price=0, amazon_sell_price=None, buybox_type=None, match_type=None)
        r.reason = "invalid_isbn_or_not_978"
        return [r]

    # Paralel: Amazon + eBay + BookFinder
    amazon_data, ebay_offers, bf_offers = await asyncio.gather(
        _get_amazon_prices(asin),
        _get_ebay_offers(isbn),
        _get_bookfinder_offers(isbn),
        return_exceptions=True,
    )

    if isinstance(amazon_data, Exception):
        amazon_data = {}
    if isinstance(ebay_offers, Exception):
        ebay_offers = []
    if isinstance(bf_offers, Exception):
        bf_offers = []

    all_offers = (ebay_offers or []) + (bf_offers or [])

    # Kullanıcı alım fiyatı (generic CSV) → sentetik offer ekle
    csv_price = isbn_buy_prices.get(isbn) or isbn_buy_prices.get(asin)
    if csv_price and csv_price > 0:
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
        r = ArbResult(isbn=isbn, asin=asin, source="", source_condition="",
                      buy_price=0, amazon_sell_price=None, buybox_type=None, match_type=None)
        r.reason = "no_ebay_listings"
        return [r]

    if not amazon_data:
        results = []
        for o in all_offers:
            r = ArbResult(isbn=isbn, asin=asin, **{k: o[k] for k in
                          ["source", "source_condition", "buy_price"]},
                          amazon_sell_price=None, buybox_type=None, match_type=None)
            r.reason = "amazon_unavailable"
            results.append(r)
        return results

    results = []
    for o in all_offers:
        r = ArbResult(
            isbn=isbn, asin=asin,
            source=o["source"],
            source_condition=o["source_condition"],
            buy_price=o["buy_price"],
            amazon_sell_price=None, buybox_type=None, match_type=None,
        )

        sell_price, bb_type, match_type, reason = _calc_profit_strict(
            o["buy_price"], o["source_condition"], amazon_data,
            strict_mode=filters.strict_mode, fees=fees,
        )

        if sell_price is None:
            r.reason = reason
        else:
            _apply_profit(r, sell_price, bb_type, match_type, fees)
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
    concurrency: int = 3,
    on_progress: Any = None,  # optional callback(done, total)
    isbn_buy_prices: Dict[str, float] = {},     # opsiyonel: kullanıcı alım fiyatları (generic CSV)
    isbn_amazon_prices: Dict[str, float] = {},  # opsiyonel: Amazon Business Report ortalama satış fiyatı
) -> Dict[str, Any]:
    """
    ISBN listesini paralel tara (max `concurrency` aynı anda).
    Returns {accepted, rejected, stats, duration_s}
    """
    t0 = time.time()
    sem = asyncio.Semaphore(concurrency)
    done_count = 0
    total = len(isbns)
    accepted: List[Dict] = []
    rejected: List[Dict] = []

    async def _run(isbn: str):
        nonlocal done_count
        async with sem:
            results = await _scan_one(isbn.strip(), filters, fees, isbn_buy_prices=isbn_buy_prices, isbn_amazon_prices=isbn_amazon_prices)
            for r in results:
                d = r.to_dict()
                if r.accepted:
                    accepted.append(d)
                else:
                    rejected.append(d)
            done_count += 1
            if on_progress:
                try:
                    on_progress(done_count, total)
                except Exception:
                    pass

    await asyncio.gather(*[_run(isbn) for isbn in isbns if isbn.strip()])

    # Accepted'i ROI'ye göre sırala
    accepted.sort(key=lambda x: x.get("roi_pct", 0), reverse=True)

    duration = round(time.time() - t0, 1)
    logger.info("csv_arb scan done: %d ISBN, %d accepted, %d rejected, %.1fs",
                total, len(accepted), len(rejected), duration)

    return {
        "accepted": accepted,
        "rejected": rejected,
        "stats": {
            "total_isbns": total,
            "accepted_count": len(accepted),
            "rejected_count": len(rejected),
            "duration_s": duration,
            "strict_mode": filters.strict_mode,
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
