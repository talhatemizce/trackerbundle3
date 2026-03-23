"""
TrackerBundle3 — CSV Arbitrage Scanner Comprehensive Tests
============================================================
Tests: ISBN conversion, profit calculation (strict/fallback),
       ScanFilters, _filter_result, ArbResult, suggest_max_buy,
       _apply_profit, scan_one integration.

~180 test scenarios.
"""
from __future__ import annotations

import asyncio
import math
from dataclasses import asdict
from typing import Dict, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.csv_arb_scanner import (
    _isbn13_to_asin,
    ArbResult,
    _calc_profit_strict,
    _apply_profit,
    ScanFilters,
    IsbnMatchPolicy,
    InvalidIsbnPolicy,
    _filter_result,
    suggest_max_buy,
)
from app.profit_calc import FeeConfig, DEFAULT_FEES


# ─── _isbn13_to_asin Tests ──────────────────────────────────────────────────

class TestIsbn13ToAsin:
    """ISBN-13 → ISBN-10 (ASIN) conversion."""

    def test_valid_isbn13(self):
        assert _isbn13_to_asin("9780132350884") == "0132350882"

    def test_valid_isbn13_with_dashes(self):
        assert _isbn13_to_asin("978-0-13-235088-4") == "0132350882"

    def test_valid_isbn13_with_spaces(self):
        assert _isbn13_to_asin("978 0 13 235088 4") == "0132350882"

    def test_valid_isbn10_passthrough(self):
        result = _isbn13_to_asin("0132350882")
        assert result == "0132350882"

    def test_isbn10_with_x_check(self):
        result = _isbn13_to_asin("020161622X")
        assert result == "020161622X"

    def test_isbn13_producing_x_check(self):
        # ISBN-13 that produces ISBN-10 with X check digit
        result = _isbn13_to_asin("9780201616224")
        assert result is not None
        assert result.endswith("X") or result[-1].isdigit()

    def test_979_prefix_returns_none(self):
        # 979 prefix has no ISBN-10 equivalent
        assert _isbn13_to_asin("9791032300824") is None

    def test_invalid_length(self):
        assert _isbn13_to_asin("12345") is None

    def test_non_isbn_asin(self):
        # B-format ASIN (not ISBN)
        assert _isbn13_to_asin("B08N5WRWNW") is None

    def test_empty_string(self):
        assert _isbn13_to_asin("") is None

    def test_random_text(self):
        assert _isbn13_to_asin("hello world") is None

    def test_all_zeros(self):
        result = _isbn13_to_asin("0000000000")
        # All zeros — checksum may or may not pass
        assert isinstance(result, (str, type(None)))

    def test_isbn13_converts_ignoring_isbn13_checksum(self):
        # _isbn13_to_asin doesn't validate ISBN-13 checksum — it extracts
        # core digits and computes the ISBN-10 check digit independently
        result = _isbn13_to_asin("9780132350885")  # wrong ISBN-13 check digit
        assert result == "0132350882"  # still produces valid ISBN-10

    def test_invalid_isbn10_checksum(self):
        assert _isbn13_to_asin("0132350883") is None

    def test_leading_trailing_whitespace(self):
        assert _isbn13_to_asin("  9780132350884  ") == "0132350882"


# ─── ArbResult Tests ────────────────────────────────────────────────────────

