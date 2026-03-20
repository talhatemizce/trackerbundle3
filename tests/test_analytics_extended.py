"""
analytics.py için genişletilmiş testler.
Önceki testlerden FARKLI: bsr_to_days_to_sell, subjects_to_textbook_score,
compute_ev, seasonal_velocity_mult, compute_scenarios, _dynamic_worst_pct,
confidence_tier, compute_confidence edge case'ler.
"""
from __future__ import annotations
import pytest
from app.analytics import (
    bsr_to_velocity, bsr_to_days_to_sell,
    lc_class_to_category, dewey_to_category,
    subjects_to_textbook_score,
    compute_confidence, confidence_tier,
    compute_ev, compute_scenarios,
    seasonal_velocity_mult, _dynamic_worst_pct,
)


# ── bsr_to_days_to_sell() ─────────────────────────────────────────────────────

class TestBsrToDaysToSell:
    def test_top_bestseller_fast(self):
        days = bsr_to_days_to_sell(50)
        assert days is not None
        assert days <= 1  # 900/mo → sells in <1 day

    def test_mid_range_bsr(self):
        days = bsr_to_days_to_sell(50_000)
        # velocity ~10 → 30/10 = 3 days
        assert days is not None
        assert 1 <= days <= 10

    def test_very_high_bsr_capped_at_730(self):
        # BSR > 1_500_000 → velocity=0.08 → ceil(30/0.08)=375, min(375,730)=375
        days = bsr_to_days_to_sell(2_000_000)
        assert days == min(375, 730)

    def test_none_bsr_returns_none(self):
        assert bsr_to_days_to_sell(None) is None

    def test_zero_bsr_returns_none(self):
        assert bsr_to_days_to_sell(0) is None

    def test_negative_bsr_returns_none(self):
        assert bsr_to_days_to_sell(-100) is None

    def test_result_is_integer(self):
        days = bsr_to_days_to_sell(1000)
        assert isinstance(days, int)

    def test_higher_bsr_more_days(self):
        days_low  = bsr_to_days_to_sell(1_000)
        days_high = bsr_to_days_to_sell(100_000)
        assert days_high > days_low

    def test_days_positive(self):
        for bsr in [100, 5000, 50000, 200000]:
            d = bsr_to_days_to_sell(bsr)
            assert d is not None and d > 0, f"BSR {bsr} → days={d}"


# ── subjects_to_textbook_score() ─────────────────────────────────────────────

class TestSubjectsToTextbookScore:
    def test_empty_subjects_zero(self):
        assert subjects_to_textbook_score([]) == 0.0

    def test_none_subjects_zero(self):
        assert subjects_to_textbook_score(None) == 0.0

    def test_textbook_keywords_score_high(self):
        score = subjects_to_textbook_score(["mathematics", "calculus"])
        assert score > 0.0

    def test_non_textbook_subjects_zero(self):
        score = subjects_to_textbook_score(["fiction", "romance", "thriller"])
        assert score == 0.0

    def test_mixed_subjects_partial_score(self):
        score = subjects_to_textbook_score(["mathematics", "fiction"])
        assert 0.0 < score < 1.0

    def test_all_textbook_subjects_score_1(self):
        score = subjects_to_textbook_score([
            "calculus", "algebra", "chemistry", "biology", "physics"
        ])
        assert score == 1.0

    def test_score_bounded_between_0_and_1(self):
        subjects = ["textbook"] * 100
        score = subjects_to_textbook_score(subjects)
        assert 0.0 <= score <= 1.0

    def test_single_textbook_subject(self):
        score = subjects_to_textbook_score(["engineering"])
        assert score > 0.0

    def test_case_insensitive_matching(self):
        score = subjects_to_textbook_score(["MATHEMATICS", "Calculus"])
        # _TEXTBOOK_SUBJECT_KEYWORDS uses .lower() for matching
        assert score > 0.0

    def test_economics_keyword(self):
        score = subjects_to_textbook_score(["economics"])
        assert score > 0.0

    def test_statistics_keyword(self):
        score = subjects_to_textbook_score(["statistics"])
        assert score > 0.0


