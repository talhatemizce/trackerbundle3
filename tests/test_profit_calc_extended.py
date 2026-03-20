"""
profit_calc.py için genişletilmiş testler.
Önceki testlerden FARKLI senaryolar: fee aritmetiği, tier sınırları,
FeeConfig özelleştirme, top2 fiyat extraction, edge case'ler.
"""
from __future__ import annotations
import pytest
from app.profit_calc import (
    calculate, FeeConfig, ProfitResult, DEFAULT_FEES, _tier, _extract_sell_price
)


# ── FeeConfig ──────────────────────────────────────────────────────────────────

class TestFeeConfig:
    def test_default_referral_pct(self):
        assert DEFAULT_FEES.referral_pct == 0.15

    def test_default_closing_fee(self):
        assert DEFAULT_FEES.closing_fee == 1.80

    def test_default_fulfillment(self):
        assert DEFAULT_FEES.fulfillment == 3.50

    def test_default_inbound(self):
        assert DEFAULT_FEES.inbound == 0.60

    def test_total_fixed_sum(self):
        fees = FeeConfig()
        assert fees.total_fixed == pytest.approx(1.80 + 3.50 + 0.60)

    def test_total_high_price_uses_pct(self):
        fees = FeeConfig()
        sell = 100.0
        t = fees.total(sell)
        expected_referral = 100.0 * 0.15  # = 15.0 > min $1.00
        assert t == pytest.approx(expected_referral + fees.total_fixed, abs=0.01)

    def test_total_low_price_uses_minimum_referral(self):
        """Referral fee minimum $1.00 — $5 sell'de %15 = $0.75, ama min $1.00 uygulanır."""
        fees = FeeConfig()
        t = fees.total(5.0)
        # referral = max(1.00, 5.0*0.15) = max(1.00, 0.75) = 1.00
        assert t == pytest.approx(1.00 + fees.total_fixed, abs=0.01)

    def test_total_exactly_at_referral_threshold(self):
        """$6.67'de %15 = $1.00 — tam sınır."""
        fees = FeeConfig()
        sell = round(1.00 / 0.15, 4)  # ~6.6667
        t = fees.total(sell)
        assert t >= 1.00 + fees.total_fixed - 0.02

    def test_custom_fee_config(self):
        fees = FeeConfig(referral_pct=0.12, closing_fee=2.00, fulfillment=4.00, inbound=0.80)
        assert fees.referral_pct == 0.12
        assert fees.total_fixed == pytest.approx(2.00 + 4.00 + 0.80)

    def test_custom_total(self):
        fees = FeeConfig(referral_pct=0.10, closing_fee=1.00, fulfillment=2.00, inbound=0.50)
        t = fees.total(50.0)
        assert t == pytest.approx(5.00 + 3.50, abs=0.01)

    def test_zero_sell_price_uses_minimum_referral(self):
        """Sıfır satış fiyatında bile minimum $1.00 referral uygulanır."""
        fees = FeeConfig()
        t = fees.total(0.0)
        assert t == pytest.approx(1.00 + fees.total_fixed, abs=0.01)


# ── _tier() ───────────────────────────────────────────────────────────────────

class TestTierFunction:
    def test_fire_at_exactly_30(self):
        assert _tier(30.0) == "fire"

    def test_fire_above_30(self):
        assert _tier(150.0) == "fire"

    def test_good_at_exactly_15(self):
        assert _tier(15.0) == "good"

    def test_good_below_30(self):
        assert _tier(29.9) == "good"

    def test_low_above_zero(self):
        assert _tier(0.1) == "low"

    def test_low_at_14_9(self):
        assert _tier(14.9) == "low"

    def test_loss_at_zero(self):
        assert _tier(0.0) == "loss"

    def test_loss_negative(self):
        assert _tier(-10.0) == "loss"

    def test_loss_deeply_negative(self):
        assert _tier(-999.9) == "loss"

    def test_good_at_29_9(self):
        assert _tier(29.9) == "good"


# ── _extract_sell_price() ─────────────────────────────────────────────────────

class TestExtractSellPrice:
    def _amz(self, used_bb=None, used_top=None, new_bb=None, new_top=None):
        data = {}
        data["used"] = {"buybox": {"total": used_bb} if used_bb else {}, "top2": [{"total": used_top}] if used_top else []}
        data["new"]  = {"buybox": {"total": new_bb}  if new_bb  else {}, "top2": [{"total": new_top}]  if new_top  else []}
        return data

    def test_used_buybox_preferred_over_top2(self):
        amz = self._amz(used_bb=30.0, used_top=25.0)
        price, src = _extract_sell_price(amz, "used")
        assert price == 30.0
        assert src == "used_buybox"

    def test_used_top2_fallback_when_no_buybox(self):
        amz = self._amz(used_top=28.0)
        price, src = _extract_sell_price(amz, "used")
        assert price == 28.0
        assert src == "used_top1"

    def test_new_buybox_preferred_when_condition_new(self):
        amz = self._amz(new_bb=45.0, new_top=40.0)
        price, src = _extract_sell_price(amz, "new")
        assert price == 45.0
        assert src == "new_buybox"

    def test_new_top2_fallback_when_condition_new(self):
        amz = self._amz(new_top=42.0)
        price, src = _extract_sell_price(amz, "new")
        assert price == 42.0
        assert src == "new_top1"

    def test_no_cross_fallback_used_condition(self):
        """used condition + only new_bb → None (cross-condition yasak)."""
        amz = self._amz(new_bb=50.0)
        price, src = _extract_sell_price(amz, "used")
        assert price is None
        assert src == "unknown"

    def test_no_cross_fallback_new_condition(self):
        """new condition + only used_bb → None."""
        amz = self._amz(used_bb=35.0)
        price, src = _extract_sell_price(amz, "new")
        assert price is None
        assert src == "unknown"

    def test_legacy_mode_falls_back_used_to_new(self):
        amz = self._amz(new_bb=50.0)
        price, src = _extract_sell_price(amz, "")
        assert price == 50.0
        assert "new" in src

    def test_legacy_mode_prefers_used(self):
        amz = self._amz(used_bb=30.0, new_bb=50.0)
        price, src = _extract_sell_price(amz, "")
        assert price == 30.0
        assert "used" in src

    def test_empty_amazon_data(self):
        price, src = _extract_sell_price({}, "used")
        assert price is None
        assert src == "unknown"

    def test_none_amazon_data(self):
        price, src = _extract_sell_price(None, "used")
        assert price is None
        assert src == "unknown"

    def test_buybox_total_zero_skipped(self):
        """buybox.total=0 → skip, try top2."""
        data = {"used": {"buybox": {"total": 0}, "top2": [{"total": 25.0}]}, "new": {"top2": []}}
        price, src = _extract_sell_price(data, "used")
        # total=0 is falsy — buybox skipped
        assert price == 25.0
        assert src == "used_top1"