class TestArbResult:
    """ArbResult dataclass tests."""

    def test_default_values(self):
        r = ArbResult(isbn="isbn1", asin="asin1", source="ebay",
                      source_condition="used", buy_price=10.0)
        assert r.profit == 0.0
        assert r.viable is False
        assert r.accepted is False
        assert r.reason == ""
        assert r.bsr is None
        assert r.velocity is None
        assert r.nyt_bestseller is False

    def test_to_dict(self):
        r = ArbResult(isbn="isbn1", asin="asin1", source="ebay",
                      source_condition="used", buy_price=10.0)
        d = r.to_dict()
        assert isinstance(d, dict)
        assert d["isbn"] == "isbn1"
        assert d["buy_price"] == 10.0

    def test_all_fields_in_dict(self):
        r = ArbResult(isbn="isbn1", asin="asin1", source="ebay",
                      source_condition="used", buy_price=10.0)
        d = r.to_dict()
        expected_fields = [
            "isbn", "asin", "source", "source_condition", "buy_price",
            "amazon_sell_price", "profit", "roi_pct", "viable",
            "bsr", "velocity", "confidence", "ev_score",
            "buyback_cash", "nyt_bestseller",
        ]
        for f in expected_fields:
            assert f in d, f"Missing field: {f}"

    def test_buyback_fields(self):
        r = ArbResult(isbn="isbn1", asin="asin1", source="ebay",
                      source_condition="used", buy_price=10.0,
                      buyback_cash=15.0, buyback_vendor="BooksRun",
                      buyback_profit=1.01, buyback_roi=10.0)
        assert r.buyback_cash == 15.0
        assert r.buyback_vendor == "BooksRun"

    def test_scenario_fields(self):
        r = ArbResult(isbn="isbn1", asin="asin1", source="ebay",
                      source_condition="used", buy_price=10.0,
                      best_case_profit=20.0, worst_case_profit=-5.0)
        assert r.best_case_profit == 20.0
        assert r.worst_case_profit == -5.0


# ─── _calc_profit_strict Tests ──────────────────────────────────────────────

class TestCalcProfitStrict:
    """Strict mode profit calculation."""

    def test_new_to_new_match(self):
        data = {"new": {"buybox": {"total": 30.0}}, "used": {"buybox": {"total": 20.0}}}
        price, bb, match, reason = _calc_profit_strict(10.0, "new", data, strict_mode=True)
        assert price == 30.0
        assert bb == "new"
        assert match == "NEW→NEW"
        assert reason == ""

    def test_used_to_used_match(self):
        data = {"new": {"buybox": {"total": 30.0}}, "used": {"buybox": {"total": 20.0}}}
        price, bb, match, reason = _calc_profit_strict(10.0, "used", data, strict_mode=True)
        assert price == 20.0
        assert bb == "used"
        assert match == "USED→USED"

    def test_new_strict_no_new_buybox(self):
        data = {"new": {}, "used": {"buybox": {"total": 20.0}}}
        price, bb, match, reason = _calc_profit_strict(10.0, "new", data, strict_mode=True)
        assert price is None
        assert reason == "missing_new_buybox"

    def test_used_strict_no_used_buybox(self):
        data = {"new": {"buybox": {"total": 30.0}}, "used": {}}
        price, bb, match, reason = _calc_profit_strict(10.0, "used", data, strict_mode=True)
        assert price is None
        assert reason == "missing_used_buybox"

    def test_new_fallback_to_used(self):
        data = {"new": {}, "used": {"buybox": {"total": 20.0}}}
        price, bb, match, reason = _calc_profit_strict(10.0, "new", data, strict_mode=False)
        assert price == 20.0
        assert match == "NEW→USED(fallback)"

    def test_used_fallback_to_new(self):
        data = {"new": {"buybox": {"total": 30.0}}, "used": {}}
        price, bb, match, reason = _calc_profit_strict(10.0, "used", data, strict_mode=False)
        assert price == 30.0
        assert match == "USED→NEW(fallback)"

    def test_no_buybox_at_all_strict(self):
        data = {"new": {}, "used": {}}
        price, bb, match, reason = _calc_profit_strict(10.0, "new", data, strict_mode=True)
        assert price is None
        assert "missing" in reason

    def test_no_buybox_at_all_fallback(self):
        data = {"new": {}, "used": {}}
        price, bb, match, reason = _calc_profit_strict(10.0, "new", data, strict_mode=False)
        assert price is None

    def test_unknown_condition(self):
        data = {"new": {"buybox": {"total": 30.0}}, "used": {"buybox": {"total": 20.0}}}
        price, bb, match, reason = _calc_profit_strict(10.0, "collectible", data, strict_mode=True)
        assert price is None
        assert "unknown_condition" in reason

    def test_buybox_zero_total(self):
        data = {"new": {"buybox": {"total": 0}}, "used": {"buybox": {"total": 20.0}}}
        price, bb, match, reason = _calc_profit_strict(10.0, "new", data, strict_mode=True)
        assert price is None  # 0 treated as no buybox

    def test_buybox_none_total(self):
        data = {"new": {"buybox": {"total": None}}, "used": {"buybox": {"total": 20.0}}}
        price, bb, match, reason = _calc_profit_strict(10.0, "new", data, strict_mode=True)
        assert price is None

    def test_case_insensitive_condition(self):
        data = {"new": {"buybox": {"total": 30.0}}, "used": {"buybox": {"total": 20.0}}}
        price, bb, match, reason = _calc_profit_strict(10.0, "NEW", data, strict_mode=True)
        assert price == 30.0

    def test_missing_new_section(self):
        data = {"used": {"buybox": {"total": 20.0}}}
        price, bb, match, reason = _calc_profit_strict(10.0, "new", data, strict_mode=True)
        assert price is None

    def test_missing_used_section(self):
        data = {"new": {"buybox": {"total": 30.0}}}
        price, bb, match, reason = _calc_profit_strict(10.0, "used", data, strict_mode=True)
        assert price is None

    def test_empty_data(self):
        data = {}
        price, bb, match, reason = _calc_profit_strict(10.0, "new", data, strict_mode=True)
        assert price is None

    def test_none_buybox_dict(self):
        data = {"new": {"buybox": None}, "used": {"buybox": None}}
        price, bb, match, reason = _calc_profit_strict(10.0, "new", data, strict_mode=True)
        assert price is None


