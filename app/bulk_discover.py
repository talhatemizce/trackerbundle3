"""
Bulk ISBN Discovery Engine
POST /discover/bulk  → Paralel tarama: eBay fiyat + Amazon fiyat + profit hesaplama

Girdi: ISBN listesi (max 200)
Çıktı: Her ISBN için scored analiz tablosu — 4 kaynak karşılaştırması:
  1. eBay Used   → Amazon used buybox
  2. eBay New    → Amazon new  buybox
  3. ThriftBooks → Amazon used buybox
  4. AbeBooks    → Amazon used buybox  (BookFinder üzerinden)
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import get_settings
from app.ebay_client import browse_search_isbn, item_total_price, normalize_condition
from app.amazon_client import get_top2_prices
from app.profit_calc import suggest_limit, calculate as profit_calc
from app.rules_store import effective_limit
from app.deal_scorer import score_deal, score_to_tier
from app.bookfinder_client import _src_thriftbooks as _tb_scrape, _src_bookfinder as _bf_scrape

logger = logging.getLogger("trackerbundle.bulk_discover")

MAX_ISBNS      = 200
CONCURRENCY    = 4     # paralel ISBN tarama
EBAY_ITEMS_PER_ISBN = 5
TB_TIMEOUT_S   = 12   # ThriftBooks timeout
BF_TIMEOUT_S   = 18   # BookFinder (AbeBooks) timeout

# eBay condition bucket'ları "new" sayılanlar
_EBAY_NEW_BUCKETS = {"brand_new"}


def _extract_price(amazon_data: Dict, section: str) -> Optional[float]:
    """Amazon verisinden buybox veya top1 fiyatını çek."""
    s = amazon_data.get(section) or {}
    bb = s.get("buybox")
    if bb and bb.get("total"):
        return round(float(bb["total"]), 2)
    top2 = s.get("top2") or []
    if top2 and top2[0].get("total"):
        return round(float(top2[0]["total"]), 2)
    return None


def _build_deal(buy_price: float, amazon_data: Optional[dict],
                source: str, condition: str = "used") -> Optional[dict]:
    """
    Tek alış fiyatı + Amazon verisiyle profit hesapla.
    condition="new" → sadece Amazon new buybox kullanılır.
    condition="used" (default) → used buybox öncelikli, sonra new.
    """
    if not amazon_data:
        return None
    if condition == "new":
        amz = {"new": amazon_data.get("new")}
    else:
        amz = amazon_data
    pc = profit_calc(buy_price, amz)
    if not pc:
        return None
    d = pc.to_dict()
    d["source"] = source
    d["buy_price"] = round(buy_price, 2)
    return d


async def _safe_amazon(isbn: str) -> Optional[dict]:
    try:
        return await get_top2_prices(isbn)
    except Exception:
        return None


async def _safe_thriftbooks(client: httpx.AsyncClient, isbn: str) -> Optional[dict]:
    try:
        return await asyncio.wait_for(_tb_scrape(client, isbn), timeout=TB_TIMEOUT_S)
    except Exception:
        return None


async def _safe_bookfinder(client: httpx.AsyncClient, isbn: str) -> Optional[dict]:
    try:
        return await asyncio.wait_for(_bf_scrape(client, isbn), timeout=BF_TIMEOUT_S)
    except Exception:
        return None


async def _scan_single_isbn(
    client: httpx.AsyncClient,
    isbn: str,
) -> Dict[str, Any]:
    """Tek ISBN için 4 kaynak (eBay used/new + ThriftBooks + AbeBooks) + Amazon arbitraj."""
    result: Dict[str, Any] = {
        "isbn":          isbn,
        "ok":            False,
        "ebay":          None,
        "ebay_new":      None,
        "amazon":        None,
        "thriftbooks":   None,
        "abebooks":      None,
        "suggestion":    None,
        "best_deal":     None,
        "best_deal_new": None,
        "deals":         [],     # tüm kaynak sonuçları (UI için)
        "score":         0,
        "error":         None,
    }

    s       = get_settings()
    calc_est = s.calculated_ship_estimate_usd if s.calculated_ship_estimate_usd > 0 else None

    # ── 1. eBay tarama ───────────────────────────────────────────────────────
    try:
        items = await browse_search_isbn(client, isbn, limit=20)
    except Exception as e:
        result["error"] = f"eBay: {e}"
        return result

    all_candidates: List[dict] = []
    for it in items:
        total = item_total_price(it, calc_ship_est=calc_est)
        if total is None:
            continue
        bucket = normalize_condition(it.get("condition"), it.get("conditionId"))
        all_candidates.append({
            "item_id":       it.get("itemId", ""),
            "title":         (it.get("title") or "")[:100],
            "total":         round(total, 2),
            "condition":     bucket,
            "url":           it.get("itemWebUrl", ""),
            "make_offer":    "BEST_OFFER" in (it.get("buyingOptions") or []),
            "ship_estimated": it.get("_shipping_estimated", False),
        })

    all_candidates.sort(key=lambda x: x["total"])

    # Used vs New ayrımı
    used_cands = [c for c in all_candidates if c["condition"] not in _EBAY_NEW_BUCKETS]
    new_cands  = [c for c in all_candidates if c["condition"] in _EBAY_NEW_BUCKETS]

    # eBay özet blokları
    cheapest_used = used_cands[:EBAY_ITEMS_PER_ISBN]
    cheapest_new  = new_cands[:EBAY_ITEMS_PER_ISBN]

    result["ebay"] = {
        "total_found": len(items),
        "cheapest":    cheapest_used,
        "min_price":   cheapest_used[0]["total"] if cheapest_used else None,
    }
    result["ebay_new"] = {
        "cheapest":  cheapest_new,
        "min_price": cheapest_new[0]["total"] if cheapest_new else None,
        "count":     len(new_cands),
    }

    # ── 2. Amazon + ThriftBooks + BookFinder paralel ─────────────────────────
    amazon_data, tb_raw, bf_raw = await asyncio.gather(
        _safe_amazon(isbn),
        _safe_thriftbooks(client, isbn),
        _safe_bookfinder(client, isbn),
    )

    if amazon_data:
        result["amazon"] = {
            "new_buybox":  _extract_price(amazon_data, "new"),
            "used_buybox": _extract_price(amazon_data, "used"),
        }

    # ── 3. Limit önerisi (used bazlı) ────────────────────────────────────────
    if amazon_data:
        for roi in [30, 20, 15]:
            sug = suggest_limit(amazon_data, target_roi_pct=float(roi))
            if sug:
                result["suggestion"] = sug.to_dict()
                break

    # ── 4. Tüm kaynaklardan deal hesapla ─────────────────────────────────────
    deals: List[dict] = []

    # 4a. eBay Used → Amazon used
    if cheapest_used and amazon_data:
        d = _build_deal(cheapest_used[0]["total"], amazon_data, "eBay Used", "used")
        if d:
            d["url"]       = cheapest_used[0].get("url", "")
            d["condition"] = cheapest_used[0].get("condition", "")
            deals.append(d)
            result["best_deal"] = d

    # 4b. eBay New → Amazon new
    if cheapest_new and amazon_data:
        d = _build_deal(cheapest_new[0]["total"], amazon_data, "eBay New", "new")
        if d:
            d["url"]       = cheapest_new[0].get("url", "")
            d["condition"] = "brand_new"
            deals.append(d)
            result["best_deal_new"] = d

    # 4c. ThriftBooks → Amazon used
    if isinstance(tb_raw, dict) and tb_raw:
        tb_min = (tb_raw.get("used") or {}).get("min")
        tb_min_new = (tb_raw.get("new") or {}).get("min")
        result["thriftbooks"] = {
            "used_min": tb_min,
            "new_min":  tb_min_new,
            "url":      tb_raw.get("url", ""),
        }
        if tb_min and amazon_data:
            d = _build_deal(tb_min, amazon_data, "ThriftBooks", "used")
            if d:
                d["url"] = tb_raw.get("url", "")
                d["condition"] = "used"
                deals.append(d)
        if tb_min_new and amazon_data:
            d = _build_deal(tb_min_new, amazon_data, "ThriftBooks New", "new")
            if d:
                d["url"] = tb_raw.get("url", "")
                d["condition"] = "brand_new"
                deals.append(d)

    # 4d. AbeBooks (via BookFinder affiliate=ABEBOOKS) → Amazon used
    if isinstance(bf_raw, dict) and bf_raw:
        abe_offers = [
            o for o in ((bf_raw.get("used") or {}).get("offers") or [])
            if o.get("seller_id") == "ABEBOOKS"
        ]
        abe_new_offers = [
            o for o in ((bf_raw.get("new") or {}).get("offers") or [])
            if o.get("seller_id") == "ABEBOOKS"
        ]
        abe_used_min = min((o["total"] for o in abe_offers), default=None)
        abe_new_min  = min((o["total"] for o in abe_new_offers), default=None)
        result["abebooks"] = {
            "used_min": abe_used_min,
            "new_min":  abe_new_min,
            "url":      f"https://www.abebooks.com/servlet/SearchResults?isbn={isbn}&n=100121503",
        }
        if abe_used_min and amazon_data:
            d = _build_deal(abe_used_min, amazon_data, "AbeBooks", "used")
            if d:
                d["url"] = result["abebooks"]["url"]
                d["condition"] = "used"
                deals.append(d)
        if abe_new_min and amazon_data:
            d = _build_deal(abe_new_min, amazon_data, "AbeBooks New", "new")
            if d:
                d["url"] = result["abebooks"]["url"]
                d["condition"] = "brand_new"
                deals.append(d)

    # Deals: ROI'ya göre sırala — en iyi fırsat önce
    deals.sort(key=lambda x: x.get("roi_pct", -999), reverse=True)
    result["deals"] = deals

    # En iyi deal → score hesaplama
    best = deals[0] if deals else {}
    best_item = cheapest_used[0] if cheapest_used else {}
    sug  = result.get("suggestion") or {}

    bd = score_deal(
        roi_pct        = best.get("roi_pct"),
        condition      = best.get("condition") or best_item.get("condition"),
        ebay_total     = best.get("buy_price") or best_item.get("total"),
        max_limit      = sug.get("max_buy"),
        ebay_count     = len(items),
        amazon_data    = amazon_data,
        sell_source    = best.get("sell_source"),
        viable         = best.get("viable", False),
        ship_estimated = best_item.get("ship_estimated", False),
        make_offer     = best_item.get("make_offer", False),
    )
    result["score"]           = bd.total
    result["score_tier"]      = score_to_tier(bd.total)
    result["score_breakdown"] = bd.to_dict()
    result["ok"]              = True
    return result


async def bulk_discover(isbns: List[str]) -> Dict[str, Any]:
    """
    Birden fazla ISBN'i paralel olarak tara.
    Returns: {"results": [...], "total": N, "scanned": N, "duration_s": float}
    """
    isbns = list(dict.fromkeys(isbns))[:MAX_ISBNS]  # deduplicate + cap
    start = time.time()
    sem   = asyncio.Semaphore(CONCURRENCY)

    async def _scan_with_sem(isbn: str) -> Dict[str, Any]:
        async with sem:
            try:
                async with httpx.AsyncClient(
                    follow_redirects=True, timeout=30
                ) as client:
                    return await _scan_single_isbn(client, isbn)
            except Exception as e:
                return {"isbn": isbn, "ok": False, "error": str(e), "score": 0, "deals": []}

    results = await asyncio.gather(*[_scan_with_sem(isbn) for isbn in isbns])

    # Skora göre sırala (en iyi fırsatlar önce)
    results = sorted(results, key=lambda x: x.get("score", 0), reverse=True)

    return {
        "results":    list(results),
        "total":      len(isbns),
        "scanned":    sum(1 for r in results if r.get("ok")),
        "duration_s": round(time.time() - start, 1),
    }
