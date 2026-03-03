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
from app.deal_scorer import score_deal, score_to_tier
from app.bookfinder_client import _src_thriftbooks as _tb_scrape, _src_bookfinder as _bf_scrape

_TB_TIMEOUT = 12
_BF_TIMEOUT = 18

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
                async with httpx.AsyncClient(follow_redirects=True, timeout=25) as client:
                    # Paralel: Amazon + eBay + ThriftBooks + BookFinder
                    async def _safe_amazon():
                        try: return await get_top2_prices(isbn)
                        except Exception: return None

                    async def _safe_tb():
                        try: return await asyncio.wait_for(_tb_scrape(client, isbn), timeout=_TB_TIMEOUT)
                        except Exception: return None

                    async def _safe_bf():
                        try: return await asyncio.wait_for(_bf_scrape(client, isbn), timeout=_BF_TIMEOUT)
                        except Exception: return None

                    amazon_data, tb_raw, bf_raw = await asyncio.gather(
                        _safe_amazon(), _safe_tb(), _safe_bf()
                    )

                    amazon_used = amazon_new = None
                    if amazon_data:
                        bb_u = (amazon_data.get("used") or {}).get("buybox")
                        bb_n = (amazon_data.get("new") or {}).get("buybox")
                        if bb_u and bb_u.get("total"): amazon_used = float(bb_u["total"])
                        if bb_n and bb_n.get("total"): amazon_new  = float(bb_n["total"])

                    amazon_price = amazon_used or amazon_new

                    # eBay tarama (best used)
                    ebay_result = await _check_isbn_on_ebay(client, isbn, amazon_price)

                if ebay_result is None:
                    return

                suggestion_data = None
                if amazon_data:
                    sug = suggest_limit(amazon_data, target_roi_pct=target_roi)
                    if sug: suggestion_data = sug.to_dict()

                # ── Tüm kaynaklardan deal hesapla ─────────────────────────────
                deals = []

                def _make_deal(buy_price, src_label, cond_type="used"):
                    if not amazon_data: return None
                    amz = {"new": amazon_data.get("new")} if cond_type == "new" else amazon_data
                    pc = profit_calc(buy_price, amz)
                    if not pc: return None
                    d = pc.to_dict()
                    d["source"]    = src_label
                    d["buy_price"] = round(buy_price, 2)
                    return d

                # eBay used
                profit_data = None
                if ebay_result:
                    d = _make_deal(ebay_result["ebay_total"], "eBay Used", "used")
                    if d:
                        d["url"] = ebay_result.get("ebay_url", "")
                        deals.append(d)
                        profit_data = d

                # ThriftBooks used + new
                if isinstance(tb_raw, dict) and tb_raw:
                    tb_u = (tb_raw.get("used") or {}).get("min")
                    tb_n = (tb_raw.get("new")  or {}).get("min")
                    if tb_u:
                        d = _make_deal(tb_u, "ThriftBooks", "used")
                        if d: d["url"] = tb_raw.get("url",""); deals.append(d)
                    if tb_n:
                        d = _make_deal(tb_n, "ThriftBooks New", "new")
                        if d: d["url"] = tb_raw.get("url",""); deals.append(d)

                # AbeBooks via BookFinder
                if isinstance(bf_raw, dict) and bf_raw:
                    abe_u = [o for o in ((bf_raw.get("used") or {}).get("offers") or []) if o.get("seller_id")=="ABEBOOKS"]
                    abe_n = [o for o in ((bf_raw.get("new")  or {}).get("offers") or []) if o.get("seller_id")=="ABEBOOKS"]
                    abe_url = f"https://www.abebooks.com/servlet/SearchResults?isbn={isbn}&n=100121503"
                    if abe_u:
                        d = _make_deal(min(o["total"] for o in abe_u), "AbeBooks", "used")
                        if d: d["url"] = abe_url; deals.append(d)
                    if abe_n:
                        d = _make_deal(min(o["total"] for o in abe_n), "AbeBooks New", "new")
                        if d: d["url"] = abe_url; deals.append(d)

                deals.sort(key=lambda x: x.get("roi_pct", -999), reverse=True)
                best = deals[0] if deals else {}

                entry = {
                    **ebay_result,
                    "amazon_used":    amazon_used,
                    "amazon_new":     amazon_new,
                    "profit":         profit_data,
                    "suggestion":     suggestion_data,
                    "deals":          deals,
                    "thriftbooks_min": (tb_raw.get("used") or {}).get("min") if isinstance(tb_raw, dict) else None,
                    "abebooks_min":    None,
                    "score":          0,
                }

                # AbeBooks min fiyat (özet)
                if isinstance(bf_raw, dict) and bf_raw:
                    abe_all = [(tb_raw.get("used") or {}).get("offers") or []]
                    abe_u2 = [o for o in ((bf_raw.get("used") or {}).get("offers") or []) if o.get("seller_id")=="ABEBOOKS"]
                    if abe_u2: entry["abebooks_min"] = min(o["total"] for o in abe_u2)

                # ── AI Deal Score ──────────────────────────────────────────────
                bd = score_deal(
                    roi_pct        = best.get("roi_pct"),
                    condition      = ebay_result.get("ebay_condition") if ebay_result else None,
                    ebay_total     = best.get("buy_price") or (ebay_result.get("ebay_total") if ebay_result else None),
                    max_limit      = (suggestion_data or {}).get("max_buy"),
                    ebay_count     = None,
                    amazon_data    = amazon_data,
                    sell_source    = best.get("sell_source"),
                    viable         = best.get("viable", False),
                    ship_estimated = False,
                    make_offer     = (ebay_result or {}).get("make_offer", False),
                )
                entry["score"]           = bd.total
                entry["score_tier"]      = score_to_tier(bd.total)
                entry["score_breakdown"] = bd.to_dict()

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
