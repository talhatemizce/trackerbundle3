"""
Analytics — TrackerBundle3
==========================
BSR → velocity, confidence scoring, EV hesabı, scenario simulator.
Patch review bulgularını (P0-P2) ve PM önerilerini (mevsimsellik, dinamik worst-case) içerir.
"""
from __future__ import annotations

import datetime as _dt
import math
from typing import Optional

# ── BSR → Velocity ─────────────────────────────────────────────────────────────

# Books kategorisi için deneysel katsayılar (Amazon US).
# BSR 1 = ~300 satış/gün, BSR 1M = ~0.01 satış/gün (logaritmik eğri).
_BSR_BREAKPOINTS: list[tuple[int, float]] = [
    (1_000,    300.0),
    (5_000,     60.0),
    (10_000,    25.0),
    (50_000,     8.0),
    (100_000,    3.0),
    (500_000,    0.5),
    (1_000_000,  0.1),
]


def bsr_to_velocity(bsr: Optional[int]) -> Optional[float]:
    """
    BSR → günlük tahmini satış (float).
    Dönüş değeri aylık bazda normalize edilmiş değil — günlük.
    None: BSR yok veya geçersiz.
    """
    if not bsr or bsr <= 0:
        return None
    for rank, daily in _BSR_BREAKPOINTS:
        if bsr <= rank:
            return round(daily, 2)
    return 0.05  # çok yüksek BSR


def bsr_to_days_to_sell(bsr: Optional[int]) -> Optional[int]:
    """BSR → stokun kaç günde satılacağı tahmini (tek adet)."""
    vel = bsr_to_velocity(bsr)
    if not vel or vel <= 0:
        return None
    return max(1, round(1.0 / vel))


# ── Mevsimsellik Çarpanları ────────────────────────────────────────────────────

_TEXTBOOK_SEASON_MULT: dict[int, float] = {
    1: 1.40,   # Ocak: bahar dönemi başlangıcı (talep zirvesi)
    2: 1.25,   # Şubat: geç kayıt
    3: 0.90,   # Mart
    4: 0.80,   # Nisan: dönem sonu
    5: 0.60,   # Mayıs: yaz tatili
    6: 0.55,   # Haziran: en düşük
    7: 0.70,   # Temmuz: erken alıcılar
    8: 1.35,   # Ağustos: güz dönemi (talep zirvesi)
    9: 1.25,   # Eylül: geç kayıt
    10: 0.85,  # Ekim
    11: 0.75,  # Kasım
    12: 0.80,  # Aralık: kış dönemi hazırlığı
}

_GENERAL_SEASON_MULT: dict[int, float] = {
    1: 0.85, 2: 0.85, 3: 0.90, 4: 0.95,
    5: 1.00, 6: 1.00, 7: 1.00, 8: 1.00,
    9: 1.05, 10: 1.10, 11: 1.15, 12: 1.20,  # Q4 tatil sezonu
}


def seasonal_velocity_mult(
    month: Optional[int] = None,
    is_textbook: bool = False,
) -> float:
    """
    Aylık mevsimsel velocity çarpanı.
    month=None → mevcut ay kullanılır.
    """
    m = month or _dt.date.today().month
    table = _TEXTBOOK_SEASON_MULT if is_textbook else _GENERAL_SEASON_MULT
    return table.get(m, 1.0)


# ── Confidence Score ───────────────────────────────────────────────────────────

