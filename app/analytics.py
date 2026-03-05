"""
TrackerBundle3 — Analitik Katman
==================================
BSR → Sales Velocity, Confidence Score, EV, Scenario Simulator.

Can & Ali PM/Dev tartışmasından türetilen P3 özellik seti.
"""
from __future__ import annotations

import math
from typing import Optional


# ── BSR → Aylık Satış Hızı ────────────────────────────────────────────────────
# Amazon Books US için ampirik eğri (Jungle Scout / Keepa 2023-24 kalibrasyonu)
# Tier tablo: (BSR_eşik, aylık_satış_adet)
_BSR_TIERS = [
    (1_000,      200.0),
    (5_000,       80.0),
    (20_000,      30.0),
    (50_000,      12.0),
    (100_000,      6.0),
    (250_000,      2.5),
    (500_000,      1.2),
    (1_000_000,    0.5),
    (float("inf"), 0.15),
]


def bsr_to_velocity(bsr: Optional[int]) -> Optional[float]:
    """BSR → tahmini aylık satış adet. BSR None/0 → None."""
    if not bsr or bsr <= 0:
        return None
    for threshold, vel in _BSR_TIERS:
        if bsr < threshold:
            return vel
    return 0.15


def bsr_to_days_to_sell(bsr: Optional[int]) -> Optional[int]:
    """
    BSR → tek birim için beklenen satış süresi (gün).
    max 730 (2 yıl) ile sınırlı.
    """
    vel = bsr_to_velocity(bsr)
    if not vel:
        return None
    days = math.ceil(30.0 / vel)
    return min(days, 730)


# ── Confidence Score (0–100) ─────────────────────────────────────────────────
# Veri kalitesi, fiyat kararlılığı ve kaynak güvenilirliğini ölçer.
# ROI'den bağımsız: yüksek ROI + düşük confidence = riskli fırsat.

def compute_confidence(result: dict) -> int:
    """
    ArbResult dict'inden 0-100 güven skoru üretir.

    Bileşen dağılımı (toplam 100):
      20  buybox kalitesi (buybox > top1/2 fallback)
      15  sub-condition bilgisi (specific > generic)
      15  spike yok
      15  Amazon self-seller değil
      10  cross-condition fallback yok
      10  rakip sayısı az
       7  seller feedback % yüksek
       5  seller feedback hacmi yeterli (scam önleme)
       3  BSR mevcut (likidite sinyali var)
    """
    score = 0

    # Buybox kalitesi
    sell_src = (result.get("sell_source") or "").lower()
    if "buybox" in sell_src:
        score += 20
    elif "top" in sell_src:
        score += 8  # buybox suppressed ama fiyat var

    # Sub-condition netliği
    sub = (result.get("ebay_sub_condition") or "").lower()
    if sub in ("brand_new", "like_new", "very_good", "good", "acceptable"):
        score += 15
    elif sub == "used_all":
        score += 6  # genel "used" — kısmi bilgi

    # Spike yok
    if not result.get("spike_warning", False):
        score += 15

    # Amazon self-seller değil
    if not result.get("is_amazon_selling", False):
        score += 15

    # Cross-condition fallback yok
    if "FALLBACK" not in (result.get("match_type") or "").upper():
        score += 10

    # Rakip sayısı (ilgili kondisyon)
    cond = result.get("source_condition", "used")
    cnt_key = "amazon_used_count" if cond == "used" else "amazon_new_count"
    cnt = result.get(cnt_key) or 0
    if cnt == 0:
        score += 10
    elif cnt <= 3:
        score += 7
    elif cnt <= 5:
        score += 4
    elif cnt <= 10:
        score += 1

    # Seller feedback %
    fb_pct = result.get("ebay_seller_feedback")
    if fb_pct is not None:
        if fb_pct >= 99.0:
            score += 7
        elif fb_pct >= 97.0:
            score += 5
        elif fb_pct >= 95.0:
            score += 3
        elif fb_pct >= 90.0:
            score += 1

    # Seller feedback hacmi (scam önleme: 100% + 3 yorum = kırmızı bayrak)
    fb_cnt = result.get("ebay_seller_feedback_count")
    if fb_cnt is not None:
        if fb_cnt >= 500:
            score += 5
        elif fb_cnt >= 100:
            score += 3
        elif fb_cnt >= 50:
            score += 2
        elif fb_cnt >= 10:
            score += 1

    # BSR mevcut (likidite sinyali)
    if result.get("bsr") and result["bsr"] > 0:
        score += 3

    return max(0, min(100, score))


def confidence_tier(score: int) -> str:
    if score >= 75: return "high"
    if score >= 50: return "medium"
    if score >= 25: return "low"
    return "very_low"