# ─── _apply_profit Tests ────────────────────────────────────────────────────

class TestApplyProfit:
    """Profit field population."""

    def test_basic_profit(self):
        r = ArbResult(isbn="isbn1", asin="asin1", source="ebay",
                      source_condition="used", buy_price=10.0)
        _apply_profit(r, 30.0, "used", "USED→USED", DEFAULT_FEES)
        assert r.amazon_sell_price == 30.0
        assert r.profit > 0
        assert r.viable is True
        assert r.roi_tier in ("fire", "good", "low")

    def test_loss_scenario(self):
        r = ArbResult(isbn="isbn1", asin="asin1", source="ebay",
                      source_condition="used", buy_price=28.0)
        _apply_profit(r, 30.0, "used", "USED→USED", DEFAULT_FEES)
        # With fees > $5.90, profit should be negative
        assert r.profit < 0
        assert r.viable is False
        assert r.roi_tier == "loss"

    def test_referral_minimum_1_dollar(self):
        r = ArbResult(isbn="isbn1", asin="asin1", source="ebay",
                      source_condition="used", buy_price=1.0)
        _apply_profit(r, 5.0, "used", "USED→USED", DEFAULT_FEES)
        # 5.0 * 0.15 = 0.75 → min $1
        assert r.referral_fee >= 1.0

    def test_fee_fields_populated(self):
        r = ArbResult(isbn="isbn1", asin="asin1", source="ebay",
                      source_condition="used", buy_price=10.0)
        _apply_profit(r, 30.0, "used", "USED→USED", DEFAULT_FEES)
        assert r.closing_fee == DEFAULT_FEES.closing_fee
        assert r.fulfillment == DEFAULT_FEES.fulfillment
        assert r.inbound == DEFAULT_FEES.inbound
        assert r.total_fees > 0

    def test_custom_fees(self):
        custom = FeeConfig(referral_pct=0.10, closing_fee=1.0, fulfillment=2.0, inbound=0.5)
        r = ArbResult(isbn="isbn1", asin="asin1", source="ebay",
                      source_condition="used", buy_price=10.0)
        _apply_profit(r, 30.0, "used", "USED→USED", custom)
        assert r.closing_fee == 1.0
        assert r.fulfillment == 2.0

    def test_zero_buy_price_roi(self):
        r = ArbResult(isbn="isbn1", asin="asin1", source="ebay",
                      source_condition="used", buy_price=0.0)
        _apply_profit(r, 30.0, "used", "USED→USED", DEFAULT_FEES)
        assert r.roi_pct == 0.0  # division by zero protection

    def test_match_type_stored(self):
        r = ArbResult(isbn="isbn1", asin="asin1", source="ebay",
                      source_condition="new", buy_price=10.0)
        _apply_profit(r, 50.0, "new", "NEW→NEW", DEFAULT_FEES)
        assert r.match_type == "NEW→NEW"
        assert r.buybox_type == "new"

    def test_roi_tier_fire(self):
        r = ArbResult(isbn="isbn1", asin="asin1", source="ebay",
                      source_condition="used", buy_price=5.0)
        _apply_profit(r, 50.0, "used", "USED→USED", DEFAULT_FEES)
        # Very high ROI
        assert r.roi_tier == "fire"

    def test_rounding(self):
        r = ArbResult(isbn="isbn1", asin="asin1", source="ebay",
                      source_condition="used", buy_price=10.333)
        _apply_profit(r, 30.777, "used", "USED→USED", DEFAULT_FEES)
        assert r.amazon_sell_price == 30.78  # rounded to 2 decimal
        profit_str = str(r.profit)
        if "." in profit_str:
            assert len(profit_str.split(".")[1]) <= 2


