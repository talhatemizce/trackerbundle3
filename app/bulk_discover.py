"""
Bulk ISBN Discovery Engine
POST /discover/bulk  → Paralel tarama: eBay fiyat + Amazon fiyat + profit hesaplama

Girdi: ISBN listesi (max 200)
Çıktı: Her ISBN için scored analiz tablosu
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

logger = logging.getLogger("trackerbundle.bulk_discover")

MAX_ISBNS = 200
CONCURRENCY = 4           # paralel ISBN tarama
EBAY_ITEMS_PER_ISBN = 5   # en ucuz 5 item'a bak


async def _scan_single_isbn(
    client: httpx.AsyncClient,
    isbn: str,
) -> Dict[str, Any]:
    """Tek ISBN için eBay + Amazon + profit bilgisi topla."""
    result: Dict[str, Any] = {
        "isbn": isbn,
        "ok": False,
        "ebay": None,
        "amazon": None,
        "suggestion": None,
        "best_deal": None,
        "score": 0,
        "error": None,
    }

    s = get_settings()
    calc_est = s.calculated_ship_estimate_usd if s.calculated_ship_estimate_usd > 0 else None

    # ── eBay tarama ──────────────────────────────────────────────────────────
    try:
        items = await browse_search_isbn(client, isbn, limit=20)
    except Exception as e:
        result["error"] = f"eBay: {e}"
        return result

    # En ucuz item'ları bul
    candidates = []
    for it in items:
        total = item_total_price(it, calc_ship_est=calc_est)
        if total is None:
            continue
        bucket = normalize_condition(it.get("condition"), it.get("conditionId"))
        candidates.append({
            "item_id": it.get("itemId", ""),
            "title": (it.get("title") or "")[:100],
            "total": round(total, 2),
            "condition": bucket,
            "url": it.get("itemWebUrl", ""),
            "make_offer": "BEST_OFFER" in (it.get("buyingOptions") or []),
            "ship_estimated": it.get("_shipping_estimated", False),
        })

    candidates.sort(key=lambda x: x["total"])
    candidates = candidates[:EBAY_ITEMS_PER_ISBN]

    result["ebay"] = {
        "total_found": len(items),
        "cheapest": candidates,
        "min_price": candidates[0]["total"] if candidates else None,
    }

    # ── Amazon fiyat ─────────────────────────────────────────────────────────
    # ISBN → ASIN dönüşümüne gerek yok, isbn'in kendisi ASIN olabilir
    # veya bookfinder'dan ASIN bulunabilir. Şimdilik ISBN'i ASIN olarak dene.
    amazon_data = None
    try:
        amazon_data = await get_top2_prices(isbn)
        result["amazon"] = {
            "new_buybox": _extract_price(amazon_data, "new"),
            "used_buybox": _extract_price(amazon_data, "used"),
        }
    except Exception:
        # Amazon verisi opsiyonel — yoksa sadece eBay verisi göster
        result["amazon"] = None

    # ── Dinamik limit önerisi ────────────────────────────────────────────────
    if amazon_data:
        for roi in [30, 20, 15]:
            sug = suggest_limit(amazon_data, target_roi_pct=float(roi))
            if sug:
                result["suggestion"] = sug.to_dict()
                break

    # ── Profit hesaplama (en ucuz eBay + Amazon fiyatla) ─────────────────────
    if candidates and amazon_data:
        best_ebay = candidates[0]["total"]
        pc = profit_calc(best_ebay, amazon_data)
        if pc:
            result["best_deal"] = pc.to_dict()

    # ── Skor hesapla ─────────────────────────────────────────────────────────
    result["score"] = _compute_discover_score(result)
    result["ok"] = True
    return result


def _extract_price(amazon_data: Dict, section: str) -> Optional[float]:
    """Amazon verinden buybox veya top1 fiyatını çek."""
    s = amazon_data.get(section) or {}
    bb = s.get("buybox")
    if bb and bb.get("total"):
        return round(float(bb["total"]), 2)
    top2 = s.get("top2") or []
    if top2 and top2[0].get("total"):
        return round(float(top2[0]["total"]), 2)
    return None


def _compute_discover_score(result: Dict) -> int:
    """0-100 keşif skoru. Yüksek = daha iyi fırsat."""
    score = 0

    # eBay'de ürün var mı?
    ebay = result.get("ebay") or {}
    cheapest = ebay.get("cheapest") or []
    if not cheapest:
        return 0
    score += 20  # eBay'de ürün bulundu

    # Amazon verisi var mı?
    if result.get("amazon"):
        score += 15

    # Profit hesaplanabildi mi?
    deal = result.get("best_deal")
    if deal:
        roi = deal.get("roi_pct", 0)
        if roi >= 50:
            score += 40
        elif roi >= 30:
            score += 30
        elif roi >= 15:
            score += 20
        elif roi > 0:
            score += 10

        # Viable bonus
        if deal.get("viable"):
            score += 15

    # Suggestion bonus
    if result.get("suggestion"):
        score += 10

    return min(100, score)


async def bulk_discover(isbns: List[str]) -> Dict[str, Any]:
    """
    Birden fazla ISBN'i paralel olarak tara.
    Returns: {"results": [...], "total": N, "scanned": N, "duration_s": float}
    """
    isbns = list(dict.fromkeys(isbns))[:MAX_ISBNS]  # deduplicate + cap
    start = time.time()
    sem = asyncio.Semaphore(CONCURRENCY)

    async def _scan_with_sem(isbn: str) -> Dict[str, Any]:
        async with sem:
            try:
                return await _scan_single_isbn(
                    httpx.AsyncClient(timeout=25),
                    isbn,
                )
            except Exception as e:
                return {"isbn": isbn, "ok": False, "error": str(e), "score": 0}

    results = await asyncio.gather(*[_scan_with_sem(isbn) for isbn in isbns])

    # Skora göre sırala (en iyi fırsatlar önce)
    results = sorted(results, key=lambda x: x.get("score", 0), reverse=True)

    return {
        "results": results,
        "total": len(isbns),
        "scanned": sum(1 for r in results if r.get("ok")),
        "duration_s": round(time.time() - start, 1),
    }
"""Bulk Discover Engine — TrackerBundle3"""