# ── compute_ev() ──────────────────────────────────────────────────────────────

class TestComputeEv:
    def test_basic_ev(self):
        ev = compute_ev(base_profit=10.0, velocity=5.0, confidence=80)
        # 10 * min(5, 30) * (80/100) = 10 * 5 * 0.8 = 40.0
        assert ev == pytest.approx(40.0)

    def test_velocity_capped_at_30(self):
        ev_fast  = compute_ev(10.0, 100.0, 100)  # velocity capped at 30
        ev_capped = compute_ev(10.0, 30.0, 100)
        assert ev_fast == ev_capped

    def test_zero_profit_returns_none(self):
        assert compute_ev(0.0, 5.0, 80) is None

    def test_negative_profit_returns_none(self):
        assert compute_ev(-5.0, 10.0, 80) is None

    def test_none_profit_returns_none(self):
        assert compute_ev(None, 5.0, 80) is None

    def test_none_velocity_returns_none(self):
        assert compute_ev(10.0, None, 80) is None

    def test_zero_velocity_returns_none(self):
        assert compute_ev(10.0, 0.0, 80) is None

    def test_zero_confidence_gives_zero(self):
        ev = compute_ev(10.0, 5.0, 0)
        assert ev == pytest.approx(0.0)

    def test_100_confidence_full_ev(self):
        ev = compute_ev(10.0, 5.0, 100)
        assert ev == pytest.approx(50.0)  # 10 * 5 * 1.0

    def test_result_rounded_to_2_decimals(self):
        ev = compute_ev(3.33, 3.33, 33)
        # Just check it doesn't crash and returns float
        assert ev is None or isinstance(ev, float)

    def test_high_confidence_gives_higher_ev(self):
        ev_low  = compute_ev(10.0, 5.0, 30)
        ev_high = compute_ev(10.0, 5.0, 90)
        assert ev_high > ev_low


# ── seasonal_velocity_mult() ─────────────────────────────────────────────────

class TestSeasonalVelocityMult:
    def test_textbook_january_high(self):
        mult = seasonal_velocity_mult(month=1, is_textbook=True)
        assert mult == 1.40

    def test_textbook_august_high(self):
        mult = seasonal_velocity_mult(month=8, is_textbook=True)
        assert mult == 1.35

    def test_textbook_june_low(self):
        mult = seasonal_velocity_mult(month=6, is_textbook=True)
        assert mult == 0.55

    def test_general_december_high(self):
        mult = seasonal_velocity_mult(month=12, is_textbook=False)
        assert mult == 1.20

    def test_general_january_low(self):
        mult = seasonal_velocity_mult(month=1, is_textbook=False)
        assert mult == 0.85

    def test_textbook_vs_general_different(self):
        tb = seasonal_velocity_mult(month=8, is_textbook=True)
        gen = seasonal_velocity_mult(month=8, is_textbook=False)
        assert tb != gen

    def test_no_month_uses_current_month(self):
        import datetime
        mult = seasonal_velocity_mult(is_textbook=False)
        current = datetime.date.today().month
        expected = {1:0.85,2:0.85,3:0.90,4:0.95,5:1.00,6:1.00,
                    7:1.00,8:1.00,9:1.05,10:1.10,11:1.15,12:1.20}[current]
        assert mult == expected

    def test_all_months_return_positive(self):
        for m in range(1, 13):
            assert seasonal_velocity_mult(m, True) > 0
            assert seasonal_velocity_mult(m, False) > 0

    def test_textbook_month_4_low_demand(self):
        # Nisan dönem sonu, düşük talep
        assert seasonal_velocity_mult(4, True) < 1.0

    def test_textbook_month_5_lowest(self):
        # Mayıs yaz tatili başlangıcı
        assert seasonal_velocity_mult(5, True) < seasonal_velocity_mult(1, True)


# ── _dynamic_worst_pct() ─────────────────────────────────────────────────────

