"""
Sold stats accumulator — 365d/3yr geçmişi oluşturmak için günlük snapshot biriktirir.

Problem:
  eBay Finding API maksimum 90 günlük veri döndürür. 365d/3yr penceresi için
  doğrudan sorgu yapılamaz. Bu modül her başarılı 30d/90d Finding API çağrısından
  sonra fiyatları diske yazar; zaman içinde biriken snapshotlardan uzun dönem
  istatistikleri hesaplanır.

Storage:
  data/sold_stats/{sha1(isbn_clean)[:16]}.json
  {
    "isbn": str,
    "entries": [
      {
        "ts":     float,         # Snapshot alım zamanı (Unix)
        "days":   int,           # Sorgu penceresi (30 veya 90)
        "cond":   str | null,    # "new" | "used" | null (hepsi)
        "totals": [float, ...]   # price + shipping (her satış için)
      },
      ...
    ]
  }

Kısıtlar:
  - Aynı isbn+days+cond için 6 saatten sık snapshot atlanır (throttle).
  - 400 günden eski entry'ler otomatik silinir.
  - Farklı günlerde alınan 30d snapshotları örtüşebilir (aynı satış birden
    fazla snapshot'ta gözükebilir). Bu, uzun pencere ortalamalarında hafif
    üst-bias yaratır. Dedup için itemId eklenmesi ileride yapılabilir.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("trackerbundle.sold_stats_store")

# ── Sabitler ─────────────────────────────────────────────────────────────────
_MAX_AGE_DAYS = int(os.getenv("SOLD_STATS_MAX_AGE_DAYS", "400"))
_THROTTLE_SECONDS = int(os.getenv("SOLD_STATS_THROTTLE_H", "6")) * 3600


# ── Dizin ─────────────────────────────────────────────────────────────────────
def _store_dir() -> Path:
    override = os.getenv("SOLD_STATS_DIR", "").strip()
    if override:
        p = Path(override)
    else:
        try:
            from app.core.config import get_settings
            p = get_settings().resolved_data_dir() / "sold_stats"
        except Exception:
            p = Path(__file__).resolve().parent.parent / "data" / "sold_stats"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _isbn_path(isbn_clean: str) -> Path:
    h = hashlib.sha1(isbn_clean.encode()).hexdigest()[:16]
    return _store_dir() / f"{h}.json"


# ── I/O ───────────────────────────────────────────────────────────────────────
def _load(isbn_clean: str) -> Dict:
    p = _isbn_path(isbn_clean)
    if not p.exists():
        return {"isbn": isbn_clean, "entries": []}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"isbn": isbn_clean, "entries": []}


def _save(isbn_clean: str, data: Dict) -> None:
    p = _isbn_path(isbn_clean)
    tmp = p.with_suffix(".tmp")
    try:
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(tmp, p)
    except Exception as e:
        logger.warning("sold_stats write error isbn=%s: %s", isbn_clean, e)
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


# ── Public API ────────────────────────────────────────────────────────────────

def append_snapshot(
    isbn: str,
    days: int,
    cond: Optional[str],
    totals: List[float],
) -> bool:
    """
    Bir Finding API yanıtından gelen fiyatları sakla.

    Returns:
      True  → snapshot eklendi
      False → atlandı (totals boş, ya da throttle: aynı pencere için çok erken)
    """
    if not totals:
        return False

    isbn_clean = isbn.replace("-", "").replace(" ", "").strip()
    data = _load(isbn_clean)
    entries: List[Dict] = data.get("entries") or []
    now = time.time()

    # Throttle: aynı isbn+days+cond için çok erken
    for e in reversed(entries):
        if e.get("days") == days and e.get("cond") == cond:
            if now - float(e.get("ts", 0)) < _THROTTLE_SECONDS:
                logger.debug(
                    "isbn=%s days=%d cond=%s snapshot throttled", isbn_clean, days, cond
                )
                return False
            break

    entries.append({"ts": round(now, 1), "days": days, "cond": cond, "totals": totals})

    # Eski entry'leri sil
    cutoff = now - (_MAX_AGE_DAYS * 86400)
    entries = [e for e in entries if float(e.get("ts", 0)) >= cutoff]

    data["isbn"] = isbn_clean
    data["entries"] = entries
    _save(isbn_clean, data)
    logger.debug(
        "isbn=%s days=%d cond=%s: stored %d prices, total entries=%d",
        isbn_clean, days, cond, len(totals), len(entries),
    )
    return True


def query_window(
    isbn: str,
    window_days: int,
    cond: Optional[str],
) -> List[float]:
    """
    Son `window_days` gün içinde biriktirilen tüm satış fiyatlarını döndürür.

    Not: Ardışık snapshotlar örtüşen veri içerebilir (aynı satış birden fazla
    snapshot'ta gözükebilir). Bu, istatistiklere hafif üst-bias katar ancak
    alım kararı için pratik açıdan ihmal edilebilir düzeydedir.
    """
    isbn_clean = isbn.replace("-", "").replace(" ", "").strip()
    data = _load(isbn_clean)
    entries: List[Dict] = data.get("entries") or []

    cutoff = time.time() - (window_days * 86400)
    result: List[float] = []

    for e in entries:
        if float(e.get("ts", 0)) < cutoff:
            continue
        if cond is not None and e.get("cond") != cond:
            continue
        result.extend(e.get("totals") or [])

    return result


def snapshot_span_days(isbn: str, cond: Optional[str]) -> Optional[float]:
    """
    Bu ISBN için en eski ve en yeni snapshot arasındaki gün sayısı.
    İlk snapshot'tan beri geçen süreyi gösterir.
    None → henüz snapshot yok.
    """
    isbn_clean = isbn.replace("-", "").replace(" ", "").strip()
    data = _load(isbn_clean)
    entries = [
        e for e in (data.get("entries") or [])
        if cond is None or e.get("cond") == cond
    ]
    if not entries:
        return None
    ts_values = [float(e.get("ts", 0)) for e in entries]
    span = max(ts_values) - min(ts_values)
    return round(span / 86400, 1)


def entry_summary(isbn: str) -> Dict:
    """Debug / panel için: kaç entry var, kaç günlük span."""
    isbn_clean = isbn.replace("-", "").replace(" ", "").strip()
    data = _load(isbn_clean)
    entries = data.get("entries") or []
    by_window: Dict[str, int] = {}
    for e in entries:
        key = f"{e.get('days')}d_{e.get('cond') or 'all'}"
        by_window[key] = by_window.get(key, 0) + 1
    return {
        "isbn": isbn_clean,
        "total_entries": len(entries),
        "by_window": by_window,
        "span_days": snapshot_span_days(isbn_clean, None),
    }


def _safe_avg(vals: List[float]) -> Optional[float]:
    return round(sum(vals) / len(vals), 2) if vals else None


def trend_direction(
    avg_short: Optional[float],
    avg_long: Optional[float],
    threshold: float = 0.15,
) -> str:
    """
    avg_short vs avg_long karşılaştırması.
    threshold=0.15 → %15'ten fazla sapma → UP/DOWN trend
    """
    if avg_short is None or avg_long is None or avg_long == 0:
        return "UNKNOWN"
    ratio = (avg_short - avg_long) / avg_long
    if ratio > threshold:
        return "UPTREND"    # Son dönem daha pahalı (fiyat artıyor)
    if ratio < -threshold:
        return "DOWNTREND"  # Son dönem daha ucuz (fiyat düşüyor)
    return "STABLE"


def compute_trends(
    avg_30: Optional[float],
    avg_90: Optional[float],
    avg_365: Optional[float],
) -> Dict:
    """
    3 zaman penceresi arasındaki trend yönlerini ve TrendShift bayrağını döndürür.
    TrendShift: |avg_30 - avg_365| / avg_365 > 0.40
    """
    trend_30_90 = trend_direction(avg_30, avg_90, threshold=0.15)
    trend_30_365 = trend_direction(avg_30, avg_365, threshold=0.15)
    trend_90_365 = trend_direction(avg_90, avg_365, threshold=0.15)

    trendshift = False
    if avg_30 is not None and avg_365 is not None and avg_365 > 0:
        trendshift = abs(avg_30 - avg_365) / avg_365 > 0.40

    return {
        "trend_30_vs_90": trend_30_90,
        "trend_30_vs_365": trend_30_365,
        "trend_90_vs_365": trend_90_365,
        "trendshift": trendshift,
    }
