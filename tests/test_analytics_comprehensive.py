"""
TrackerBundle3 — Analytics Module Comprehensive Tests
======================================================
Tests: BSR velocity, days-to-sell, LC/Dewey classification,
       textbook scoring, confidence, EV, seasonality, scenarios.

~200 test scenarios.
"""
from __future__ import annotations

import math
import pytest

from app.analytics import (
    bsr_to_velocity,
    bsr_to_days_to_sell,
    lc_class_to_category,
    dewey_to_category,
    subjects_to_textbook_score,
    compute_confidence,
    confidence_tier,
    compute_ev,
    seasonal_velocity_mult,
    _dynamic_worst_pct,
    compute_scenarios,
)


# ─── BSR → Velocity Tests ───────────────────────────────────────────────────

class TestBsrToVelocity:

    def test_none_returns_none(self):
        assert bsr_to_velocity(None) is None

    def test_zero_returns_none(self):
        assert bsr_to_velocity(0) is None

    def test_negative_returns_none(self):
        assert bsr_to_velocity(-100) is None

    @pytest.mark.parametrize("bsr,expected_vel", [
        (1,        900.0),
        (50,       900.0),
        (99,       900.0),
        (100,      400.0),
        (499,      400.0),
        (500,      200.0),
        (1000,     100.0),
        (5000,      35.0),
        (10000,     18.0),
        (50000,      5.0),
        (100000,     2.5),
        (200000,     1.2),
        (400000,     0.5),
        (750000,     0.2),
        (1500000,    0.08),
    ])
    def test_velocity_tiers(self, bsr, expected_vel):
        assert bsr_to_velocity(bsr) == expected_vel

    def test_very_high_bsr(self):
        result = bsr_to_velocity(5_000_000)
        assert result == 0.08

    def test_velocity_decreases_with_bsr(self):
        v1 = bsr_to_velocity(100)
        v2 = bsr_to_velocity(100000)
        assert v1 > v2

    def test_bestseller_bsr(self):
        """BSR 1 should have highest velocity."""
        assert bsr_to_velocity(1) == 900.0


# ─── BSR → Days to Sell ──────────────────────────────────────────────────────

class TestBsrToDaysToSell:

    def test_none_returns_none(self):
        assert bsr_to_days_to_sell(None) is None

    def test_zero_returns_none(self):
        assert bsr_to_days_to_sell(0) is None

    def test_low_bsr_fast_sell(self):
        days = bsr_to_days_to_sell(50)  # velocity=900
        assert days == 1

    def test_medium_bsr(self):
        days = bsr_to_days_to_sell(50000)  # velocity=5.0
        assert days == math.ceil(30 / 5.0)  # 6 days

    def test_high_bsr_slow_sell(self):
        days = bsr_to_days_to_sell(1_000_000)  # velocity=0.2
        assert days == math.ceil(30 / 0.2)  # 150 days

    def test_max_730_days(self):
        days = bsr_to_days_to_sell(5_000_000)  # velocity=0.08
        # 30/0.08 = 375, still < 730
        assert days <= 730

    def test_days_always_positive_for_valid_bsr(self):
        for bsr in [1, 100, 1000, 10000, 100000, 1000000]:
            days = bsr_to_days_to_sell(bsr)
            assert days is not None
            assert days > 0


# ─── LC Class → Category Tests ───────────────────────────────────────────────

class TestLcClassToCategory:

    def test_empty_string(self):
        result = lc_class_to_category("")
        assert result["category"] == "Unknown"
        assert result["is_textbook_likely"] is False

    def test_none_input(self):
        result = lc_class_to_category(None)
        assert result["category"] == "Unknown"

    @pytest.mark.parametrize("lc,expected_cat,is_tb", [
        ("QA76.5",    "Mathematics",          True),
        ("QC175",     "Physics",              True),
        ("QD",        "Chemistry",            True),
        ("QH",        "Biology/Life Sciences", True),
        ("Q1",        "Science (General)",    True),
        ("TK",        "Engineering/Tech",     True),
        ("HF5001",    "Business/Finance",     True),
        ("HB",        "Economics",            True),
        ("LB",        "Education",            True),
        ("R",         "Medicine/Health",      True),
        ("KF",        "Law",                  True),
        ("PS",        "Language/Literature",  False),
        ("N",         "Fine Arts",            False),
        ("BD",        "Philosophy/Psychology", False),
        ("DA",        "History",              False),
        ("Z",         "Library Science",      False),
    ])
    def test_lc_categories(self, lc, expected_cat, is_tb):
        result = lc_class_to_category(lc)
        assert result["category"] == expected_cat
        assert result["is_textbook_likely"] == is_tb

    def test_lowercase_input(self):
        result = lc_class_to_category("qa76")
        assert result["category"] == "Mathematics"

    def test_unknown_prefix(self):
        result = lc_class_to_category("XX99")
        assert result["category"] == "General/Other"

    def test_whitespace_handling(self):
        result = lc_class_to_category("  QA76  ")
        assert result["category"] == "Mathematics"


