"""
Buyback price aggregator.
=====================================================================
İki kaynak:

1. BookScouter API (api.bookscouter.com)
   - 30+ buyback vendor (BooksRun, TextbookRush, Powell's, Chegg, ...)
   - Ücretsiz tier: kayıt gerekli → api.bookscouter.com
   - Env: BOOKSCOUTER_API_KEY
   - Endpoint: GET https://api.bookscouter.com/v1/book/{isbn}/prices?type=sell

2. BooksRun API (booksrun.com/api)
   - Sadece BooksRun fiyatları, ama tamamen ücretsiz
   - Env: BOOKSRUN_API_KEY (kayıt: booksrun.com/page/api-reference)
   - Endpoint: GET https://booksrun.com/api/price/sell/{isbn}?key={key}

Buyback modeli:
  Maliyet  = eBay alım fiyatı + nakliye (~$3.99 media mail)
  Gelir    = buyback_cash_price
  Kar      = Gelir - Maliyet
  NOT: Amazon FBA ücreti YOK — buyback siteleri ücretsiz nakliye verir.

Cache TTL: 4 saat (fiyatlar sık değişmez ama günde birkaç kez güncellenir)
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import get_settings
from app.core.json_store import file_lock, _read_unsafe, _write_unsafe

logger = logging.getLogger("trackerbundle.buyback")

_CACHE_TTL_S = 4 * 3600   # 4 saat
_SHIP_COST   = 3.99        # Media mail buyback'e gönderme maliyeti


# ── Cache ─────────────────────────────────────────────────────────────────────

def _cache_path() -> Path:
    return get_settings().resolved_data_dir() / "buyback_cache.json"


def _cache_get(isbn: str) -> Optional[dict]:
    try:
        data = _read_unsafe(_cache_path(), default={"entries": {}})
        entry = data.get("entries", {}).get(isbn)
        if entry and time.time() - entry.get("ts", 0) < _CACHE_TTL_S:
            return entry
    except Exception:
        pass
    return None


def _cache_set(isbn: str, result: dict) -> None:
    p = _cache_path()
    try:
        with file_lock(p):
            data = _read_unsafe(p, default={"entries": {}})
            now = time.time()
            data["entries"] = {
                k: v for k, v in data.get("entries", {}).items()
                if now - v.get("ts", 0) < _CACHE_TTL_S * 6
            }
            data["entries"][isbn] = {**result, "ts": int(now)}
            _write_unsafe(p, data)
    except Exception:
        pass


# ── BookScouter API ────────────────────────────────────────────────────────────

async def _fetch_bookscouter(isbn: str, client: httpx.AsyncClient) -> List[Dict]:
    """
    BookScouter API → list of vendor offers.
    Returns [] if no key configured or API fails.

    Free registration: https://api.bookscouter.com/
    After signup you get a free API key in your dashboard.
    """
    s = get_settings()
    key = s.bookscouter_api_key
    if not key:
        return []

    try:
        r = await client.get(
            f"https://api.bookscouter.com/v1/book/{isbn}/prices",
            params={"type": "sell"},
            headers={
                "Authorization": f"Bearer {key}",
                "Accept": "application/json",
            },
            timeout=15,
        )
        if r.status_code == 401:
            logger.warning("BookScouter API key invalid")
            return []
        if r.status_code == 429:
            logger.warning("BookScouter API rate limited")
            return []
        if r.status_code != 200:
            logger.warning("BookScouter API status=%d", r.status_code)
            return []

        data = r.json()
        vendors = data.get("data", data.get("vendors", data.get("prices", [])))
        if not isinstance(vendors, list):
            # Try alternate schema
            vendors = data.get("results", [])

        offers = []
        for v in vendors:
            cash = v.get("cashPrice") or v.get("cash_price") or v.get("price") or 0
            credit = v.get("creditPrice") or v.get("credit_price") or 0
            name = v.get("vendorName") or v.get("vendor_name") or v.get("name") or "unknown"
            vendor_id = v.get("vendorId") or v.get("vendor_id") or name.lower().replace(" ", "_")
            url = v.get("url") or v.get("buybackUrl") or f"https://bookscouter.com/book/{isbn}?type=sell"

            try:
                cash = float(cash)
                credit = float(credit)
            except (TypeError, ValueError):
                continue

            if cash > 0:
                offers.append({
                    "vendor": name,
                    "vendor_id": vendor_id,
                    "cash": round(cash, 2),
                    "credit": round(credit, 2),
                    "url": url,
                    "source": "bookscouter",
                })

        return sorted(offers, key=lambda x: x["cash"], reverse=True)

    except Exception as e:
        logger.warning("BookScouter fetch error isbn=%s: %s", isbn, e)
        return []


# ── BooksRun API ───────────────────────────────────────────────────────────────

async def _fetch_booksrun(isbn: str, client: httpx.AsyncClient) -> List[Dict]:
    """
    BooksRun free API — single vendor but free with registration.
    Returns cash prices for Average/Good/New conditions.

    Free signup: https://booksrun.com/page/api-reference
    """
    s = get_settings()
    key = s.booksrun_api_key
    if not key:
        return []

    try:
        r = await client.get(
            f"https://booksrun.com/api/price/sell/{isbn}",
            params={"key": key},
            headers={"Accept": "application/json"},
            timeout=12,
        )
        if r.status_code != 200:
            return []

        data = r.json()
        result = data.get("result", {})
        if result.get("status") != "success":
            return []

        prices = result.get("text", {})
        # Use "Good" condition price as the primary buyback price
        cash = prices.get("Good") or prices.get("Average") or prices.get("New") or 0
        try:
            cash = float(cash)
        except (TypeError, ValueError):
            return []

        if cash <= 0:
            return []

        return [{
            "vendor": "BooksRun",
            "vendor_id": "booksrun",
            "cash": round(cash, 2),
            "credit": 0.0,
            "conditions": {
                "average": float(prices.get("Average", 0) or 0),
                "good": float(prices.get("Good", 0) or 0),
                "new": float(prices.get("New", 0) or 0),
            },
            "url": f"https://booksrun.com/textbooks-buyback/add-to-cart/{isbn}",
            "source": "booksrun_api",
        }]

    except Exception as e:
        logger.warning("BooksRun fetch error isbn=%s: %s", isbn, e)
        return []


# ── Main entry ─────────────────────────────────────────────────────────────────

async def fetch_buyback_prices(isbn: str, force: bool = False) -> Dict[str, Any]:
    """
    ISBN için tüm buyback fiyatlarını çek.

    Döner:
    {
      "ok": bool,
      "isbn": str,
      "offers": [{"vendor", "vendor_id", "cash", "credit", "url"}, ...],
      "best_cash": float | None,
      "best_vendor": str | None,
      "best_url": str | None,
      "sources": ["bookscouter"|"booksrun_api"],
      "cached": bool,
      "cache_age_s": int,
    }
    """
    from app.isbn_utils import to_isbn13
    isbn13 = to_isbn13(isbn) or isbn.replace("-", "").strip()

    if not force:
        cached = _cache_get(isbn13)
        if cached:
            age = int(time.time() - cached.get("ts", time.time()))
            return {**cached, "cached": True, "cache_age_s": age}

    s = get_settings()
    has_bookscouter = bool(s.bookscouter_api_key)
    has_booksrun    = bool(s.booksrun_api_key)
    has_valore      = bool(getattr(s, "valore_access_key", None))

    if not has_bookscouter and not has_booksrun and not has_valore:
        return {
            "ok": False,
            "isbn": isbn13,
            "offers": [],
            "best_cash": None,
            "best_vendor": None,
            "best_url": None,
            "sources": [],
            "no_keys": True,
            "hint": (
                "Buyback fiyatları için API anahtarı gerekli. "
                "BookScouter: api.bookscouter.com (ücretsiz kayıt) → BOOKSCOUTER_API_KEY. "
                "BooksRun: booksrun.com/page/api-reference (ücretsiz) → BOOKSRUN_API_KEY. "
                "ValoreBooks: APIsupport@valorebooks.com → VALORE_ACCESS_KEY + VALORE_SECRET_KEY."
            ),
            "cached": False,
            "cache_age_s": 0,
        }

    async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
        results = await asyncio.gather(
            _fetch_bookscouter(isbn13, client),
            _fetch_booksrun(isbn13, client),
            _fetch_valore(isbn13, client),
            return_exceptions=True,
        )

    all_offers: List[Dict] = []
    sources = []
    for res in results:
        if isinstance(res, list) and res:
            all_offers.extend(res)
            if res[0].get("source"):
                sources.append(res[0]["source"])

    # Deduplicate by vendor_id, keep highest cash
    best_per_vendor: Dict[str, Dict] = {}
    for o in all_offers:
        vid = o["vendor_id"]
        if vid not in best_per_vendor or o["cash"] > best_per_vendor[vid]["cash"]:
            best_per_vendor[vid] = o

    offers = sorted(best_per_vendor.values(), key=lambda x: x["cash"], reverse=True)

    if not offers:
        return {
            "ok": False,
            "isbn": isbn13,
            "offers": [],
            "best_cash": None,
            "best_vendor": None,
            "best_url": None,
            "sources": sources,
            "cached": False,
            "cache_age_s": 0,
        }

    best = offers[0]
    out = {
        "ok": True,
        "isbn": isbn13,
        "offers": offers,
        "best_cash": best["cash"],
        "best_vendor": best["vendor"],
        "best_url": best["url"],
        "sources": sources,
        "cached": False,
        "cache_age_s": 0,
    }
    _cache_set(isbn13, out)
    return out


def calc_buyback_profit(
    buy_price: float,
    buyback_cash: float,
    ship_cost: float = _SHIP_COST,
) -> Dict[str, float]:
    """
    Buyback kâr hesapla.
    Amazon FBA ücreti yok — sadece eBay alım + buyback'e nakliye.
    """
    total_cost = round(buy_price + ship_cost, 2)
    profit = round(buyback_cash - total_cost, 2)
    roi = round((profit / total_cost) * 100, 1) if total_cost > 0 else 0.0
    return {
        "buy_price": buy_price,
        "ship_to_buyback": ship_cost,
        "total_cost": total_cost,
        "buyback_cash": buyback_cash,
        "profit": profit,
        "roi_pct": roi,
    }


# ── BookScouter Historic Pricing ──────────────────────────────────────────────
# BookScouter'ın ücretsiz tier'ında "historic" fiyat endpoint'i var.
# Bu endpoint geçmiş fiyatları ve trend bilgisini döndürür.
# Kayıt: https://bookscouter.com/api  (ücretsiz plan)
# Endpoint: GET https://api.bookscouter.com/v1/book/{isbn}/prices/history

_hist_cache: Dict[str, tuple] = {}  # isbn → (ts, data)
_HIST_TTL = 3600 * 24  # 24 saat — tarihi veri daha yavaş değişir


async def get_buyback_price_trend(isbn: str) -> Dict[str, Any]:
    """
    BookScouter'dan buyback fiyat trendi çek.

    Returns:
        {
          "trend": "rising" | "falling" | "stable" | "unknown",
          "current_avg": float,     # son 30 gün ortalama
          "peak_30d": float,        # son 30 gün zirve
          "low_30d": float,         # son 30 gün dip
          "data_points": int,       # kaç veri noktası var
          "note": str               # trend açıklaması
        }
    """
    from app.isbn_utils import to_isbn13
    isbn13 = to_isbn13(isbn) or isbn

    # Cache
    if isbn13 in _hist_cache:
        ts, data = _hist_cache[isbn13]
        if time.time() - ts < _HIST_TTL:
            return data

    s = get_settings()
    key = s.bookscouter_api_key
    if not key:
        return {"trend": "unknown", "note": "BOOKSCOUTER_API_KEY yok"}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            # BookScouter history endpoint resmi dokümanda yok.
            # /v1/book/{isbn}/prices?type=sell mevcut current fiyatları döndürür.
            # Trend için tek anlık veri yeterli değil — "stable" döndür.
            r = await client.get(
                f"https://api.bookscouter.com/v1/book/{isbn13}/prices",
                params={"type": "sell"},
                headers={
                    "Authorization": f"Bearer {key}",
                    "Accept": "application/json",
                },
                timeout=10,
            )
            if r.status_code != 200:
                logger.debug("BookScouter history HTTP %d for isbn=%s", r.status_code, isbn13)
                return {"trend": "unknown", "note": f"HTTP {r.status_code}"}

            data = r.json()
            history = data.get("data") or data.get("history") or []
            if not history:
                return {"trend": "unknown", "note": "Veri yok"}

            # Son 30 günü filtrele
            now_ts = time.time()
            cutoff = now_ts - 30 * 86400
            prices_30d = []
            for entry in history:
                # entry: {date: "2024-01-15", price: 12.5} veya {timestamp: ..., price: ...}
                entry_ts = entry.get("timestamp") or 0
                if not entry_ts:
                    # date string varsa parse et
                    d_str = entry.get("date", "")
                    if d_str:
                        try:
                            import datetime
                            dt = datetime.datetime.strptime(d_str[:10], "%Y-%m-%d")
                            entry_ts = dt.timestamp()
                        except Exception:
                            pass
                price_val = entry.get("price") or entry.get("cash") or 0
                if entry_ts >= cutoff and price_val > 0:
                    prices_30d.append((entry_ts, float(price_val)))

            if not prices_30d:
                return {"trend": "unknown", "note": "Son 30 günde veri yok"}

            prices_30d.sort(key=lambda x: x[0])
            vals = [p for _, p in prices_30d]

            current_avg = round(sum(vals) / len(vals), 2)
            peak_30d    = round(max(vals), 2)
            low_30d     = round(min(vals), 2)

            # Trend: ilk yarı vs son yarı karşılaştır
            mid = len(vals) // 2
            first_half_avg = sum(vals[:mid]) / max(mid, 1)
            second_half_avg = sum(vals[mid:]) / max(len(vals) - mid, 1)
            diff_pct = ((second_half_avg - first_half_avg) / max(first_half_avg, 0.01)) * 100

            if diff_pct > 10:
                trend = "rising"
                note = f"Son 30 günde +{diff_pct:.0f}% artış — buyback fiyatı yükseliyor"
            elif diff_pct < -10:
                trend = "falling"
                note = f"Son 30 günde {diff_pct:.0f}% düşüş ⚠️ — buyback fiyatı eriyor"
            else:
                trend = "stable"
                note = f"Son 30 günde ±{abs(diff_pct):.0f}% — stabil fiyat"

            result = {
                "trend": trend,
                "current_avg": current_avg,
                "peak_30d": peak_30d,
                "low_30d": low_30d,
                "data_points": len(prices_30d),
                "note": note,
            }
            _hist_cache[isbn13] = (time.time(), result)
            return result

    except Exception as e:
        logger.debug("BookScouter history error isbn=%s: %s", isbn13, e)
        return {"trend": "unknown", "note": str(e)[:80]}


# ── ValoreBooks Sellback API ───────────────────────────────────────────────────
# Docs: https://valorebooks.github.io/api/sellback/price/
# Endpoint: GET https://api.valorebooks.com/sellback/price?isbn={isbn}
# Auth: AWS SigV4 — partner credentials gerekli
#   Kayıt: APIsupport@valorebooks.com → ücretsiz
#   Env: VALORE_ACCESS_KEY, VALORE_SECRET_KEY, VALORE_API_URL
#
# Not: API gateway URL partner başvurusu sonrası verilir.
# Bu aşamada web sayfasını scrape ederek fiyat çekiyoruz (public endpoint).

async def _fetch_valore(isbn: str, client: httpx.AsyncClient) -> List[Dict]:
    """
    ValoreBooks Sellback Price API.
    Docs: https://valorebooks.github.io/api/sellback/price/

    Endpoint: GET https://api.valorebooks.com/sellback/pricing/best/{isbn}
    Response: {"price": 75.75}
    Auth: AWS SigV4 signed — partner credentials gerekli (ücretsiz)
      Kayıt: APIsupport@valorebooks.com
      Env: VALORE_ACCESS_KEY, VALORE_SECRET_KEY, VALORE_API_URL
    Test: https://test.valorebooks.com/sellback/pricing/best/{isbn}
    """
    from app.isbn_utils import to_isbn13
    isbn13 = to_isbn13(isbn) or isbn
    s = get_settings()

    valore_key    = getattr(s, "valore_access_key", None)
    valore_secret = getattr(s, "valore_secret_key", None)
    valore_url    = getattr(s, "valore_api_url", "https://api.valorebooks.com")

    if not valore_key or not valore_secret:
        return []

    base_url = (valore_url or "https://api.valorebooks.com").rstrip("/")
    endpoint = f"{base_url}/sellback/pricing/best/{isbn13}"

    try:
        from botocore.auth import SigV4Auth
        from botocore.awsrequest import AWSRequest
        from botocore.credentials import Credentials
    except ImportError:
        logger.debug("ValoreBooks: botocore not installed — skipping")
        return []

    try:
        host    = base_url.replace("https://", "").replace("http://", "").split("/")[0]
        creds   = Credentials(valore_key, valore_secret)
        req_obj = AWSRequest(method="GET", url=endpoint, headers={"Host": host})
        SigV4Auth(creds, "execute-api", "us-east-1").add_auth(req_obj)

        r = await client.get(endpoint, headers=dict(req_obj.headers), timeout=10)
        if r.status_code == 200:
            price = float(r.json().get("price") or 0)
            if price > 0:
                logger.debug("ValoreBooks isbn=%s price=%.2f", isbn13, price)
                return [{
                    "vendor":    "ValoreBooks",
                    "vendor_id": "valorebooks",
                    "cash":      round(price, 2),
                    "credit":    0.0,
                    "url":       f"https://www.valore.com/sellback?isbn={isbn13}",
                    "source":    "valorebooks_api",
                }]
        elif r.status_code == 404:
            logger.debug("ValoreBooks: isbn=%s not in system", isbn13)
        else:
            logger.debug("ValoreBooks HTTP %d isbn=%s", r.status_code, isbn13)
    except Exception as e:
        logger.debug("ValoreBooks error isbn=%s: %s", isbn13, e)

    return []
