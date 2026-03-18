"""
Hardcover.app API client — Goodreads'in halefi.
=====================================================
Ücretsiz GraphQL API, ISBN bazlı kitap verisi.
Kayıt: hardcover.app → Settings → API → token kopyala
Env:   HARDCOVER_API_KEY
Endpoint: POST https://api.hardcover.app/v1/graphql

Sistemimize katkısı:
  users_read_count    → talep kanıtı (BSR'sız velocity proxy)
  users_reading_count → şu anki aktif talep
  ratings_count       → göreli popülerlik
  rating              → kalite sinyali
  cached_tags         → genre/kategori (textbook detection backup)

Özellikle Trade kitaplarda (roman, kişisel gelişim) BSR bazen
SP-API'den gelmiyor. Hardcover bu boşluğu dolduruyor.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

import httpx

from app.core.config import get_settings

logger = logging.getLogger("trackerbundle.hardcover")

HARDCOVER_GQL = "https://api.hardcover.app/v1/graphql"

_cache: Dict[str, tuple] = {}
_TTL = 3600 * 24 * 3  # 3 gün — okuyucu sayısı nadiren değişir

# ISBN'den kitap verisi çeken GraphQL sorgusu
_QUERY_BY_ISBN = """
query BookByISBN($isbn: String!) {
  books(where: {editions: {isbn_13: {_eq: $isbn}}}, limit: 1) {
    id
    title
    users_read_count
    users_reading_count
    users_count
    ratings_count
    rating
    release_year
    pages
    cached_tags
    contributions(limit: 3) {
      author {
        name
      }
    }
  }
}
"""


async def get_book_demand(isbn: str) -> Dict[str, Any]:
    """
    ISBN için Hardcover demand sinyallerini çek.

    Returns:
        {
          "users_read":    int,   # kaç kişi okudu
          "users_reading": int,   # şu an kaç kişi okuyor
          "ratings_count": int,   # değerlendirme sayısı
          "rating":        float, # ortalama puan (0-5)
          "demand_tier":   str,   # "high"|"medium"|"low"|"unknown"
          "note":          str,   # AI prompt için özet
          "tags":          list,  # genre etiketleri
        }
    """
    s = get_settings()
    key = s.hardcover_api_key
    if not key:
        return {"demand_tier": "unknown", "note": "HARDCOVER_API_KEY yok"}

    from app.isbn_utils import to_isbn13
    isbn13 = to_isbn13(isbn) or isbn

    if isbn13 in _cache:
        ts, data = _cache[isbn13]
        if time.time() - ts < _TTL:
            return data

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                HARDCOVER_GQL,
                json={"query": _QUERY_BY_ISBN, "variables": {"isbn": isbn13}},
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
            )

            if r.status_code == 429:
                logger.warning("Hardcover rate limit isbn=%s", isbn13)
                return {"demand_tier": "unknown", "note": "Hardcover rate limit"}
            if r.status_code != 200:
                logger.debug("Hardcover HTTP %d isbn=%s", r.status_code, isbn13)
                return {"demand_tier": "unknown", "note": f"HTTP {r.status_code}"}

            gql_data = r.json()
            books = (gql_data.get("data") or {}).get("books") or []

            if not books:
                data = {"demand_tier": "unknown", "note": "Hardcover'da bulunamadı", "tags": []}
                _cache[isbn13] = (time.time(), data)
                return data

            b = books[0]
            read_count    = int(b.get("users_read_count")    or 0)
            reading_count = int(b.get("users_reading_count") or 0)
            ratings_count = int(b.get("ratings_count")       or 0)
            rating        = float(b.get("rating")            or 0)

            # Demand tier belirleme
            if read_count >= 5000 or ratings_count >= 1000:
                tier = "high"
            elif read_count >= 500 or ratings_count >= 100:
                tier = "medium"
            elif read_count >= 50 or ratings_count >= 10:
                tier = "low"
            else:
                tier = "niche"

            # Tag listesi — textbook detection için
            raw_tags = b.get("cached_tags") or {}
            tags = []
            if isinstance(raw_tags, dict):
                for cat_list in raw_tags.values():
                    if isinstance(cat_list, list):
                        tags.extend([t.get("tag", {}).get("tag", "") for t in cat_list if isinstance(t, dict)])
            tags = [t for t in tags if t][:8]

            # Not oluştur
            parts = []
            if read_count > 0:
                parts.append(f"{read_count:,} kişi okudu")
            if reading_count > 0:
                parts.append(f"{reading_count:,} şu an okuyor")
            if ratings_count > 0:
                parts.append(f"{ratings_count:,} değerlendirme ({rating:.1f}/5)")
            note = f"Hardcover: {', '.join(parts)}" if parts else "Hardcover: veri yok"

            data = {
                "users_read":    read_count,
                "users_reading": reading_count,
                "ratings_count": ratings_count,
                "rating":        round(rating, 2),
                "demand_tier":   tier,
                "note":          note,
                "tags":          tags,
                "hardcover_id":  b.get("id"),
            }
            _cache[isbn13] = (time.time(), data)
            logger.debug("Hardcover isbn=%s read=%d tier=%s", isbn13, read_count, tier)
            return data

    except Exception as e:
        logger.debug("Hardcover error isbn=%s: %s", isbn13, e)
        return {"demand_tier": "unknown", "note": str(e)[:80], "tags": []}