# ─── Dewey → Category Tests ─────────────────────────────────────────────────

class TestDeweyToCategory:

    def test_empty_string(self):
        result = dewey_to_category("")
        assert result["category"] == "Unknown"

    def test_none_input(self):
        result = dewey_to_category(None)
        assert result["category"] == "Unknown"

    @pytest.mark.parametrize("dewey,expected_cat,is_tb", [
        ("005",    "Computer Science/General",    True),
        ("150",    "Philosophy/Psychology",        False),
        ("250",    "Religion",                     False),
        ("301",    "Social Sciences",              True),
        ("330",    "Economics",                     True),
        ("340",    "Law",                           True),
        ("370",    "Education",                     True),
        ("380",    "Commerce/Business",             True),
        ("450",    "Language/Linguistics",          False),
        ("510",    "Mathematics",                   True),
        ("530",    "Physics",                       True),
        ("540",    "Chemistry",                     True),
        ("570",    "Biology/Life Sciences",         True),
        ("620",    "Engineering",                   True),
        ("750",    "Fine Arts",                     False),
        ("850",    "Literature",                    False),
        ("950",    "History/Geography",             False),
    ])
    def test_dewey_categories(self, dewey, expected_cat, is_tb):
        result = dewey_to_category(dewey)
        assert result["category"] == expected_cat
        assert result["is_textbook_likely"] == is_tb

    def test_dewey_with_decimal(self):
        result = dewey_to_category("512.5")
        assert result["category"] == "Mathematics"

    def test_dewey_with_slash(self):
        result = dewey_to_category("510/512")
        assert result["category"] == "Mathematics"

    def test_invalid_dewey(self):
        result = dewey_to_category("abc")
        assert result["category"] == "Unknown"

    def test_dewey_out_of_range(self):
        result = dewey_to_category("1500")
        assert result["category"] == "General/Other"


# ─── Subjects → Textbook Score ───────────────────────────────────────────────

class TestSubjectsToTextbookScore:

    def test_empty_list(self):
        assert subjects_to_textbook_score([]) == 0.0

    def test_no_textbook_subjects(self):
        assert subjects_to_textbook_score(["Fiction", "Novel", "Mystery"]) == 0.0

    def test_all_textbook_subjects(self):
        score = subjects_to_textbook_score(["Mathematics", "Calculus", "Textbook"])
        assert score == 1.0

    def test_mixed_subjects(self):
        score = subjects_to_textbook_score(["Mathematics", "Fiction", "Novel", "Calculus"])
        assert 0.3 <= score <= 0.7

    def test_single_textbook_subject(self):
        score = subjects_to_textbook_score(["Mathematics"])
        assert score == 1.0

    def test_case_insensitive(self):
        score = subjects_to_textbook_score(["MATHEMATICS", "BIOLOGY"])
        assert score > 0

    def test_partial_match(self):
        """Keywords like 'college' should match in subject strings."""
        score = subjects_to_textbook_score(["College Mathematics Primer"])
        assert score > 0

    def test_max_score_is_1(self):
        subjects = ["textbook", "education", "mathematics", "calculus",
                     "algebra", "chemistry", "biology", "physics", "engineering"]
        score = subjects_to_textbook_score(subjects)
        assert score <= 1.0


# ─── Confidence Score Tests ──────────────────────────────────────────────────