# ── EV Score (Expected Value) ─────────────────────────────────────────────────
# EV = base_profit × min(velocity, 30) × (confidence / 100)
# Birim: USD/ay — "Bu kitabı 1 ay tutarsak güven-ayarlı beklenen kazanç"

def compute_ev(
    base_profit: Optional[float],
    velocity: Optional[float],
    confidence: int,
) -> Optional[float]:
    """
    Monthly Expected Value.
    Negatif base_profit → None (zarar beklentisi EV hesabına katılmaz).
    """
    if base_profit is None or velocity is None:
        return None
    if base_profit <= 0 or velocity <= 0:
        return None
    ev = base_profit * min(velocity, 30.0) * (confidence / 100.0)
    return round(ev, 2)


# ── Mevsimsellik Çarpanları (Textbook / Genel) ──────────────────────────
# Ders kitapları: Ocak-Şubat ve Ağustos-Eylül döneminde talep patlar.
# Yaz aylarında BSR velocity anlamsızlaşır — mevsimsel düzeltme gerekir.
import datetime as _dt

_TEXTBOOK_SEASON_MULT: dict[int, float] = {
    1: 1.40,   # Ocak: bahar dönemi başlangıcı (talep zirvesi)
    2: 1.25,   # Şubat: geç kayıt
    3: 0.90,   # Mart
    4: 0.80,   # Nisan: dönem sonu — ders kitapları satılıyor
    5: 0.60,   # Mayıs: yaz tatili
    6: 0.55,   # Haziran: en düşük
    7: 0.70,   # Temmuz: erken alıcılar
    8: 1.35,   # Ağustos: güz dönemi başlangıcı (talep zirvesi)
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
    Textbook modunda ders kitabı talep patternini uygular.
    """
    m = month or _dt.date.today().month
    table = _TEXTBOOK_SEASON_MULT if is_textbook else _GENERAL_SEASON_MULT
    return table.get(m, 1.0)


# ── Scenario Simulator ────────────────────────────────────────────────────
# Tek bir fiyat noktası yerine üç senaryo: best / base / worst
# v2: Worst case artık dinamik — yavaş satan kitaplarda risk çarpanı artar.

def _dynamic_worst_pct(velocity: Optional[float], bsr: Optional[int]) -> float:
    """
    Worst-case fiyat kırpma yüzdesi (0.0-1.0 arası — sell_price * (1 - pct) = worst).

    Hızlı satanlar (velocity > 10) → %15 kırpma (düşük risk)
    Orta satanlar (1-10)           → %25 kırpma
    Yavaş satanlar (< 1)          → %40 kırpma (yüksek risk — stok kalma ihtimali)
    BSR yok veya > 1M             → %45 kırpma (veri yokluğu cezası)
    """
    if not velocity or velocity <= 0:
        return 0.45
    if not bsr or bsr > 1_000_000:
        return 0.45
    if velocity >= 10.0:
        return 0.15
    if velocity >= 5.0:
        return 0.20
    if velocity >= 1.0:
        return 0.25
    if velocity >= 0.5:
        return 0.35
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
    best_case:   anlık buybox fiyatı (fiyat bu seviyede tutarsa)
    base_case:   tarihsel ortalama fiyat (avg_sell) — daha gerçekçi
                 avg yoksa: current_sell * 0.85 (ihtiyat payı)
    worst_case:  base_case * (1 - dynamic_pct)
                 v2: Kırpma yüzdesi velocity/BSR'a göre dinamik.

    Dönüş: {best_case_*, base_case_*, worst_case_*} dict'i.
    Eksik veri durumunda boş dict döner.
    """
    if not current_sell or buy_price <= 0:
        return {}

    best  = round(current_sell, 2)
    base  = round(avg_sell, 2) if (avg_sell and avg_sell > 0) else round(current_sell * 0.85, 2)

    worst_pct = _dynamic_worst_pct(velocity, bsr)
    worst = round(base * (1.0 - worst_pct), 2)

    def _p(sell: float) -> float:
        return round(sell - total_fees - buy_price, 2)

    def _roi(sell: float) -> float:
        p = _p(sell)
        return round(p / buy_price * 100, 1) if buy_price > 0 else 0.0

    return {
        "best_case_sell":    best,
        "best_case_profit":  _p(best),
        "best_case_roi":     _roi(best),
        "base_case_sell":    base,
        "base_case_profit":  _p(base),
        "base_case_roi":     _roi(base),
        "worst_case_sell":   worst,
        "worst_case_profit": _p(worst),
        "worst_case_roi":    _roi(worst),
        "worst_cut_pct":     round(worst_pct * 100, 1),
    }
