"""
Reverse Lookup — Amazon bestseller kitapları tara, eBay'de ucuzunu bul.

POST /discover/reverse
  { "max_bsr": 100000, "category": "books", "limit": 20 }

Amazon SP-API ile belirli BSR aralığındaki kitapları bul,
her birini eBay'de ara ve arbitraj fırsatlarını sırala.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import get_settings
from app.ebay_client import browse_search_isbn, item_total_price, normalize_condition, isbn_variants
from app.amazon_client import get_top2_prices
from app.profit_calc import suggest_limit, calculate as profit_calc

logger = logging.getLogger("trackerbundle.reverse_lookup")

CONCURRENCY = 3
MAX_RESULTS = 50


async def _check_isbn_on_ebay(
    client: httpx.AsyncClient,
    isbn: str,
    amazon_price: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """ISBN'i eBay'de ara, en ucuz listing'i bul, profit hesapla."""
    s = get_settings()
    calc_est = s.calculated_ship_estimate_usd if s.calculated_ship_estimate_usd > 0 else None

    try:
        items = await browse_search_isbn(client, isbn, limit=10)
    except Exception:
        return None

    if not items:
        return None

    # En ucuz item'ı bul
    best = None
    best_total = 99999.0
    for it in items:
        total = item_total_price(it, calc_ship_est=calc_est)
        if total is not None and total < best_total:
            best_total = total
            best = it

    if best is None:
        return best

    bucket = normalize_condition(best.get("condition"), best.get("conditionId"))

    return {
        "isbn": isbn,
        "ebay_total": round(best_total, 2),
        "ebay_title": (best.get("title") or "")[:100],
        "ebay_url": best.get("itemWebUrl", ""),
        "ebay_condition": bucket,
        "ebay_item_id": best.get("itemId", ""),
        "make_offer": "BEST_OFFER" in (best.get("buyingOptions") or []),
        "amazon_price": amazon_price,
    }


async def reverse_lookup(
    isbns: List[str],
    target_roi: float = 30.0,
) -> Dict[str, Any]:
    """
    ISBN listesi al, her birini eBay+Amazon'da kontrol et.
    Profit hesapla, skora göre sırala.

    Bu fonksiyon doğrudan bir ISBN listesi alır.
    Panel'den veya Telegram'dan çağrılabilir.
    """
    start = time.time()
    isbns = list(dict.fromkeys(isbns))[:MAX_RESULTS]
    sem = asyncio.Semaphore(CONCURRENCY)
    results: List[Dict[str, Any]] = []

    async def _process(isbn: str):
        async with sem:
            try:
                # Amazon fiyat
                amazon_data = None
                try:
                    amazon_data = await get_top2_prices(isbn)
                except Exception:
                    pass

                amazon_used = None
                amazon_new = None
                if amazon_data:
                    used_section = amazon_data.get("used") or {}
                    new_section = amazon_data.get("new") or {}
                    bb_used = used_section.get("buybox")
                    bb_new = new_section.get("buybox")
                    if bb_used and bb_used.get("total"):
                        amazon_used = float(bb_used["total"])
                    if bb_new and bb_new.get("total"):
                        amazon_new = float(bb_new["total"])

                amazon_price = amazon_used or amazon_new

                # eBay tarama
                async with httpx.AsyncClient(timeout=20) as client:
                    ebay_result = await _check_isbn_on_ebay(client, isbn, amazon_price)

                if ebay_result is None:
                    return

                # Profit hesapla
                profit_data = None
                suggestion_data = None
                if amazon_data and ebay_result:
                    pc = profit_calc(ebay_result["ebay_total"], amazon_data)
                    if pc:
                        profit_data = pc.to_dict()

                    sug = suggest_limit(amazon_data, target_roi_pct=target_roi)
                    if sug:
                        suggestion_data = sug.to_dict()

                entry = {
                    **ebay_result,
                    "amazon_used": amazon_used,
                    "amazon_new": amazon_new,
                    "profit": profit_data,
                    "suggestion": suggestion_data,
                    "score": 0,
                }

                # Skor hesapla
                if profit_data:
                    roi = profit_data.get("roi_pct", 0)
                    if roi >= 50:
                        entry["score"] = 95
                    elif roi >= 30:
                        entry["score"] = 80
                    elif roi >= 15:
                        entry["score"] = 60
                    elif roi > 0:
                        entry["score"] = 40
                    else:
                        entry["score"] = 20
                elif amazon_price and ebay_result["ebay_total"] < amazon_price * 0.7:
                    entry["score"] = 50  # Amazon verisi eksik ama fiyat farkı var
                else:
                    entry["score"] = 15

                results.append(entry)
            except Exception as e:
                logger.debug("reverse_lookup isbn=%s error: %s", isbn, e)

    await asyncio.gather(*[_process(isbn) for isbn in isbns])

    # Skora göre sırala
    results.sort(key=lambda x: x.get("score", 0), reverse=True)

    return {
        "results": results,
        "total_input": len(isbns),
        "found": len(results),
        "duration_s": round(time.time() - start, 1),
    }