class TestComputeConfidence:

    def test_no_sell_source_returns_zero(self):
        assert compute_confidence({}) == 0
        assert compute_confidence({"sell_source": ""}) == 0

    def test_buybox_gives_20_points(self):
        result = {"sell_source": "used_buybox"}
        score = compute_confidence(result)
        assert score >= 20

    def test_top_gives_8_points(self):
        result = {"sell_source": "used_top1"}
        score = compute_confidence(result)
        assert score >= 8

    def test_sub_condition_specific(self):
        r1 = compute_confidence({"sell_source": "used_buybox", "ebay_sub_condition": "like_new"})
        r2 = compute_confidence({"sell_source": "used_buybox", "ebay_sub_condition": "used_all"})
        assert r1 > r2

    def test_spike_warning_false_gives_points(self):
        r = compute_confidence({"sell_source": "used_buybox", "spike_warning": False})
        r_no = compute_confidence({"sell_source": "used_buybox"})
        assert r > r_no

    def test_spike_warning_true_no_points(self):
        r = compute_confidence({"sell_source": "used_buybox", "spike_warning": True})
        r_false = compute_confidence({"sell_source": "used_buybox", "spike_warning": False})
        assert r < r_false

    def test_amazon_not_selling_gives_points(self):
        r = compute_confidence({"sell_source": "used_buybox", "is_amazon_selling": False})
        r_no = compute_confidence({"sell_source": "used_buybox"})
        assert r > r_no

    def test_no_cross_condition_fallback(self):
        r = compute_confidence({"sell_source": "used_buybox", "match_type": "used_buybox"})
        r_fb = compute_confidence({"sell_source": "used_buybox", "match_type": "CROSS_CONDITION_FALLBACK"})
        assert r > r_fb

    def test_zero_competitors(self):
        r = compute_confidence({"sell_source": "used_buybox", "source_condition": "used",
                                 "amazon_used_count": 0})
        r_many = compute_confidence({"sell_source": "used_buybox", "source_condition": "used",
                                      "amazon_used_count": 20})
        assert r > r_many

    def test_high_feedback(self):
        r = compute_confidence({"sell_source": "used_buybox", "ebay_seller_feedback": 99.5})
        r_low = compute_confidence({"sell_source": "used_buybox", "ebay_seller_feedback": 80.0})
        assert r > r_low

    def test_feedback_count(self):
        r = compute_confidence({"sell_source": "used_buybox", "ebay_seller_feedback_count": 1000})
        r_low = compute_confidence({"sell_source": "used_buybox", "ebay_seller_feedback_count": 5})
        assert r > r_low

    def test_bsr_gives_3_points(self):
        r = compute_confidence({"sell_source": "used_buybox", "bsr": 50000})
        r_no = compute_confidence({"sell_source": "used_buybox"})
        assert r - r_no == 3

    def test_max_100(self):
        """Perfect data should not exceed 100."""
        r = compute_confidence({
            "sell_source": "used_buybox",
            "ebay_sub_condition": "like_new",
            "spike_warning": False,
            "is_amazon_selling": False,
            "match_type": "used_buybox",
            "source_condition": "used",
            "amazon_used_count": 0,
            "ebay_seller_feedback": 99.9,
            "ebay_seller_feedback_count": 10000,
            "bsr": 1000,
        })
        assert r <= 100

    def test_min_0(self):
        r = compute_confidence({"sell_source": "something"})
        assert r >= 0


# ─── Confidence Tier ─────────────────────────────────────────────────────────

class TestConfidenceTier:

    @pytest.mark.parametrize("score,expected", [
        (100, "high"),
        (75,  "high"),
        (74,  "medium"),
        (50,  "medium"),
        (49,  "low"),
        (25,  "low"),
        (24,  "very_low"),
        (0,   "very_low"),
    ])
    def test_tiers(self, score, expected):
        assert confidence_tier(score) == expected


# ─── EV Score Tests ──────────────────────────────────────────────────────────

class TestComputeEV:

    def test_none_profit(self):
        assert compute_ev(None, 5.0, 80) is None

    def test_none_velocity(self):
        assert compute_ev(10.0, None, 80) is None

    def test_zero_profit(self):
        assert compute_ev(0.0, 5.0, 80) is None

    def test_negative_profit(self):
        assert compute_ev(-5.0, 5.0, 80) is None

    def test_zero_velocity(self):
        assert compute_ev(10.0, 0.0, 80) is None

    def test_basic_ev(self):
        ev = compute_ev(10.0, 5.0, 100)
        assert ev == 10.0 * 5.0 * 1.0  # 50.0

    def test_confidence_scaling(self):
        ev_100 = compute_ev(10.0, 5.0, 100)
        ev_50 = compute_ev(10.0, 5.0, 50)
        assert ev_100 == ev_50 * 2

    def test_velocity_capped_at_30(self):
        ev = compute_ev(10.0, 900.0, 100)  # velocity=900 → capped at 30
        expected = 10.0 * 30.0 * 1.0
        assert ev == expected


# ─── Seasonality Tests ───────────────────────────────────────────────────────

