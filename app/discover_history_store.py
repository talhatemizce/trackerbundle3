"""
Discover Scan History Store
============================
Her POST /discover/bulk veya /discover/reverse sonrası tarama sonucu disk'e kaydedilir.
Panel yeniden açıldığında GET /discover/history ile okunur.

Şema (her entry):
  {
    "id":          "...",        # uuid4 kısa
    "ts":          1234567890,   # unix timestamp
    "type":        "bulk"|"reverse",
    "isbns":       [...],        # taranan ISBN'ler
    "total":       N,
    "scanned":     N,
    "duration_s":  float,
    "best_score":  int,
    "viable_count": int,         # profit > 0 olan deal sayısı
    "top_deals":   [...],        # en iyi 5 deal (özet)
    "results":     [...],        # tam sonuç (gzip ile saklanabilir)
  }

Disk: data/discover_history.json
Max: 50 tarama (en yeniler önce)
"""
from __future__ import annotations

import time
import uuid
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.core.config import get_settings
from app.core.json_store import file_lock, _read_unsafe, _write_unsafe

logger = logging.getLogger("trackerbundle.discover_history")

MAX_ENTRIES = 50


def _path() -> Path:
    p = get_settings().resolved_data_dir() / "discover_history.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _short_id() -> str:
    return uuid.uuid4().hex[:10]


def _extract_top_deals(results: List[dict], n: int = 5) -> List[dict]:
    """Tüm sonuçlardan en iyi n deal'ı çıkar (özet format)."""
    all_deals = []
    for r in results:
        for d in (r.get("deals") or []):
            if d.get("viable") and d.get("roi_pct") is not None:
                all_deals.append({
                    "isbn":      r.get("isbn", ""),
                    "source":    d.get("source", ""),
                    "buy_price": d.get("buy_price"),
                    "roi_pct":   d.get("roi_pct"),
                    "profit":    d.get("profit"),
                    "url":       d.get("url", ""),
                })
        # Eski format (deals dizisi yoksa best_deal kullan)
        if not r.get("deals") and r.get("best_deal", {}).get("viable"):
            bd = r["best_deal"]
            all_deals.append({
                "isbn":      r.get("isbn", ""),
                "source":    "eBay Used",
                "buy_price": bd.get("ebay_cost"),
                "roi_pct":   bd.get("roi_pct"),
                "profit":    bd.get("profit"),
                "url":       "",
            })

    all_deals.sort(key=lambda x: x.get("roi_pct", -999), reverse=True)
    return all_deals[:n]


def save_scan(
    scan_type: str,           # "bulk" | "reverse"
    isbns: List[str],
    result: Dict[str, Any],
) -> str:
    """Tarama sonucunu kaydet. scan_id döner."""
    results = result.get("results") or []
    top_deals = _extract_top_deals(results)

    # Viable deal sayısı
    viable_count = 0
    for r in results:
        for d in (r.get("deals") or []):
            if d.get("viable"): viable_count += 1
        if not r.get("deals") and (r.get("best_deal") or {}).get("viable"):
            viable_count += 1

    best_score = max((r.get("score", 0) for r in results), default=0)

    entry = {
        "id":           _short_id(),
        "ts":           int(time.time()),
        "type":         scan_type,
        "isbns":        isbns[:200],
        "total":        result.get("total", len(isbns)),
        "scanned":      result.get("scanned", 0),
        "duration_s":   result.get("duration_s", 0),
        "best_score":   best_score,
        "viable_count": viable_count,
        "top_deals":    top_deals,
        "results":      results,   # tam veri
    }

    p = _path()
    with file_lock(p):
        data = _read_unsafe(p, default={"entries": []})
        entries: list = data.get("entries") or []
        entries.insert(0, entry)          # en yeni başa
        entries = entries[:MAX_ENTRIES]   # kap
        _write_unsafe(p, {"entries": entries})

    logger.info("discover_history saved id=%s type=%s isbns=%d viable=%d",
                entry["id"], scan_type, len(isbns), viable_count)
    return entry["id"]


def get_history(limit: int = 50) -> List[Dict[str, Any]]:
    """Kayıtlı taramaları döndür (results dahil)."""
    data = _read_unsafe(_path(), default={"entries": []})
    entries = data.get("entries") or []
    return entries[:limit]


def get_scan(scan_id: str) -> Optional[Dict[str, Any]]:
    """Belirli bir tarama ID'sini getir."""
    data = _read_unsafe(_path(), default={"entries": []})
    for e in (data.get("entries") or []):
        if e.get("id") == scan_id:
            return e
    return None


def delete_scan(scan_id: str) -> bool:
    """Belirli bir taramayı sil. True → silindi."""
    p = _path()
    with file_lock(p):
        data = _read_unsafe(p, default={"entries": []})
        entries = data.get("entries") or []
        before = len(entries)
        entries = [e for e in entries if e.get("id") != scan_id]
        if len(entries) == before:
            return False
        _write_unsafe(p, {"entries": entries})
    return True


def clear_all() -> int:
    """Tüm geçmişi temizle. Silinen kayıt sayısı döner."""
    p = _path()
    with file_lock(p):
        data = _read_unsafe(p, default={"entries": []})
        count = len(data.get("entries") or [])
        _write_unsafe(p, {"entries": []})
    return count