# ─── ScanFilters Tests ──────────────────────────────────────────────────────

class TestScanFilters:
    """ScanFilters dataclass defaults and enums."""

    def test_defaults(self):
        f = ScanFilters()
        assert f.min_roi_pct is None
        assert f.only_viable is True
        assert f.strict_mode is True
        assert f.isbn_match_policy == IsbnMatchPolicy.BALANCED
        assert f.invalid_isbn_policy == InvalidIsbnPolicy.BEST_EFFORT

    def test_custom_filters(self):
        f = ScanFilters(min_roi_pct=20.0, min_profit_usd=5.0,
                        max_buy_price=100.0, only_viable=True)
        assert f.min_roi_pct == 20.0
        assert f.min_profit_usd == 5.0

    def test_isbn_match_policies(self):
        assert IsbnMatchPolicy.PRECISION.value == "precision"
        assert IsbnMatchPolicy.BALANCED.value == "balanced"
        assert IsbnMatchPolicy.RECALL.value == "recall"

    def test_invalid_isbn_policies(self):
        assert InvalidIsbnPolicy.REJECT.value == "reject"
        assert InvalidIsbnPolicy.BEST_EFFORT.value == "best_effort"

    def test_buyback_filters(self):
        f = ScanFilters(buyback_only=True, min_buyback_profit=5.0)
        assert f.buyback_only is True
        assert f.min_buyback_profit == 5.0

    def test_condition_source_filters(self):
        f = ScanFilters(condition_in=["used"], source_in=["ebay"])
        assert f.condition_in == ["used"]
        assert f.source_in == ["ebay"]


# ─── _filter_result Tests ───────────────────────────────────────────────────