def compute_confidence(
    sell_source: str,           # "used_buybox" | "new_buybox" | "used_top1" | "new_top1"
    spike_warning: bool,
    is_amazon_selling: bool,
    seller_count: Optional[int],
    seller_feedback_pct: Optional[float],
    bsr: Optional[int],
    roi_pct: float,
) -> int:
    """
    0-100 arası confidence skoru.
    Muhafazakar: eksik veri → puan verilmez.
    """
    score = 0

    # Buybox kaynağı (20 puan)
    if "buybox" in sell_source:
        score += 20
    elif "top1" in sell_source or "top2" in sell_source:
        score += 10

    # Spike yok (15 puan)
    if not spike_warning:
        score += 15

    # Amazon satıcı değil (15 puan)
    if not is_amazon_selling:
        score += 15

    # Satıcı sayısı (10 puan)
    if seller_count is not None:
        if seller_count <= 3:
            score += 10
        elif seller_count <= 8:
            score += 5

    # Seller feedback (7 + 5 puan)
    if seller_feedback_pct is not None:
        if seller_feedback_pct >= 98:
            score += 7
        elif seller_feedback_pct >= 95:
            score += 5
        elif seller_feedback_pct >= 90:
            score += 3

    # BSR (15 puan)
    if bsr is not None:
        if bsr <= 10_000:
            score += 15
        elif bsr <= 50_000:
            score += 10
        elif bsr <= 200_000:
            score += 5

    # ROI tier (18 puan)
    if roi_pct >= 30:
        score += 18
    elif roi_pct >= 15:
        score += 12
    elif roi_pct > 0:
        score += 5

    return min(score, 100)


def confidence_tier(score: int) -> str:
    if score >= 80: return "high"
    if score >= 60: return "medium"
    if score >= 40: return "low"
    return "uncertain"


# ── Expected Value ─────────────────────────────────────────────────────────────

def compute_ev(
    profit: float,
    velocity: Optional[float],   # günlük satış tahmini
    confidence_score: int,
) -> Optional[float]:
    """
    EV = profit × monthly_velocity_capped × (confidence/100)
    monthly_velocity capped at 30 (tek satıcı olarak tüm aylık satışı alamayız).
    """
    if velocity is None or velocity <= 0:
        return None
    monthly = min(velocity * 30, 30.0)
    ev = profit * monthly * (confidence_score / 100)
    return round(ev, 2)


# ── Scenario Simulator (v2 — dinamik worst-case) ──────────────────────────────

def _dynamic_worst_pct(velocity: Optional[float], bsr: Optional[int]) -> float:
    """
    Worst-case kırpma yüzdesi (0.0–1.0).
    Hızlı satanlar → düşük risk → küçük kırpma.
    Veri yok → maksimum ceza.
    """
    if not velocity or velocity <= 0:
        return 0.45
    if not bsr or bsr > 1_000_000:
        return 0.45
    if velocity >= 10.0: return 0.15
    if velocity >= 5.0:  return 0.20
    if velocity >= 1.0:  return 0.25
    if velocity >= 0.5:  return 0.35
    return 0.40


def compute_scenarios(
    buy_price: float,
    current_sell: Optional[float],
    avg_sell: Optional[float],
    total_fees: float,
    velocity: Optional[float] = None,
    bsr: Optional[int] = None,
) -> dict:
    """
    3 senaryo: best / base / worst.
    best:  anlık buybox fiyatı
    base:  tarihsel ortalama (avg_sell) veya current_sell × 0.85
    worst: base × (1 - dynamic_pct)  — v2: velocity/BSR'a göre dinamik
    """
    if not current_sell or current_sell <= 0:
        return {}

    best  = round(current_sell, 2)
    base  = round(avg_sell, 2) if (avg_sell and avg_sell > 0) else round(current_sell * 0.85, 2)
    worst_pct = _dynamic_worst_pct(velocity, bsr)
    worst = round(base * (1.0 - worst_pct), 2)

    def _profit(sell: float) -> float:
        return round(sell - total_fees - buy_price, 2)

    def _roi(sell: float) -> float:
        p = _profit(sell)
        return round(p / buy_price * 100, 1) if buy_price > 0 else 0.0

    return {
        "best_case_sell":    best,
        "best_case_profit":  _profit(best),
        "best_case_roi":     _roi(best),
        "base_case_sell":    base,
        "base_case_profit":  _profit(base),
        "base_case_roi":     _roi(base),
        "worst_case_sell":   worst,
        "worst_case_profit": _profit(worst),
        "worst_case_roi":    _roi(worst),
        "worst_cut_pct":     round(worst_pct * 100, 1),
    }
