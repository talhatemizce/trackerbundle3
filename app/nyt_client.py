"""
New York Times Books API client.
=================================
Ücretsiz: 4.000 req/gün, 10 req/dak, 6 saniye arası bekleme önerilir.
Kayıt: https://developer.nytimes.com (ücretsiz)
Env: NYT_API_KEY

İki kullanım alanı:
  1. ISBN'in NYT listesindeki geçmişi — talep sinyali
     GET /v3/lists/best-sellers/history.json?isbn={isbn}
     → weeks_on_list, highest_rank, list_name

  2. Güncel bestseller listeleri — watchlist discovery
     GET /v3/lists/current/{list_name}.json
     → ISBNs, rank, weeks_on_list, amazon_product_url

Her iki endpoint da arbitraj için kritik:
  - NYT'de 10+ hafta kalan kitap → talep kanıtlanmış → buyback+resale değeri yüksek
  - Yeni çıkan bestseller → henüz düşük eBay stoku → arbitraj fırsatı penceresi kısa
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import get_settings

logger = logging.getLogger("trackerbundle.nyt")

NYT_BASE = "https://api.nytimes.com/svc/books/v3"

# ── Cache ──────────────────────────────────────────────────────────────────────
_isbn_cache: Dict[str, tuple] = {}   # isbn → (ts, data)
_list_cache: Dict[str, tuple] = {}   # list_name → (ts, data)
_ISBN_TTL  = 3600 * 24 * 7  # 7 gün — NYT listesi geçmişi nadiren değişir
_LIST_TTL  = 3600 * 4       # 4 saat — güncel liste haftalık güncellenir

# NYT Books kategorisi ↔ bizim sistemdeki kategori eşleşmesi
# Bu liste arbitraj için en değerli NYT kategorileri
VALUABLE_LISTS = [
    "hardcover-nonfiction",
    "paperback-nonfiction",
    "hardcover-fiction",
    "trade-fiction-paperback",
    "young-adult-hardcover",
    "science",
    "health",
    "business-books",
    "education",
]


async def get_isbn_nyt_history(isbn: str) -> Dict[str, Any]:
    """
    ISBN'in NYT bestseller geçmişini çek.

    Returns:
        {
          "was_bestseller": bool,
          "highest_rank": int | None,       # 1 = #1 liste
          "total_weeks": int,               # toplam liste haftası
          "lists": [{"list_name", "weeks_on_list", "bestsellers_date"}],
          "note": str,                      # AI prompt için özet
        }
    """
    s = get_settings()
    key = s.nyt_api_key
    if not key:
        return {"was_bestseller": False, "note": "NYT_API_KEY yok"}

    from app.isbn_utils import to_isbn13
    isbn13 = to_isbn13(isbn) or isbn

    if isbn13 in _isbn_cache:
        ts, data = _isbn_cache[isbn13]
        if time.time() - ts < _ISBN_TTL:
            return data

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{NYT_BASE}/lists/best-sellers/history.json",
                params={"isbn": isbn13, "api-key": key},
            )
            if r.status_code == 429:
                logger.warning("NYT API rate limit — isbn=%s", isbn13)
                return {"was_bestseller": False, "note": "NYT rate limit"}
            if r.status_code == 401:
                logger.warning("NYT API key invalid")
                return {"was_bestseller": False, "note": "NYT key invalid"}
            if r.status_code != 200:
                logger.debug("NYT API HTTP %d isbn=%s", r.status_code, isbn13)
                return {"was_bestseller": False, "note": f"NYT HTTP {r.status_code}"}

            results = r.json().get("results") or []
            if not results:
                data = {"was_bestseller": False, "total_weeks": 0, "lists": [], "note": "NYT listesinde hiç yer almadı"}
                _isbn_cache[isbn13] = (time.time(), data)
                return data

            # Her kitap için ranks listesi
            all_lists = []
            total_weeks = 0
            best_rank = 999

            for book in results:
                for rank_entry in (book.get("ranks_history") or []):
                    wks = int(rank_entry.get("weeks_on_list") or 0)
                    rk  = int(rank_entry.get("rank") or 999)
                    total_weeks += wks
                    if rk < best_rank:
                        best_rank = rk
                    all_lists.append({
                        "list_name": rank_entry.get("list_name", ""),
                        "weeks_on_list": wks,
                        "bestsellers_date": rank_entry.get("bestsellers_date", ""),
                        "rank": rk,
                    })

            if not all_lists:
                data = {"was_bestseller": False, "total_weeks": 0, "lists": [], "note": "NYT listesinde hiç yer almadı"}
            else:
                top_list = sorted(all_lists, key=lambda x: x["rank"])[0]
                note = (
                    f"NYT Bestseller: #{best_rank} rank, {total_weeks} hafta "
                    f"({top_list['list_name']})"
                )
                data = {
                    "was_bestseller": True,
                    "highest_rank": best_rank,
                    "total_weeks": total_weeks,
                    "lists": all_lists[:5],  # ilk 5
                    "note": note,
                }

            _isbn_cache[isbn13] = (time.time(), data)
            return data

    except Exception as e:
        logger.debug("NYT isbn history error isbn=%s: %s", isbn13, e)
        return {"was_bestseller": False, "note": str(e)[:80]}


async def get_current_bestsellers(list_name: str = "hardcover-nonfiction") -> List[Dict]:
    """
    Güncel NYT bestseller listesini çek.
    Watchlist discovery için kullanılır.

    Returns:
        [{"isbn13", "isbn10", "title", "author", "rank",
          "weeks_on_list", "amazon_url", "publisher"}, ...]
    """
    s = get_settings()
    key = s.nyt_api_key
    if not key:
        return []

    if list_name in _list_cache:
        ts, data = _list_cache[list_name]
        if time.time() - ts < _LIST_TTL:
            return data

    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.get(
                f"{NYT_BASE}/lists/current/{list_name}.json",
                params={"api-key": key},
            )
            if r.status_code != 200:
                logger.debug("NYT list HTTP %d list=%s", r.status_code, list_name)
                return []

            books_raw = (r.json().get("results") or {}).get("books") or []
            books = []
            for b in books_raw:
                # ISBN'leri al
                isbns = b.get("isbns") or []
                isbn13 = ""
                isbn10 = ""
                for entry in isbns:
                    if entry.get("isbn13"):
                        isbn13 = entry["isbn13"]
                    if entry.get("isbn10"):
                        isbn10 = entry["isbn10"]
                # primary_isbn fallback
                if not isbn13:
                    isbn13 = b.get("primary_isbn13", "")
                if not isbn10:
                    isbn10 = b.get("primary_isbn10", "")

                if not isbn13 and not isbn10:
                    continue

                books.append({
                    "isbn13":       isbn13,
                    "isbn10":       isbn10,
                    "title":        b.get("title", ""),
                    "author":       b.get("author", ""),
                    "rank":         b.get("rank", 0),
                    "rank_last_week": b.get("rank_last_week", 0),
                    "weeks_on_list": b.get("weeks_on_list", 0),
                    "publisher":    b.get("publisher", ""),
                    "description":  b.get("description", ""),
                    "amazon_url":   b.get("amazon_product_url", ""),
                    "list_name":    list_name,
                })

            _list_cache[list_name] = (time.time(), books)
            logger.info("NYT %s: %d books fetched", list_name, len(books))
            return books

    except Exception as e:
        logger.debug("NYT list error list=%s: %s", list_name, e)
        return []


async def get_watchlist_suggestions(max_per_list: int = 5) -> List[Dict]:
    """
    Tüm değerli NYT listelerinden watchlist önerileri topla.
    Tekrar eden ISBN'leri deduplicate eder.
    """
    s = get_settings()
    if not s.nyt_api_key:
        return []

    all_books: Dict[str, Dict] = {}
    for list_name in VALUABLE_LISTS[:4]:  # günde 4 liste = 4 istek
        try:
            books = await get_current_bestsellers(list_name)
            for b in books[:max_per_list]:
                key = b.get("isbn13") or b.get("isbn10", "")
                if key and key not in all_books:
                    all_books[key] = b
            await asyncio.sleep(0.7)  # NYT rate limit: 10/dak = 6s arası
        except Exception as e:
            logger.debug("NYT list %s error: %s", list_name, e)

    return list(all_books.values())
