"""
Amazon Price Fallback — SerpApi / Serper Google Shopping

SP-API buybox döndürmediğinde veya Amazon ASIN bulunamadığında
Google Shopping üzerinden Amazon'un güncel fiyatını tahmin eder.

Ücretsiz limitler (Mart 2026):
  Serper:  2.500 req/ay  → günlük ~83 sorgu
  SerpApi: 250 req/ay   → günlük ~8 sorgu (yedek)

Kullanım stratejisi:
  - Her ISBN için max 1 sorgu (başlık veya ISBN ile)
  - Sadece amazon_data boş geldiğinde tetiklenir
  - 6 saatlik cache — aynı gün tekrar sorgu atılmaz
  - Sonuç yapısı: amazon_data ile aynı schema → drop-in replacement
"""
from __future__ import annotations

import time
import logging
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger("trackerbundle.amazon_fallback")

# ── Cache ─────────────────────────────────────────────────────────────────────
_cache: Dict[str, tuple[float, Dict]] = {}  # isbn → (ts, data)
_CACHE_TTL = 3600 * 6  # 6 saat

# ── Providerlar ───────────────────────────────────────────────────────────────
_SERPER_URL  = "https://google.serper.dev/shopping"
_SERPAPI_URL = "https://serpapi.com/search"


def _cached(isbn: str) -> Optional[Dict]:
    if isbn in _cache:
        ts, data = _cache[isbn]
        if time.time() - ts < _CACHE_TTL:
            return data
    return None


def _store(isbn: str, data: Dict) -> None:
    _cache[isbn] = (time.time(), data)


def _parse_price(price_str: Any) -> Optional[float]:
    """'$45.99', 45.99, '$45' → 45.99"""
    if price_str is None:
        return None
    try:
        return float(str(price_str).replace("$", "").replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _build_amazon_data(price: float, condition: str = "used") -> Dict:
    """
    Bulunan fiyattan scanner-uyumlu amazon_data dict'i oluştur.
    Kaynak: google_shopping → güven düşük, conservative olarak işaretle.
    """
    entry = {
        "total": price,
        "price": price,
        "ship": 0.0,
        "label": "A",
        "buybox": True,
        "source": "google_shopping_fallback",
    }
    data: Dict[str, Any] = {condition: {"buybox": entry, "top2": [entry]}}
    # Her iki kondisyon için de sun — hangisi uygunsa scanner seçer
    if condition == "used":
        data["new"] = {"buybox": None, "top2": []}
    else:
        data["used"] = {"buybox": None, "top2": []}
    data["_fallback"] = True  # downstream'e kaynak bildir
    return data


async def _try_serper(
    query: str,
    api_key: str,
    client: httpx.AsyncClient,
) -> Optional[float]:
    """Serper Google Shopping API → Amazon fiyatı çıkarmaya çalış."""
    try:
        r = await client.post(
            _SERPER_URL,
            json={"q": query, "gl": "us", "hl": "en", "num": 10},
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            timeout=8,
        )
        if r.status_code != 200:
            logger.debug("Serper HTTP %d for query=%s", r.status_code, query)
            return None
        results = r.json().get("shopping") or []
        for item in results:
            source = (item.get("source") or "").lower()
            if "amazon" in source:
                price = _parse_price(item.get("price") or item.get("extractedPrice"))
                if price and price > 0:
                    logger.debug("Serper found Amazon price=%.2f for %s", price, query)
                    return price
    except Exception as e:
        logger.debug("Serper error: %s", e)
    return None


async def _try_serpapi(
    query: str,
    api_key: str,
    client: httpx.AsyncClient,
) -> Optional[float]:
    """SerpApi Google Shopping API → Amazon fiyatı çıkarmaya çalış."""
    try:
        r = await client.get(
            _SERPAPI_URL,
            params={
                "engine": "google_shopping",
                "q": query,
                "api_key": api_key,
                "gl": "us",
                "hl": "en",
                "num": 10,
            },
            timeout=10,
        )
        if r.status_code != 200:
            return None
        for item in (r.json().get("shopping_results") or []):
            source = (item.get("source") or "").lower()
            if "amazon" in source:
                price = _parse_price(item.get("price") or item.get("extracted_price"))
                if price and price > 0:
                    logger.debug("SerpApi found Amazon price=%.2f for %s", price, query)
                    return price
    except Exception as e:
        logger.debug("SerpApi error: %s", e)
    return None


async def get_amazon_price_via_shopping(
    isbn: str,
    title: str = "",
    condition_hint: str = "used",
) -> Dict[str, Any]:
    """
    ISBN veya title ile Google Shopping'de Amazon fiyatı ara.

    Args:
        isbn:           Kitap ISBN (13 veya 10 hane)
        title:          Kitap başlığı (ISBN hit olmadığında kullanılır)
        condition_hint: "used" veya "new" — hangi kondisyon buybox aranıyor

    Returns:
        amazon_data dict (scanner-uyumlu) veya {} (bulunamadı)
    """
    from app.core.config import get_settings
    s = get_settings()
    serper_key  = getattr(s, "serper_api_key", None)
    serpapi_key = getattr(s, "serpapi_key", None)

    if not serper_key and not serpapi_key:
        return {}

    # Cache kontrolü
    cached = _cached(isbn)
    if cached is not None:
        logger.debug("Amazon fallback cache HIT isbn=%s", isbn)
        return cached

    queries = []
    if isbn:
        queries.append(f"ISBN {isbn} site:amazon.com")
    if title:
        # Title kısalt + Amazon site filter
        short_title = title[:40].strip()
        queries.append(f"{short_title} amazon.com book")

    price: Optional[float] = None
    async with httpx.AsyncClient(timeout=12) as client:
        for query in queries:
            if not price and serper_key:
                price = await _try_serper(query, serper_key, client)
            if not price and serpapi_key:
                price = await _try_serpapi(query, serpapi_key, client)
            if price:
                break

    if not price or price <= 0:
        logger.debug("Amazon fallback found nothing for isbn=%s", isbn)
        _store(isbn, {})
        return {}

    result = _build_amazon_data(price, condition_hint)
    logger.info(
        "Amazon fallback SUCCESS isbn=%s price=%.2f condition=%s",
        isbn, price, condition_hint,
    )
    _store(isbn, result)
    return result
