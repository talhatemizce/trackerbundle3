"""
Finding API tiered disk cache.

eBay Finding API rate limit'ini aşmak için period bazlı disk cache.
TTL katmanları:
  - 30d ve 100d period  → SHORT_TTL_HOURS  (varsayılan 24 saat — günlük kota)
  - 365d ve 1095d period → LONG_TTL_DAYS   (varsayılan 30 gün — aylık yenileme)

Cache anahtarı: isbn_clean + period_days + condition_filter
JSON formatı: {"ts": float, "totals": [float, ...]}

Thread/process güvenli: atomic write (tmp + os.replace).

Rate-limit fallback: get_stale() TTL'i yok sayarak cache'den okur.
Eğer eBay rate-limit verirse stale cache kullanılır, hiç cache yoksa boş liste döner.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("trackerbundle.finding_cache")

# ── Ayarlar ───────────────────────────────────────────────────────────────────
# 30d/100d: günde 1 kez yenile (24 saat) — rate-limit baskısını minimize eder
_SHORT_TTL = int(float(os.getenv("SGPRICE_SHORT_TTL_HOURS", "24")) * 3600)
# 365d/3yr: ayda 1 kez yenile (30 gün) — tarihsel data nadiren değişir
_LONG_TTL  = int(float(os.getenv("SGPRICE_LONG_TTL_DAYS",  "30")) * 86400)

# Cache dosyaları bu dizine yazılır
_CACHE_DIR = Path(os.getenv("FINDING_CACHE_DIR", "")) or (
    Path(__file__).resolve().parent / "data" / "finding_cache"
)


def _ttl_for(days_back: int) -> int:
    """Period uzunluğuna göre TTL saniye döndür."""
    return _LONG_TTL if days_back >= 365 else _SHORT_TTL


def _cache_path(isbn: str, days_back: int, condition: Optional[str]) -> Path:
    key = f"{isbn}:{days_back}:{condition or 'all'}"
    h = hashlib.sha1(key.encode()).hexdigest()[:16]
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _CACHE_DIR / f"{h}.json"


def get_cached(isbn: str, days_back: int, condition: Optional[str]) -> Optional[List[float]]:
    """Cache hit ise float listesi döndür, stale/yok ise None."""
    path = _cache_path(isbn, days_back, condition)
    try:
        if not path.exists():
            return None
        raw = path.read_text(encoding="utf-8")
        entry = json.loads(raw)
        age = time.time() - float(entry.get("ts", 0))
        ttl = _ttl_for(days_back)
        if age > ttl:
            logger.debug("Cache stale isbn=%s days=%d cond=%s age=%.0fs", isbn, days_back, condition, age)
            return None
        totals = entry.get("totals") or []
        logger.debug("Cache HIT isbn=%s days=%d cond=%s count=%d age=%.0fs", isbn, days_back, condition, len(totals), age)
        return totals
    except Exception:
        logger.debug("Cache read error isbn=%s days=%d", isbn, days_back)
        return None


def get_stale(isbn: str, days_back: int, condition: Optional[str]) -> Optional[List[float]]:
    """
    TTL'i yok sayarak cache'den okur.
    Rate-limit hatası geldiğinde stale data döndürmek için kullanılır.
    Cache dosyası yoksa None döner.
    """
    path = _cache_path(isbn, days_back, condition)
    try:
        if not path.exists():
            return None
        raw = path.read_text(encoding="utf-8")
        entry = json.loads(raw)
        totals = entry.get("totals") or []
        age = time.time() - float(entry.get("ts", 0))
        logger.debug(
            "Stale cache READ isbn=%s days=%d cond=%s count=%d age=%.0fs",
            isbn, days_back, condition, len(totals), age,
        )
        return totals
    except Exception:
        logger.debug("Stale cache read error isbn=%s days=%d", isbn, days_back)
        return None


def set_cached(isbn: str, days_back: int, condition: Optional[str], totals: List[float]) -> None:
    """Sonuçları cache'e yaz (atomic)."""
    path = _cache_path(isbn, days_back, condition)
    entry = {"ts": time.time(), "totals": totals, "isbn": isbn, "days": days_back, "condition": condition}
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
        logger.debug("Cache WRITE isbn=%s days=%d cond=%s count=%d", isbn, days_back, condition, len(totals))
    except Exception as e:
        logger.warning("Cache write error isbn=%s days=%d: %s", isbn, days_back, e)
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


def clear_isbn(isbn: str) -> int:
    """Bir ISBN'e ait tüm cache dosyalarını sil. Silinen sayı döner."""
    removed = 0
    try:
        for path in _CACHE_DIR.glob("*.json"):
            try:
                entry = json.loads(path.read_text(encoding="utf-8"))
                if entry.get("isbn") == isbn:
                    path.unlink(missing_ok=True)
                    removed += 1
            except Exception:
                pass
    except Exception:
        pass
    return removed


def cache_stats() -> dict:
    """Toplam cache dosya sayısı ve toplam boyut (bytes)."""
    try:
        files = list(_CACHE_DIR.glob("*.json"))
        total_bytes = sum(f.stat().st_size for f in files if f.exists())
        return {"files": len(files), "bytes": total_bytes}
    except Exception:
        return {"files": 0, "bytes": 0}
