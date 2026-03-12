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



# ─── Görsel doğrulama (Gemini Vision) ────────────────────────────────────────

async def _verify_image_vision(
    image_url: str,
    isbn: str,
    expected_title: str,
    candidate: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Gemini Vision ile kapak fotoğrafını doğrula.
    Soru: Bu fotoğraf gerçekten beklenen kitabın kapağı mı?
    Kota yoksa skip (vision task → sadece Gemini yapabilir).
    """
    if not image_url:
        return {"status": "NO_IMAGE", "verdict": "NO_IMAGE", "notes": "Görsel URL yok"}

    try:
        from app.ai_analyst import _fetch_image_b64
        from app.llm_router import route as llm_route

        # Görüntüyü indir
        async with httpx.AsyncClient(timeout=15) as client:
            image_b64 = await _fetch_image_b64(image_url, client)

        if not image_b64:
            return {"status": "NO_IMAGE", "verdict": "NO_IMAGE", "notes": "Görsel indirilemedi"}

        isbn13 = isbn
        try:
            from app.isbn_utils import to_isbn13
            isbn13 = to_isbn13(isbn) or isbn
        except Exception:
            pass

        sys_prompt = """You are a book cover verification expert.
Your job: examine the image and determine if it matches the expected book.
Reply ONLY with this JSON (no markdown):
{
  "verdict": "MATCH or MISMATCH or UNCERTAIN or STOCK_PHOTO",
  "confidence": 0-100,
  "notes": "1-2 sentences about what you see",
  "title_visible": true/false,
  "author_visible": true/false,
  "is_stock_photo": true/false,
  "condition_notes": "visible damage, wear, or condition observations"
}

MATCH: cover title/author clearly matches the expected book
MISMATCH: clearly a different book
UNCERTAIN: can't determine (blurry, wrong angle, partial view)
STOCK_PHOTO: plain white background with no imperfections = publisher stock photo (used condition should show real item)
"""

        user_prompt = f"""Expected book:
Title: {expected_title[:100]}
ISBN: {isbn13}
Declared condition: {candidate.get('source_condition', '?')}

Does the eBay listing image show THIS specific book?"""

        result = await llm_route(
            task="vision",
            system_prompt=sys_prompt,
            user_prompt=user_prompt,
            image_b64=image_b64,
            max_tokens=400,
        )

        import json as _json
        text = result["text"].strip()
        # Strip markdown fences
        for fence in ["```json", "```"]:
            if fence in text:
                parts = text.split(fence)
                text = parts[1] if len(parts) >= 3 else text.replace(fence, "")
        text = text.strip()
        s, e = text.find("{"), text.rfind("}") + 1
        if s >= 0 and e > s:
            parsed = _json.loads(text[s:e])
        else:
            parsed = {"verdict": "UNCERTAIN", "notes": text[:200]}

        parsed["provider"] = result.get("provider", "unknown")
        parsed["status"] = parsed.get("verdict", "UNCERTAIN")

        # Stock photo + used condition = risky
        if parsed.get("is_stock_photo") and candidate.get("source_condition") == "used":
            parsed["stock_photo_risk"] = True
            parsed["notes"] = (parsed.get("notes") or "") + " ⚠️ Stock fotoğraf + used kondisyon: gerçek durum gizlenmiş olabilir."

        return parsed

    except Exception as e:
        logger.warning("_verify_image_vision isbn=%s error: %s", isbn, e)
        return {"status": "ERROR", "verdict": "UNCERTAIN", "notes": f"Vision hatası: {str(e)[:80]}"}


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
    image_url = candidate.get("image_url") or candidate.get("ebay_image_url") or ""
    expected_title = candidate.get("title") or candidate.get("ebay_title") or ""

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

    # Adım 3: Vision — eBay sonucu GONE/MISMATCH değilse kapağa bak
    vision_result: Dict[str, Any] = {"status": "SKIP", "verdict": "NO_IMAGE", "notes": ""}
    ebay_ok = ebay_result.get("status") not in ("GONE", "MISMATCH")
    if image_url and ebay_ok:
        try:
            vision_result = await _verify_image_vision(image_url, isbn, expected_title, candidate)
            logger.info("vision verify isbn=%s verdict=%s provider=%s",
                        isbn, vision_result.get("verdict"), vision_result.get("provider"))
        except Exception as ve:
            logger.warning("vision verify failed isbn=%s: %s", isbn, ve)
            vision_result = {"status": "ERROR", "verdict": "UNCERTAIN", "notes": str(ve)[:80]}

    # Nihai durum kararı
    final_status = _decide_final_status(ebay_result, market_result, source, vision_result)
    summary = _build_summary(final_status, ebay_result, market_result, buy_price, vision_result)

    return {
        "status": final_status,
        "ebay": ebay_result,
        "market": market_result,
        "vision": vision_result,
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
    vision: Optional[Dict[str, Any]] = None,
) -> str:
    """eBay, piyasa ve vision sonuçlarını birleştirerek nihai karar ver."""
    # Vision MISMATCH → en kritik, hemen döndür
    if vision and vision.get("verdict") == "MISMATCH":
        return "MISMATCH"

    if source == "ebay":
        ebay_status = ebay.get("status", "ERROR")
        if ebay_status in ("GONE", "MISMATCH"):
            return ebay_status
        if ebay_status == "PRICE_UP":
            return "PRICE_UP"
        if ebay_status == "PRICE_DOWN":
            return "PRICE_DOWN"
        if ebay_status == "VERIFIED":
            # Vision STOCK_PHOTO ile VERIFIED → özel durum
            if vision and vision.get("verdict") == "STOCK_PHOTO":
                return "VERIFIED_STOCK_PHOTO"
            return "VERIFIED"

    # eBay dışı kaynak
    market_status = market.get("status", "ERROR")
    if market_status in ("PRICE_UP", "PRICE_DOWN", "VERIFIED"):
        return market_status

    return "ERROR"


def _build_summary(
    status: str,
    ebay: Dict[str, Any],
    market: Dict[str, Any],
    expected: float,
    vision: Optional[Dict[str, Any]] = None,
) -> str:
    if status == "VERIFIED":
        vision_note = ""
        if vision and vision.get("verdict") == "MATCH":
            vision_note = f" · 📷 Kapak doğru ({vision.get('confidence',0)}% güven)"
        elif vision and vision.get("verdict") == "UNCERTAIN":
            vision_note = " · 📷 Kapak belirsiz"
        return f"✅ İlan doğrulandı — fiyat ${expected:.2f}{vision_note}"
    if status == "VERIFIED_STOCK_PHOTO":
        return f"⚠️ İlan mevcut ama kapak stock fotoğraf — gerçek kondisyon gizli olabilir"
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