# ── calculate() ───────────────────────────────────────────────────────────────

class TestCalculateMain:
    def _amz_used(self, bb):
        return {"used": {"buybox": {"total": bb}, "top2": []}, "new": {"top2": []}}

    def _amz_new(self, bb):
        return {"used": {"top2": []}, "new": {"buybox": {"total": bb}, "top2": []}}

    def test_returns_profit_result_instance(self):
        r = calculate(10.0, self._amz_used(40.0), DEFAULT_FEES)
        assert isinstance(r, ProfitResult)

    def test_to_dict_contains_all_fields(self):
        r = calculate(10.0, self._amz_used(40.0), DEFAULT_FEES)
        d = r.to_dict()
        for key in ("sell_price","sell_source","ebay_cost","referral_fee",
                    "closing_fee","fulfillment","inbound","total_fees",
                    "profit","roi_pct","roi_tier","viable"):
            assert key in d, f"Missing key: {key}"

    def test_ebay_cost_stored_correctly(self):
        r = calculate(12.50, self._amz_used(40.0), DEFAULT_FEES)
        assert r.ebay_cost == pytest.approx(12.50)

    def test_total_fees_matches_components(self):
        r = calculate(10.0, self._amz_used(40.0), DEFAULT_FEES)
        expected = r.referral_fee + r.closing_fee + r.fulfillment + r.inbound
        assert r.total_fees == pytest.approx(expected, abs=0.01)

    def test_profit_formula(self):
        r = calculate(10.0, self._amz_used(40.0), DEFAULT_FEES)
        expected = r.sell_price - r.total_fees - r.ebay_cost
        assert r.profit == pytest.approx(expected, abs=0.01)

    def test_roi_pct_formula(self):
        r = calculate(10.0, self._amz_used(40.0), DEFAULT_FEES)
        expected_roi = r.profit / r.ebay_cost * 100
        assert r.roi_pct == pytest.approx(expected_roi, abs=0.1)

    def test_viable_true_when_profit_positive(self):
        r = calculate(5.0, self._amz_used(40.0), DEFAULT_FEES)
        assert r.viable is True

    def test_viable_false_when_no_profit(self):
        r = calculate(35.0, self._amz_used(36.0), DEFAULT_FEES)
        # fees ~7.4 + 35 = 42.4 > 36 → profit negative
        assert r is None or r.viable is False

    def test_zero_ebay_price_returns_none(self):
        r = calculate(0.0, self._amz_used(40.0), DEFAULT_FEES)
        assert r is None

    def test_negative_ebay_price_returns_none(self):
        r = calculate(-5.0, self._amz_used(40.0), DEFAULT_FEES)
        assert r is None

    def test_custom_fees_applied(self):
        custom = FeeConfig(referral_pct=0.08, closing_fee=1.00, fulfillment=2.50, inbound=0.50)
        r_default = calculate(10.0, self._amz_used(40.0), DEFAULT_FEES)
        r_custom  = calculate(10.0, self._amz_used(40.0), custom)
        assert r_custom.profit > r_default.profit  # daha düşük fee → daha yüksek profit

    def test_new_condition_uses_new_buybox(self):
        amz = self._amz_new(50.0)
        r = calculate(10.0, amz, DEFAULT_FEES, source_condition="new")
        assert r is not None
        assert r.sell_source == "new_buybox"

    def test_new_condition_no_fallback_to_used(self):
        amz = self._amz_used(50.0)
        r = calculate(10.0, amz, DEFAULT_FEES, source_condition="new")
        assert r is None

    def test_roi_tier_fire(self):
        r = calculate(5.0, self._amz_used(50.0), DEFAULT_FEES)
        assert r is not None
        assert r.roi_tier == "fire"

    def test_roi_tier_loss(self):
        r = calculate(30.0, self._amz_used(31.0), DEFAULT_FEES)
        # çok düşük margin
        if r is not None:
            assert r.roi_tier == "loss"

    def test_referral_fee_minimum_enforced(self):
        """$6.67 sell → %15 referral tam min sınırında."""
        small_amz = {"used": {"buybox": {"total": 6.67}, "top2": []}, "new": {"top2": []}}
        r = calculate(1.0, small_amz, DEFAULT_FEES, source_condition="used")
        if r:
            assert r.referral_fee >= 1.00

    def test_sell_price_rounded_to_2_decimals(self):
        amz = {"used": {"buybox": {"total": 33.333}, "top2": []}, "new": {"top2": []}}
        r = calculate(10.0, amz, DEFAULT_FEES)
        if r:
            assert r.sell_price == round(r.sell_price, 2)