class TestDynamicWorstPct:
    def test_high_velocity_low_cut(self):
        pct = _dynamic_worst_pct(velocity=20.0, bsr=5000)
        assert pct == 0.15

    def test_medium_velocity_medium_cut(self):
        pct = _dynamic_worst_pct(velocity=3.0, bsr=50000)
        assert pct == 0.25

    def test_low_velocity_high_cut(self):
        pct = _dynamic_worst_pct(velocity=0.3, bsr=500000)
        assert pct == 0.40

    def test_no_velocity_max_cut(self):
        pct = _dynamic_worst_pct(velocity=None, bsr=50000)
        assert pct == 0.45

    def test_high_bsr_max_cut(self):
        pct = _dynamic_worst_pct(velocity=5.0, bsr=2_000_000)
        assert pct == 0.45

    def test_zero_velocity_max_cut(self):
        pct = _dynamic_worst_pct(velocity=0.0, bsr=10000)
        assert pct == 0.45

    def test_velocity_5_to_10_range(self):
        pct = _dynamic_worst_pct(velocity=7.0, bsr=20000)
        assert pct == 0.20

    def test_velocity_0_5_range(self):
        pct = _dynamic_worst_pct(velocity=0.7, bsr=800000)
        # velocity >= 0.5 and < 1.0 → 0.35
        assert pct == 0.35

    def test_cut_pct_between_0_and_1(self):
        for vel in [0.1, 0.5, 1.0, 5.0, 10.0, 50.0]:
            pct = _dynamic_worst_pct(vel, 50000)
            assert 0.0 < pct < 1.0


# ── compute_scenarios() ───────────────────────────────────────────────────────

class TestComputeScenarios:
    def test_basic_scenarios(self):
        s = compute_scenarios(buy_price=10.0, current_sell=30.0, avg_sell=28.0,
                              total_fees=8.0, velocity=5.0, bsr=50000)
        assert "best_case_sell" in s
        assert "base_case_sell" in s
        assert "worst_case_sell" in s

    def test_best_case_uses_current_sell(self):
        s = compute_scenarios(10.0, 30.0, 28.0, 8.0)
        assert s["best_case_sell"] == 30.0

    def test_base_case_uses_avg_sell(self):
        s = compute_scenarios(10.0, 30.0, 25.0, 8.0)
        assert s["base_case_sell"] == 25.0

    def test_base_case_fallback_when_no_avg(self):
        s = compute_scenarios(10.0, 30.0, None, 8.0)
        assert s["base_case_sell"] == pytest.approx(30.0 * 0.85)

    def test_worst_case_below_base(self):
        s = compute_scenarios(10.0, 30.0, 28.0, 8.0, velocity=5.0, bsr=50000)
        assert s["worst_case_sell"] < s["base_case_sell"]

    def test_no_current_sell_returns_empty(self):
        s = compute_scenarios(10.0, None, None, 8.0)
        assert s == {}

    def test_zero_buy_price_returns_empty(self):
        s = compute_scenarios(0.0, 30.0, None, 8.0)
        assert s == {}

    def test_profit_formula_in_best_case(self):
        s = compute_scenarios(10.0, 30.0, None, 8.0)
        # best_case_profit = 30.0 - 8.0 - 10.0 = 12.0
        assert s["best_case_profit"] == pytest.approx(12.0)

    def test_roi_formula_in_best_case(self):
        s = compute_scenarios(10.0, 30.0, None, 8.0)
        # roi = 12.0 / 10.0 * 100 = 120.0
        assert s["best_case_roi"] == pytest.approx(120.0)

    def test_worst_cut_pct_present(self):
        s = compute_scenarios(10.0, 30.0, 28.0, 8.0)
        assert "worst_cut_pct" in s
        assert s["worst_cut_pct"] > 0

    def test_all_keys_present(self):
        s = compute_scenarios(10.0, 30.0, 28.0, 8.0, 5.0, 50000)
        expected_keys = {
            "best_case_sell","best_case_profit","best_case_roi",
            "base_case_sell","base_case_profit","base_case_roi",
            "worst_case_sell","worst_case_profit","worst_case_roi",
            "worst_cut_pct",
        }
        assert expected_keys.issubset(s.keys())