class TestSeasonalVelocityMult:

    @pytest.mark.parametrize("month", range(1, 13))
    def test_all_months_valid_general(self, month):
        mult = seasonal_velocity_mult(month, is_textbook=False)
        assert 0.5 <= mult <= 1.5

    @pytest.mark.parametrize("month", range(1, 13))
    def test_all_months_valid_textbook(self, month):
        mult = seasonal_velocity_mult(month, is_textbook=True)
        assert 0.4 <= mult <= 1.5

    def test_textbook_peak_january(self):
        assert seasonal_velocity_mult(1, is_textbook=True) >= 1.3

    def test_textbook_peak_august(self):
        assert seasonal_velocity_mult(8, is_textbook=True) >= 1.3

    def test_textbook_trough_june(self):
        assert seasonal_velocity_mult(6, is_textbook=True) <= 0.6

    def test_general_q4_holiday(self):
        assert seasonal_velocity_mult(12, is_textbook=False) >= 1.15

    def test_default_uses_current_month(self):
        mult = seasonal_velocity_mult()
        assert 0.5 <= mult <= 1.5


# ─── Dynamic Worst % Tests ──────────────────────────────────────────────────

class TestDynamicWorstPct:

    def test_no_velocity(self):
        assert _dynamic_worst_pct(None, 50000) == 0.45

    def test_zero_velocity(self):
        assert _dynamic_worst_pct(0, 50000) == 0.45

    def test_no_bsr(self):
        assert _dynamic_worst_pct(5.0, None) == 0.45

    def test_high_bsr(self):
        assert _dynamic_worst_pct(5.0, 2_000_000) == 0.45

    def test_fast_seller(self):
        assert _dynamic_worst_pct(15.0, 5000) == 0.15

    def test_medium_fast(self):
        assert _dynamic_worst_pct(7.0, 20000) == 0.20

    def test_medium(self):
        assert _dynamic_worst_pct(2.0, 100000) == 0.25

    def test_slow(self):
        assert _dynamic_worst_pct(0.6, 500000) == 0.35

    def test_very_slow(self):
        assert _dynamic_worst_pct(0.1, 800000) == 0.40


# ─── Scenario Simulator Tests ───────────────────────────────────────────────

class TestComputeScenarios:

    def test_empty_when_no_sell_price(self):
        assert compute_scenarios(10.0, None, None, 5.0) == {}

    def test_empty_when_zero_buy(self):
        assert compute_scenarios(0, 30.0, 25.0, 5.0) == {}

    def test_basic_scenarios(self):
        result = compute_scenarios(10.0, 30.0, 25.0, 5.0)
        assert result["best_case_sell"] == 30.0
        assert result["base_case_sell"] == 25.0
        assert result["worst_case_sell"] < 25.0

    def test_best_case_profit(self):
        result = compute_scenarios(10.0, 30.0, 25.0, 5.0)
        # best profit = 30 - 5 - 10 = 15
        assert result["best_case_profit"] == 15.0

    def test_base_fallback_without_avg(self):
        """No avg_sell → base = current * 0.85."""
        result = compute_scenarios(10.0, 30.0, None, 5.0)
        assert result["base_case_sell"] == round(30.0 * 0.85, 2)

    def test_worst_case_dynamic(self):
        """With velocity/BSR, worst pct should be dynamic."""
        result = compute_scenarios(10.0, 30.0, 25.0, 5.0, velocity=15.0, bsr=5000)
        assert result["worst_cut_pct"] == 15.0  # fast seller = 15%

    def test_worst_case_no_velocity(self):
        result = compute_scenarios(10.0, 30.0, 25.0, 5.0, velocity=None)
        assert result["worst_cut_pct"] == 45.0  # no data = max cut

    def test_roi_calculation(self):
        result = compute_scenarios(10.0, 30.0, 25.0, 5.0)
        # best ROI = (30 - 5 - 10) / 10 * 100 = 150%
        assert result["best_case_roi"] == 150.0

    def test_all_keys_present(self):
        result = compute_scenarios(10.0, 30.0, 25.0, 5.0)
        expected_keys = {
            "best_case_sell", "best_case_profit", "best_case_roi",
            "base_case_sell", "base_case_profit", "base_case_roi",
            "worst_case_sell", "worst_case_profit", "worst_case_roi",
            "worst_cut_pct",
        }
        assert expected_keys == set(result.keys())

    def test_scenario_ordering(self):
        """Best >= base >= worst."""
        result = compute_scenarios(10.0, 40.0, 30.0, 5.0)
        assert result["best_case_sell"] >= result["base_case_sell"]
        assert result["base_case_sell"] >= result["worst_case_sell"]
        assert result["best_case_profit"] >= result["base_case_profit"]
        assert result["base_case_profit"] >= result["worst_case_profit"]
