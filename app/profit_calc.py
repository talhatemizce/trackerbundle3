"""
Profit / ROI calculator — kitap arbitraj için MVP fee model.

Tüm değerler USD. Kalibre edilebilir (config veya override).

Amazon FBA Books fee structure (US, 2024-2025 verified):
  - Referral fee:   15% of sale price (min $1.00)
  - Closing fee:    $1.80 (media category fixed)
  - Fulfillment:    weight-based; kitap için ~$3.50 ortalama (0.5lb paperback)
  - Inbound ship:   varsayım ~$0.60/book (UPS/USPS media mail estimate)

Returns structured dict — UI doğrudan kullanır, hesaplama UI'da yok.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class FeeConfig:
    """Kalibre edilebilir fee parametreleri. İleride DB/config'e taşınabilir."""
    referral_pct: float = 0.15      # Amazon referral %15
    closing_fee:  float = 1.80      # Media closing fee
    fulfillment:  float = 3.50      # FBA fulfillment (ortalama kitap)
    inbound:      float = 0.60      # Gönderim tahmini (media mail)

    @property
    def total_fixed(self) -> float:
        return self.closing_fee + self.fulfillment + self.inbound


DEFAULT_FEES = FeeConfig()


@dataclass
class ProfitResult:
    sell_price:    float          # Amazon sell price used
    sell_source:   str            # "used_buybox" | "new_buybox" | "used_top1" | "unknown"
    ebay_cost:     float          # eBay total (item + ship)
    referral_fee:  float
    closing_fee:   float
    fulfillment:   float
    inbound:       float
    total_fees:    float
    profit:        float          # sell - fees - ebay_cost
    roi_pct:       float          # profit / ebay_cost * 100
    roi_tier:      str            # "fire" | "good" | "low" | "loss"
    viable:        bool           # profit > 0

    def to_dict(self) -> dict:
        return asdict(self)


def calculate(
    ebay_total: float,
    amazon_data: Optional[dict],
    fees: FeeConfig = DEFAULT_FEES,
) -> Optional[ProfitResult]:
    """
    amazon_data: /decide/asin veya /alerts/details'ten gelen amazon blob.
    Şema: {"used": {"buybox": {"total": X}, "top2": [...]}, "new": {...}}
    
    Returns None if sell_price cannot be determined.
    """
    if ebay_total <= 0:
        return None

    sell_price, sell_source = _extract_sell_price(amazon_data)
    if sell_price is None:
        return None

    referral = max(1.00, sell_price * fees.referral_pct)
    total_fees = referral + fees.closing_fee + fees.fulfillment + fees.inbound
    profit = sell_price - total_fees - ebay_total
    roi_pct = (profit / ebay_total * 100) if ebay_total > 0 else 0.0

    return ProfitResult(
        sell_price=round(sell_price, 2),
        sell_source=sell_source,
        ebay_cost=round(ebay_total, 2),
        referral_fee=round(referral, 2),
        closing_fee=fees.closing_fee,
        fulfillment=fees.fulfillment,
        inbound=fees.inbound,
        total_fees=round(total_fees, 2),
        profit=round(profit, 2),
        roi_pct=round(roi_pct, 1),
        roi_tier=_tier(roi_pct),
        viable=profit > 0,
    )


def _extract_sell_price(amazon_data: Optional[dict]) -> tuple[Optional[float], str]:
    """Priority: used buybox → used top1 → new buybox → new top1."""
    if not amazon_data:
        return None, "unknown"

    for section, label_bb, label_top in [
        ("used", "used_buybox", "used_top1"),
        ("new",  "new_buybox",  "new_top1"),
    ]:
        s = amazon_data.get(section) or {}
        bb = s.get("buybox")
        if bb and bb.get("total"):
            return float(bb["total"]), label_bb
        top2 = s.get("top2") or []
        if top2 and top2[0].get("total"):
            return float(top2[0]["total"]), label_top

    return None, "unknown"


def _tier(roi_pct: float) -> str:
    if roi_pct >= 30:  return "fire"
    if roi_pct >= 15:  return "good"
    if roi_pct > 0:    return "low"
    return "loss"


# ── Dynamic Limit Suggestion ──────────────────────────────────────────────────
# "Amazon'da $X'e satılıyorsa ve %Y ROI istiyorsan, eBay'den max $Z'ye al."

@dataclass
class SuggestedLimit:
    sell_price: float
    sell_source: str
    target_roi_pct: float
    max_buy: float           # eBay'den en fazla bu fiyata al
    referral_fee: float
    closing_fee: float
    fulfillment: float
    inbound: float
    total_fees: float
    expected_profit: float   # sell - fees - max_buy
    tier: str                # roi tier at exactly target ROI

    def to_dict(self) -> dict:
        return asdict(self)


def suggest_limit(
    amazon_data: Optional[dict],
    target_roi_pct: float = 30.0,
    fees: FeeConfig = DEFAULT_FEES,
) -> Optional[SuggestedLimit]:
    """
    Amazon satış fiyatından geriye doğru hesapla:
      max_buy = (sell_price - total_fees) / (1 + target_roi/100)

    Bu fiyattan alırsan tam olarak target_roi_pct ROI elde edersin.
    """
    if target_roi_pct < 0:
        return None

    sell_price, sell_source = _extract_sell_price(amazon_data)
    if sell_price is None or sell_price <= 0:
        return None

    referral = max(1.00, sell_price * fees.referral_pct)
    total_fees = referral + fees.closing_fee + fees.fulfillment + fees.inbound

    net_after_fees = sell_price - total_fees
    if net_after_fees <= 0:
        return None

    # max_buy * (1 + roi/100) = net_after_fees
    max_buy = net_after_fees / (1 + target_roi_pct / 100.0)
    expected_profit = net_after_fees - max_buy

    if max_buy <= 0:
        return None

    return SuggestedLimit(
        sell_price=round(sell_price, 2),
        sell_source=sell_source,
        target_roi_pct=round(target_roi_pct, 1),
        max_buy=round(max_buy, 2),
        referral_fee=round(referral, 2),
        closing_fee=fees.closing_fee,
        fulfillment=fees.fulfillment,
        inbound=fees.inbound,
        total_fees=round(total_fees, 2),
        expected_profit=round(expected_profit, 2),
        tier=_tier(target_roi_pct),
    )

