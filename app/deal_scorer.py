"""
AI Deal Scoring Engine — TrackerBundle3
========================================
Kitap arbitraj fırsatlarını 0-100 arası puanlar.

5 faktör + ceza sistemi:
  1. ROI Faktörü      (0-40 puan) — temel kârlılık
  2. Condition        (0-20 puan) — satış fiyatı güvencesi + iade riski
  3. Fiyat Güvencesi  (0-20 puan) — (limit - cost) / limit: ne kadar altından aldık
  4. Arz Faktörü      (0-10 puan) — eBay listing yoğunluğu (az = nadir = değerli)
  5. Veri Kalitesi    (0-10 puan) — Amazon buybox güveni

Cezalar:
  - ship_estimated  : -5  (kargo maliyeti belirsiz)
  - make_offer      : -3  (teklif reddedilebilir)
  - sell_src unknown: -5  (Amazon fiyatı güvenilmez)

Tier etiketleri:
  85-100 → 🔥 FIRE
  70-84  → ✨ EXCELLENT
  55-69  → 👍 GOOD
  40-54  → 🤔 FAIR
  0-39   → ❌ SKIP
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Optional


# ── Tier tanımı ───────────────────────────────────────────────────────────────

def score_to_tier(score: int) -> str:
    if score >= 85: return "🔥 FIRE"
    if score >= 70: return "✨ EXCELLENT"
    if score >= 55: return "👍 GOOD"
    if score >= 40: return "🤔 FAIR"
    return "❌ SKIP"


# ── Condition ağırlıkları ─────────────────────────────────────────────────────

_CONDITION_PTS = {
    "new":       20,
    "like_new":  18,
    "very_good": 14,
    "good":       9,
    "acceptable": 4,
}


# ── Sonuç dataclass ───────────────────────────────────────────────────────────

@dataclass
class ScoreBreakdown:
    roi_pts:        int = 0   # 0-40
    condition_pts:  int = 0   # 0-20
    margin_pts:     int = 0   # 0-20
    supply_pts:     int = 0   # 0-10
    data_pts:       int = 0   # 0-10
    penalty_pts:    int = 0   # 0 veya negatif

    @property
    def total(self) -> int:
        raw = (self.roi_pts + self.condition_pts + self.margin_pts
               + self.supply_pts + self.data_pts + self.penalty_pts)
        return max(0, min(100, raw))

    def to_dict(self) -> dict:
        d = asdict(self)
        d["total"] = self.total
        d["tier"] = score_to_tier(self.total)
        return d


# ── Ana fonksiyon ─────────────────────────────────────────────────────────────

def score_deal(
    roi_pct:        Optional[float] = None,   # profit_calc'dan
    condition:      Optional[str]   = None,   # "good", "very_good" ...
    ebay_total:     Optional[float] = None,   # eBay'de ödediğimiz tutar ($)
    max_limit:      Optional[float] = None,   # suggest_limit.max_buy ($)
    ebay_count:     Optional[int]   = None,   # kaç eBay listing bulundu
    amazon_data:    Optional[dict]  = None,   # get_top2_prices() raw çıktısı
    sell_source:    Optional[str]   = None,   # "used_buybox" | "new_buybox" | "unknown"
    viable:         bool            = False,
    ship_estimated: bool            = False,
    make_offer:     bool            = False,
) -> ScoreBreakdown:
    """
    Tüm parametreler opsiyonel — eksik veri nötr puan alır, sıfır vermez.
    Böylece kısmi data'da bile anlamlı skor üretilir.
    """
    bd = ScoreBreakdown()

    # ── 1. ROI Faktörü (0-40) ────────────────────────────────────────────────
    if roi_pct is not None:
        if roi_pct >= 60:   bd.roi_pts = 40
        elif roi_pct >= 50: bd.roi_pts = 36
        elif roi_pct >= 40: bd.roi_pts = 32
        elif roi_pct >= 30: bd.roi_pts = 26
        elif roi_pct >= 20: bd.roi_pts = 18
        elif roi_pct >= 15: bd.roi_pts = 12
        elif roi_pct >= 10: bd.roi_pts = 6
        elif roi_pct >= 0:  bd.roi_pts = 2
        else:               bd.roi_pts = 0
    else:
        bd.roi_pts = 0  # Amazon verisi yoksa ROI hesaplanamaz

    # ── 2. Condition Faktörü (0-20) ──────────────────────────────────────────
    cond_key = (condition or "").lower().replace(" ", "_").replace("-", "_")
    bd.condition_pts = _CONDITION_PTS.get(cond_key, 7)  # 7 = bilinmiyor → nötr

    # ── 3. Fiyat Güvencesi / Margin Faktörü (0-20) ──────────────────────────
    if max_limit and ebay_total and max_limit > 0 and ebay_total > 0:
        ratio = (max_limit - ebay_total) / max_limit
        if ratio >= 0.50:   bd.margin_pts = 20
        elif ratio >= 0.35: bd.margin_pts = 16
        elif ratio >= 0.25: bd.margin_pts = 12
        elif ratio >= 0.15: bd.margin_pts = 7
        elif ratio >= 0.05: bd.margin_pts = 3
        else:               bd.margin_pts = 0
    else:
        bd.margin_pts = 10  # veri yoksa nötr

    # ── 4. Arz Faktörü (0-10) ────────────────────────────────────────────────
    if ebay_count is not None:
        if ebay_count == 0:    bd.supply_pts = 0   # ürün yok
        elif ebay_count <= 3:  bd.supply_pts = 8   # nadir — değerli
        elif ebay_count <= 8:  bd.supply_pts = 10  # dengeli
        elif ebay_count <= 15: bd.supply_pts = 7   # orta yoğunluk
        else:                  bd.supply_pts = 3   # doymuş pazar
    else:
        bd.supply_pts = 5  # nötr

    # ── 5. Veri Kalitesi / Amazon Güven Faktörü (0-10) ───────────────────────
    if amazon_data:
        # Buybox'tan fiyat var mı?
        has_used_bb = bool(
            (amazon_data.get("used") or {}).get("buybox", {}).get("total")
        )
        has_new_bb = bool(
            (amazon_data.get("new") or {}).get("buybox", {}).get("total")
        )
        if viable and (has_used_bb or has_new_bb):
            bd.data_pts = 10
        elif has_used_bb or has_new_bb:
            bd.data_pts = 7
        else:
            bd.data_pts = 4  # Amazon verisi var ama buybox yok
    else:
        bd.data_pts = 0  # Amazon verisi hiç yok

    # ── Cezalar ──────────────────────────────────────────────────────────────
    penalty = 0
    if ship_estimated:
        penalty -= 5   # kargo tahmini = maliyet belirsiz
    if make_offer:
        penalty -= 3   # teklif reddedilebilir
    if sell_source == "unknown":
        penalty -= 5   # fiyat güvenilmez
    bd.penalty_pts = penalty

    return bd


# ── Yardımcı: bulk_discover result dict'inden skor hesapla ───────────────────

def score_from_discover_result(result: dict) -> ScoreBreakdown:
    """
    _scan_single_isbn() çıktısından doğrudan skor üret.
    bulk_discover.py içinde kullanılır.
    """
    ebay   = result.get("ebay") or {}
    deal   = result.get("best_deal") or {}
    sug    = result.get("suggestion") or {}
    amazon = result.get("_amazon_raw")  # raw amazon verisi (ayrıca saklanmalı)

    cheapest = ebay.get("cheapest") or []
    best_item = cheapest[0] if cheapest else {}

    return score_deal(
        roi_pct        = deal.get("roi_pct"),
        condition      = best_item.get("condition"),
        ebay_total     = deal.get("ebay_cost") or best_item.get("total"),
        max_limit      = sug.get("max_buy"),
        ebay_count     = ebay.get("total_found"),
        amazon_data    = amazon,
        sell_source    = deal.get("sell_source"),
        viable         = deal.get("viable", False),
        ship_estimated = best_item.get("ship_estimated", False),
        make_offer     = best_item.get("make_offer", False),
    )


# ── Yardımcı: reverse_lookup result dict'inden skor hesapla ──────────────────

def score_from_reverse_result(entry: dict) -> ScoreBreakdown:
    """
    reverse_lookup.py _process() içindeki entry dict'inden skor üret.
    """
    profit = entry.get("profit") or {}
    sug    = entry.get("suggestion") or {}

    return score_deal(
        roi_pct        = profit.get("roi_pct"),
        condition      = entry.get("ebay_condition"),
        ebay_total     = entry.get("ebay_total"),
        max_limit      = sug.get("max_buy"),
        ebay_count     = None,  # reverse_lookup tek item döndürür
        amazon_data    = entry.get("_amazon_raw"),
        sell_source    = profit.get("sell_source"),
        viable         = profit.get("viable", False),
        ship_estimated = False,
        make_offer     = entry.get("make_offer", False),
    )
