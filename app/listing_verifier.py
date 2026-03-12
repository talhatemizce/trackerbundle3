"""
TrackerBundle3 — Listing Verifier
===================================
AI olmadan saf HTTP/API ile ilan doğrulama.

Her fırsat için kontrol eder:
  1. eBay → item hâlâ aktif mi? fiyat değişti mi? ISBN uyuşuyor mu?
  2. AbeBooks/BookFinder → kaynak fiyat gerçek mi?

Sonuç:
  VERIFIED         — her şey tutarlı, ilan geçerli
  GONE             — ilan silinmiş / satılmış
  PRICE_UP         — fiyat yükselmiş (fırsat zayıfladı)
  PRICE_DOWN       — fiyat düşmüş (daha iyi fırsat!)
  MISMATCH         — eBay ilanı farklı bir kitap (ISBN uyuşmuyor)
  ERROR            — kontrol yapılamadı
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("trackerbundle.verifier")


# ─── eBay item doğrulama ──────────────────────────────────────────────────────

async def _verify_ebay_item(
    item_id: str,
    expected_price: float,
    isbn: str,
    client: httpx.AsyncClient,
) -> Dict[str, Any]:
    """
    eBay Browse API ile item detail çek:
    - Hâlâ aktif mi?
    - Fiyat aynı mı?
    - ISBN/GTIN eşleşiyor mu? (PRODUCT fieldgroup)
    """
    if not item_id:
        return {"status": "ERROR", "reason": "no_item_id"}

    try:
        from app.ebay_client import get_app_token, _browse_base
        from app.isbn_utils import isbn_variants

        token = await get_app_token(client)
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

        if r.status_code == 404:
            return {"status": "GONE", "reason": "item_not_found"}
        if r.status_code == 410:
            return {"status": "GONE", "reason": "item_ended"}
        if r.status_code != 200:
            return {"status": "ERROR", "reason": f"http_{r.status_code}"}

        data = r.json()

        # İlan durumu
        buying_options = data.get("buyingOptions") or []
        estimated_avail = data.get("estimatedAvailabilities") or []
        if estimated_avail:
            avail = estimated_avail[0].get("availabilityThresholdType", "")
            if avail in ("NONE", "SOLD_OUT"):
                return {"status": "GONE", "reason": "sold_out"}

        # Gerçek fiyat
        try:
            current_price = float(data.get("price", {}).get("value", 0))
        except (TypeError, ValueError):
            current_price = 0.0

        # Shipping
        ship_cost = 0.0
        ship_opts = data.get("shippingOptions") or []
        if ship_opts:
            cost_val = ship_opts[0].get("shippingCost", {}).get("value")
            if cost_val:
                try:
                    ship_cost = float(cost_val)
                except (TypeError, ValueError):
                    pass
        total_price = round(current_price + ship_cost, 2)

        # ISBN doğrulama (GTIN veya localizedAspects)
        isbn_match = _check_isbn_in_detail(data, isbn)

        # Fiyat delta
        price_delta = round(total_price - expected_price, 2)
        price_delta_pct = round(price_delta / expected_price * 100, 1) if expected_price else 0

        if total_price > expected_price * 1.05:
            price_status = "PRICE_UP"
        elif total_price < expected_price * 0.92:
            price_status = "PRICE_DOWN"
        else:
            price_status = "PRICE_OK"

        # ISBN mismatch → farklı kitap
        if isbn_match == "MISMATCH":
            return {
                "status": "MISMATCH",
                "reason": "isbn_not_found_in_listing",
                "current_price": total_price,
                "expected_price": expected_price,
                "price_delta": price_delta,
                "item_title": data.get("title", "")[:100],
            }

        final_status = "VERIFIED" if price_status == "PRICE_OK" else price_status
        return {
            "status": final_status,
            "reason": price_status.lower(),
            "current_price": total_price,
            "expected_price": expected_price,
            "price_delta": price_delta,
            "price_delta_pct": price_delta_pct,
            "isbn_check": isbn_match,
            "item_title": data.get("title", "")[:100],
            "condition": data.get("condition", ""),
        }

    except Exception as e:
        logger.warning("_verify_ebay_item item=%s error: %s", item_id, e)
        return {"status": "ERROR", "reason": str(e)[:100]}


def _check_isbn_in_detail(data: Dict[str, Any], isbn: str) -> str:
    """
    Item detail'da ISBN var mı?
    MATCH / MISMATCH / UNKNOWN (GTIN yoksa)
    """
    try:
        from app.isbn_utils import isbn_variants
        variants = {v.upper() for v in isbn_variants(isbn)}
        if not variants:
            return "UNKNOWN"

        # product.gtins
        product = data.get("product") or {}
        gtins = [g.replace("-", "").upper() for g in (product.get("gtins") or [])]
        if gtins:
            return "MATCH" if any(g in variants for g in gtins) else "MISMATCH"

        # localizedAspects
        for asp in (data.get("localizedAspects") or []):
            name_upper = asp.get("name", "").upper()
            if name_upper in ("ISBN", "EAN", "GTIN", "ISBN-10", "ISBN-13", "UPC"):
                val = (asp.get("value") or "").replace("-", "").upper()
                if val in variants:
                    return "MATCH"
                elif val:
                    return "MISMATCH"

        return "UNKNOWN"  # GTIN veri yok (seller girişi atlayabilir)

    except Exception:
        return "UNKNOWN"


# ─── AbeBooks fiyat doğrulama ─────────────────────────────────────────────────

async def _verify_abebooks_price(
    isbn: str,
    expected_price: float,
    client: httpx.AsyncClient,
) -> Dict[str, Any]:
    """AbeBooks ve BookFinder üzerinden mevcut piyasa tabanını doğrula."""
    try:
        from app.bookfinder_client import fetch_bookfinder

        result = await fetch_bookfinder(isbn, condition="all", force=True)
        if not result.get("ok"):
            return {"status": "ERROR", "reason": "bookfinder_failed"}

        # En ucuz fiyatı bul
        cheapest = None
        source = None
        for cond_key in ("used", "new"):
            block = result.get(cond_key)
            if not block:
                continue
            for offer in (block.get("offers") or []):
                p = offer.get("total_price") or offer.get("price")
                if p is not None:
                    try:
                        pf = float(p)
                        if cheapest is None or pf < cheapest:
                            cheapest = pf
                            source = offer.get("source", "")
                    except (TypeError, ValueError):
                        pass

        if cheapest is None:
            return {"status": "ERROR", "reason": "no_prices_found"}

        delta = round(cheapest - expected_price, 2)
        delta_pct = round(delta / expected_price * 100, 1) if expected_price else 0

        if cheapest > expected_price * 1.10:
            status = "PRICE_UP"
        elif cheapest < expected_price * 0.90:
            status = "PRICE_DOWN"
        else:
            status = "VERIFIED"

        return {
            "status": status,
            "cheapest_found": cheapest,
            "expected_price": expected_price,
            "price_delta": delta,
            "price_delta_pct": delta_pct,
            "cheapest_source": source,
        }

    except Exception as e:
        logger.warning("_verify_abebooks isbn=%s error: %s", isbn, e)
        return {"status": "ERROR", "reason": str(e)[:100]}


# ─── Ana verify fonksiyonu ────────────────────────────────────────────────────

async def verify_listing(
    candidate: Dict[str, Any],
    isbn: str,
) -> Dict[str, Any]:
    """
    Tek bir arbitraj fırsatını doğrula.
    Paralel: eBay check + AbeBooks/BookFinder check.

    candidate dict beklenen alanlar:
      source, buy_price, item_id (eBay item_id), ebay_url, ebay_title

    Döner:
      {
        status: VERIFIED|GONE|PRICE_UP|PRICE_DOWN|MISMATCH|ERROR
        ebay: {...}     (eBay spesifik sonuç)
        market: {...}   (AbeBooks/BookFinder sonuç)
        summary: str    (okunabilir özet)
        checked_at: float
      }
    """
    source = candidate.get("source", "")
    buy_price = float(candidate.get("buy_price") or 0)
    item_id = candidate.get("item_id") or candidate.get("ebay_item_id") or ""

    async with httpx.AsyncClient(timeout=20) as client:
        tasks = []

        # eBay items için eBay doğrulama
        if source == "ebay" and item_id:
            tasks.append(_verify_ebay_item(item_id, buy_price, isbn, client))
        else:
            tasks.append(asyncio.sleep(0, result={"status": "SKIP", "reason": "not_ebay"}))

        # Piyasa tabanı kontrolü (AbeBooks/BookFinder)
        tasks.append(_verify_abebooks_price(isbn, buy_price, client))

        results = await asyncio.gather(*tasks, return_exceptions=True)

    ebay_result = results[0] if not isinstance(results[0], Exception) else {"status": "ERROR", "reason": str(results[0])[:100]}
    market_result = results[1] if not isinstance(results[1], Exception) else {"status": "ERROR", "reason": str(results[1])[:100]}

    # Nihai durum kararı
    final_status = _decide_final_status(ebay_result, market_result, source)
    summary = _build_summary(final_status, ebay_result, market_result, buy_price)

    return {
        "status": final_status,
        "ebay": ebay_result,
        "market": market_result,
        "summary": summary,
        "checked_at": time.time(),
        "isbn": isbn,
        "source": source,
        "expected_price": buy_price,
    }


def _decide_final_status(
    ebay: Dict[str, Any],
    market: Dict[str, Any],
    source: str,
) -> str:
    """eBay ve piyasa sonuçlarını birleştirerek nihai karar ver."""
    if source == "ebay":
        ebay_status = ebay.get("status", "ERROR")
        if ebay_status in ("GONE", "MISMATCH"):
            return ebay_status
        if ebay_status == "PRICE_UP":
            return "PRICE_UP"
        if ebay_status == "PRICE_DOWN":
            return "PRICE_DOWN"
        if ebay_status == "VERIFIED":
            return "VERIFIED"

    # eBay dışı kaynak veya eBay check atlandıysa
    market_status = market.get("status", "ERROR")
    if market_status in ("PRICE_UP", "PRICE_DOWN", "VERIFIED"):
        return market_status

    return "ERROR"


def _build_summary(
    status: str,
    ebay: Dict[str, Any],
    market: Dict[str, Any],
    expected: float,
) -> str:
    if status == "VERIFIED":
        return f"✅ İlan doğrulandı — fiyat ${expected:.2f} (değişmemiş)"
    if status == "GONE":
        return f"❌ İlan yok — satılmış veya kaldırılmış"
    if status == "MISMATCH":
        title = ebay.get("item_title", "")
        return f"⚠️ ISBN uyuşmuyor — eBay ilanı farklı kitap: {title[:60]}"
    if status == "PRICE_UP":
        current = ebay.get("current_price") or market.get("cheapest_found") or expected
        delta_pct = ebay.get("price_delta_pct") or market.get("price_delta_pct") or 0
        return f"📈 Fiyat arttı — ${current:.2f} (+{delta_pct:.1f}%) beklenen: ${expected:.2f}"
    if status == "PRICE_DOWN":
        current = ebay.get("current_price") or market.get("cheapest_found") or expected
        delta_pct = abs(ebay.get("price_delta_pct") or market.get("price_delta_pct") or 0)
        return f"📉 Fiyat düştü — ${current:.2f} (-{delta_pct:.1f}%) → daha iyi fırsat!"
    return f"⚠️ Kontrol hatası — {ebay.get('reason', '')} / {market.get('reason', '')}"


# ─── Toplu doğrulama ─────────────────────────────────────────────────────────

async def verify_batch(
    items: List[Dict[str, Any]],
    concurrency: int = 4,
) -> List[Dict[str, Any]]:
    """
    items: [{"isbn": ..., "candidate": {...}}, ...]
    Paralel doğrulama, sonuç sırasını koru.
    """
    sem = asyncio.Semaphore(concurrency)

    async def _run(item: Dict[str, Any]) -> Dict[str, Any]:
        async with sem:
            result = await verify_listing(item["candidate"], item["isbn"])
            result["_index"] = item.get("_index", 0)
            return result

    results = await asyncio.gather(*[_run(it) for it in items], return_exceptions=True)
    return [
        r if not isinstance(r, Exception)
        else {"status": "ERROR", "reason": str(r)[:100], "_index": items[i].get("_index", i)}
        for i, r in enumerate(results)
    ]