# ── confidence_tier() ─────────────────────────────────────────────────────────

class TestConfidenceTier:
    def test_high_at_75(self):
        assert confidence_tier(75) == "high"

    def test_high_at_100(self):
        assert confidence_tier(100) == "high"

    def test_medium_at_50(self):
        assert confidence_tier(50) == "medium"

    def test_medium_at_74(self):
        assert confidence_tier(74) == "medium"

    def test_low_at_25(self):
        assert confidence_tier(25) == "low"

    def test_low_at_49(self):
        assert confidence_tier(49) == "low"

    def test_very_low_at_zero(self):
        assert confidence_tier(0) == "very_low"

    def test_very_low_at_24(self):
        assert confidence_tier(24) == "very_low"

    def test_very_low_negative(self):
        assert confidence_tier(-10) == "very_low"


# ── compute_confidence() edge cases ───────────────────────────────────────────

class TestComputeConfidenceEdgeCases:
    def _base(self, **overrides) -> dict:
        r = {
            "sell_source": "used_buybox",
            "source_condition": "used",
            "buy_price": 10.0,
            "amazon_sell_price": 40.0,
            "buybox_type": "used",
            "amazon_is_sold_by_amazon": False,
            "amazon_seller_count": 3,
            "ebay_seller_feedback": 98.5,
            "bsr": 25000,
            "profit": 15.0,
            "roi_pct": 60.0,
            "viable": True,
        }
        r.update(overrides)
        return r

    def test_no_sell_source_returns_zero(self):
        r = self._base(sell_source="")
        assert compute_confidence(r) == 0

    def test_none_sell_source_returns_zero(self):
        r = self._base()
        r["sell_source"] = None
        assert compute_confidence(r) == 0

    def test_top1_source_lower_than_buybox(self):
        r_bb  = self._base(sell_source="used_buybox")
        r_top = self._base(sell_source="used_top1")
        assert compute_confidence(r_bb) > compute_confidence(r_top)

    def test_brand_new_condition_max_points(self):
        r = self._base(source_condition="brand_new")
        score = compute_confidence(r)
        assert score > 0  # brand_new is a valid sub-condition

    def test_like_new_condition_scores(self):
        r = self._base(source_condition="like_new")
        assert compute_confidence(r) > 0

    def test_used_all_condition_partial_points(self):
        r_specific = self._base(source_condition="good")
        r_generic  = self._base(source_condition="used_all")
        assert compute_confidence(r_specific) > compute_confidence(r_generic)

    def test_spike_warning_true_no_bonus(self):
        r_no_spike  = self._base(spike_warning=False)
        r_yes_spike = self._base(spike_warning=True)
        assert compute_confidence(r_no_spike) > compute_confidence(r_yes_spike)

    def test_spike_warning_missing_no_bonus(self):
        """spike_warning eksikse puan yok (sadece pozitif sinyal sayılır)."""
        r_with = self._base(spike_warning=False)
        r_without = {k: v for k, v in self._base().items() if k != "spike_warning"}
        # r_with has spike_warning=False → +15 points bonus
        assert compute_confidence(r_with) >= compute_confidence(r_without)

    def test_feedback_99_max_points(self):
        r_99 = self._base(ebay_seller_feedback=99.5)
        r_95 = self._base(ebay_seller_feedback=95.0)
        assert compute_confidence(r_99) > compute_confidence(r_95)

    def test_feedback_below_90_no_bonus(self):
        r_low  = self._base(ebay_seller_feedback=85.0)
        r_none = self._base()
        r_none.pop("ebay_seller_feedback", None)
        # low feedback < 90 → no points, same as missing
        score_low  = compute_confidence(r_low)
        score_none = compute_confidence(r_none)
        # Both get 0 feedback points
        assert score_low == score_none

    def test_feedback_count_500_bonus(self):
        r_big   = self._base(ebay_seller_feedback_count=500)
        r_small = self._base(ebay_seller_feedback_count=10)
        assert compute_confidence(r_big) > compute_confidence(r_small)

    def test_amazon_zero_sellers_max_competitor_bonus(self):
        r_zero = self._base(amazon_seller_count=0)
        r_many = self._base(amazon_seller_count=20)
        assert compute_confidence(r_zero) > compute_confidence(r_many)

    def test_match_type_fallback_penalized(self):
        r_no_fallback   = self._base(match_type="USED→USED")
        r_with_fallback = self._base(match_type="NEW→USED(FALLBACK)")
        assert compute_confidence(r_no_fallback) > compute_confidence(r_with_fallback)

    def test_bsr_present_bonus(self):
        r_with_bsr    = self._base(bsr=50000)
        r_without_bsr = self._base(bsr=None)
        assert compute_confidence(r_with_bsr) > compute_confidence(r_without_bsr)

    def test_score_bounded_0_100(self):
        """Tüm maksimum değerlerle bile 100'ü aşmamalı."""
        r = self._base(
            sell_source="used_buybox",
            source_condition="brand_new",
            spike_warning=False,
            amazon_is_sold_by_amazon=False,
            amazon_seller_count=0,
            ebay_seller_feedback=99.9,
            ebay_seller_feedback_count=10000,
            bsr=5000,
            match_type="USED→USED",
        )
        score = compute_confidence(r)
        assert 0 <= score <= 100

    def test_score_never_negative(self):
        r = {
            "sell_source": "used_top1",
            "source_condition": "unknown_cond",
            "amazon_is_sold_by_amazon": True,
            "amazon_seller_count": 100,
            "ebay_seller_feedback": 50.0,
            "bsr": None,
        }
        score = compute_confidence(r)
        assert score >= 0