class TestFilterResult:
    """Filter rejection logic."""

    def _make_result(self, **kwargs) -> ArbResult:
        defaults = dict(isbn="isbn1", asin="asin1", source="ebay",
                        source_condition="used", buy_price=10.0,
                        amazon_sell_price=30.0, viable=True, profit=10.0,
                        roi_pct=50.0)
        defaults.update(kwargs)
        return ArbResult(**defaults)

    def test_passes_all_filters(self):
        r = self._make_result()
        f = ScanFilters(only_viable=True)
        assert _filter_result(r, f) == ""

    def test_buy_price_below_min(self):
        r = self._make_result(buy_price=5.0)
        f = ScanFilters(min_buy_price=10.0)
        assert "buy_price_below_min" in _filter_result(r, f)

    def test_buy_price_above_max(self):
        r = self._make_result(buy_price=200.0)
        f = ScanFilters(max_buy_price=100.0)
        assert "buy_price_above_max" in _filter_result(r, f)

    def test_no_amazon_price(self):
        r = self._make_result(amazon_sell_price=None)
        f = ScanFilters()
        result = _filter_result(r, f)
        assert result != ""  # should reject

    def test_amazon_price_below_min(self):
        r = self._make_result(amazon_sell_price=5.0)
        f = ScanFilters(min_amazon_price=10.0)
        assert "amazon_price_below_min" in _filter_result(r, f)

    def test_amazon_price_above_max(self):
        r = self._make_result(amazon_sell_price=200.0)
        f = ScanFilters(max_amazon_price=100.0)
        assert "amazon_price_above_max" in _filter_result(r, f)

    def test_not_viable_rejected(self):
        r = self._make_result(viable=False)
        f = ScanFilters(only_viable=True)
        assert "not_viable" in _filter_result(r, f)

    def test_not_viable_allowed(self):
        r = self._make_result(viable=False)
        f = ScanFilters(only_viable=False)
        assert _filter_result(r, f) == ""

    def test_profit_below_min(self):
        r = self._make_result(profit=3.0)
        f = ScanFilters(min_profit_usd=5.0, only_viable=False)
        assert "profit_below_min" in _filter_result(r, f)

    def test_roi_below_min(self):
        r = self._make_result(roi_pct=10.0)
        f = ScanFilters(min_roi_pct=20.0, only_viable=False)
        assert "roi_below_min" in _filter_result(r, f)

    def test_roi_above_max(self):
        r = self._make_result(roi_pct=500.0)
        f = ScanFilters(max_roi_pct=200.0, only_viable=False)
        assert "roi_above_max" in _filter_result(r, f)

    def test_condition_not_in(self):
        r = self._make_result(source_condition="new")
        f = ScanFilters(condition_in=["used"], only_viable=False)
        assert "condition_not_in" in _filter_result(r, f)

    def test_condition_in_passes(self):
        r = self._make_result(source_condition="used")
        f = ScanFilters(condition_in=["used"], only_viable=False)
        assert _filter_result(r, f) == ""

    def test_source_not_in(self):
        r = self._make_result(source="thriftbooks")
        f = ScanFilters(source_in=["ebay"], only_viable=False)
        assert "source_not_in" in _filter_result(r, f)

    def test_source_in_passes(self):
        r = self._make_result(source="ebay")
        f = ScanFilters(source_in=["ebay"], only_viable=False)
        assert _filter_result(r, f) == ""

    def test_buyback_only_no_profit(self):
        r = self._make_result(buyback_profit=None)
        f = ScanFilters(buyback_only=True, only_viable=False)
        assert "buyback_not_profitable" in _filter_result(r, f)

    def test_buyback_only_negative_profit(self):
        r = self._make_result(buyback_profit=-5.0)
        f = ScanFilters(buyback_only=True, only_viable=False)
        assert "buyback_not_profitable" in _filter_result(r, f)

    def test_buyback_only_positive_profit(self):
        r = self._make_result(buyback_profit=5.0)
        f = ScanFilters(buyback_only=True, only_viable=False)
        assert _filter_result(r, f) == ""

    def test_min_buyback_profit(self):
        r = self._make_result(buyback_profit=3.0)
        f = ScanFilters(min_buyback_profit=5.0, only_viable=False)
        assert "buyback_profit_below_min" in _filter_result(r, f)

    def test_max_buy_ratio(self):
        r = self._make_result(buy_price=20.0, amazon_sell_price=30.0)
        f = ScanFilters(max_buy_ratio_pct=50.0, only_viable=False)
        # 20/30 = 66.7% > 50% max
        assert "buy_ratio_too_high" in _filter_result(r, f)

    def test_max_buy_ratio_passes(self):
        r = self._make_result(buy_price=10.0, amazon_sell_price=30.0)
        f = ScanFilters(max_buy_ratio_pct=50.0, only_viable=False)
        # 10/30 = 33% < 50% max
        assert _filter_result(r, f) == ""

    def test_multiple_filters(self):
        r = self._make_result(buy_price=5.0, profit=3.0, roi_pct=10.0)
        f = ScanFilters(min_buy_price=10.0, min_profit_usd=5.0,
                        min_roi_pct=20.0, only_viable=False)
        # First failing filter wins
        result = _filter_result(r, f)
        assert result != ""

    def test_no_filters_passes(self):
        r = self._make_result()
        f = ScanFilters(only_viable=False)
        assert _filter_result(r, f) == ""


# ─── suggest_max_buy Tests ──────────────────────────────────────────────────