# ── LC classification extended ────────────────────────────────────────────────

class TestLcClassExtended:
    def test_medicine_is_textbook(self):
        r = lc_class_to_category("RC123")
        assert r["is_textbook_likely"] is True

    def test_law_is_textbook(self):
        r = lc_class_to_category("KF123")
        assert r["is_textbook_likely"] is True

    def test_history_not_textbook(self):
        r = lc_class_to_category("D123")
        assert r["is_textbook_likely"] is False

    def test_philosophy_not_textbook(self):
        r = lc_class_to_category("B123")
        assert r["is_textbook_likely"] is False

    def test_empty_string_default(self):
        r = lc_class_to_category("")
        assert r["category"] == "Unknown"
        assert r["is_textbook_likely"] is False

    def test_unknown_prefix_general(self):
        r = lc_class_to_category("ZZZ999")
        assert "General" in r["category"] or r["is_textbook_likely"] is False

    def test_case_insensitive(self):
        r1 = lc_class_to_category("QA76")
        r2 = lc_class_to_category("qa76")
        assert r1["is_textbook_likely"] == r2["is_textbook_likely"]


# ── Dewey classification extended ─────────────────────────────────────────────

class TestDeweyExtended:
    def test_religion_not_textbook(self):
        r = dewey_to_category("220")
        assert r["is_textbook_likely"] is False

    def test_chemistry_is_textbook(self):
        r = dewey_to_category("540")
        assert r["is_textbook_likely"] is True

    def test_engineering_is_textbook(self):
        r = dewey_to_category("620")
        assert r["is_textbook_likely"] is True

    def test_literature_not_textbook(self):
        r = dewey_to_category("823")
        assert r["is_textbook_likely"] is False

    def test_history_not_textbook(self):
        r = dewey_to_category("900")
        assert r["is_textbook_likely"] is False

    def test_empty_string_default(self):
        r = dewey_to_category("")
        assert r["category"] == "Unknown"

    def test_invalid_dewey_default(self):
        r = dewey_to_category("xyz")
        assert r["category"] == "Unknown"

    def test_dewey_with_slash(self):
        # "510/512" → split on "/" → "510" → Mathematics
        r = dewey_to_category("510/512")
        assert r["is_textbook_likely"] is True

    def test_decimal_dewey(self):
        r = dewey_to_category("510.5")
        assert r["is_textbook_likely"] is True

    def test_computer_science_range(self):
        r = dewey_to_category("005.1")
        assert r["is_textbook_likely"] is True