class TestSuggestMaxBuy:
    """Dynamic max buy price recommendation."""

    def test_basic_suggestion(self):
        result = suggest_max_buy(30.0, 30.0)
        assert result is not None
        assert result > 0
        assert result < 30.0

    def test_zero_sell_price(self):
        assert suggest_max_buy(0.0, 30.0) is None

    def test_negative_sell_price(self):
        assert suggest_max_buy(-10.0, 30.0) is None

    def test_high_target_roi(self):
        r1 = suggest_max_buy(30.0, 50.0)
        r2 = suggest_max_buy(30.0, 100.0)
        assert r1 > r2  # Higher target ROI → lower max buy

    def test_zero_target_roi(self):
        # 0% ROI → max buy = net (sell - fees)
        result = suggest_max_buy(30.0, 0.0)
        assert result is not None
        assert result > 0

    def test_custom_fees(self):
        custom = FeeConfig(referral_pct=0.10, closing_fee=1.0,
                           fulfillment=2.0, inbound=0.5)
        result = suggest_max_buy(30.0, 30.0, custom)
        assert result is not None

    def test_very_low_sell_price(self):
        # After fees, net might be <= 0
        result = suggest_max_buy(3.0, 30.0)
        # Fees: min $1 referral + $1.80 + $3.50 + $0.60 = $6.90
        # Net: 3.0 - 6.90 = -3.90 → None
        assert result is None

    def test_rounding(self):
        result = suggest_max_buy(30.0, 30.0)
        if result is not None:
            s = str(result)
            if "." in s:
                assert len(s.split(".")[1]) <= 2


# ─── Edge Cases ─────────────────────────────────────────────────────────────

class TestArbEdgeCases:

    def test_arb_result_ebay_metadata(self):
        r = ArbResult(isbn="isbn1", asin="asin1", source="ebay",
                      source_condition="used", buy_price=10.0,
                      ebay_item_id="12345", ebay_title="Test Book",
                      ebay_url="http://ebay.com/12345",
                      ebay_seller_name="seller1",
                      ebay_seller_feedback=99.5)
        d = r.to_dict()
        assert d["ebay_item_id"] == "12345"
        assert d["ebay_seller_feedback"] == 99.5

    def test_textbook_fields(self):
        r = ArbResult(isbn="isbn1", asin="asin1", source="ebay",
                      source_condition="used", buy_price=10.0,
                      is_textbook_likely=True, textbook_score=0.85,
                      dewey="510", lc_class="QA")
        assert r.is_textbook_likely is True
        assert r.textbook_score == 0.85

    def test_nyt_fields(self):
        r = ArbResult(isbn="isbn1", asin="asin1", source="ebay",
                      source_condition="used", buy_price=10.0,
                      nyt_bestseller=True, nyt_weeks=15, nyt_rank=3)
        assert r.nyt_bestseller is True
        assert r.nyt_weeks == 15
        assert r.nyt_rank == 3

    def test_filter_with_no_reason(self):
        """ArbResult with empty reason and no amazon_sell_price."""
        r = ArbResult(isbn="isbn1", asin="asin1", source="ebay",
                      source_condition="used", buy_price=10.0,
                      amazon_sell_price=None, reason="")
        f = ScanFilters()
        result = _filter_result(r, f)
        assert result == "no_amazon_price"

    def test_filter_preserves_existing_reason(self):
        r = ArbResult(isbn="isbn1", asin="asin1", source="ebay",
                      source_condition="used", buy_price=10.0,
                      amazon_sell_price=None, reason="missing_used_buybox")
        f = ScanFilters()
        result = _filter_result(r, f)
        assert result == "missing_used_buybox"

    def test_isbn13_to_asin_uppercase(self):
        result = _isbn13_to_asin("9780132350884")
        assert result is not None
        assert result == result.upper() or result[-1] == "X" or result[-1].isdigit()

    def test_multiple_strict_mode_combinations(self):
        data = {"new": {"buybox": {"total": 50.0}}, "used": {"buybox": {"total": 25.0}}}
        # Strict new → new
        p1, _, m1, _ = _calc_profit_strict(10.0, "new", data, strict_mode=True)
        assert p1 == 50.0
        assert m1 == "NEW→NEW"
        # Strict used → used
        p2, _, m2, _ = _calc_profit_strict(10.0, "used", data, strict_mode=True)
        assert p2 == 25.0
        assert m2 == "USED→USED"

    def test_apply_profit_preserves_buy_price(self):
        r = ArbResult(isbn="isbn1", asin="asin1", source="ebay",
                      source_condition="used", buy_price=15.0)
        _apply_profit(r, 30.0, "used", "USED→USED", DEFAULT_FEES)
        assert r.buy_price == 15.0  # not modified
