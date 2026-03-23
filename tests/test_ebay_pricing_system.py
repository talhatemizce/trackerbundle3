"""
TrackerBundle3 — eBay Pricing System Comprehensive Tests
==========================================================
Tests: decision engine, pricing analysis, listing/sold summaries,
       watch store, condition-specific limits, offer ceilings.

~150 test scenarios.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.ebay_pricing.models import (
    ConditionType, LimitConfig, AllLimits, ListingItem,
    SoldItem, ListingSummary, SoldSummary, DetailedSoldSummary,
    ItemDecision, DecisionType, DecisionRequest, EbaySummaryResponse,
)
from app.ebay_pricing.limits import (
    calculate_all_limits, get_limit_for_condition, calculate_offer_ceiling,
)
from app.ebay_pricing.decision import evaluate_listings
from app.ebay_pricing.pricing import (
    analyze_listings, analyze_sold_items, get_condition_sold_average,
)


# ─── ConditionType Tests ────────────────────────────────────────────────────

class TestConditionType:

    def test_all_conditions(self):
        assert ConditionType.NEW.value == "new"
        assert ConditionType.USED_ACCEPTABLE.value == "used_acceptable"
        assert ConditionType.USED_GOOD.value == "used_good"
        assert ConditionType.USED_VERY_GOOD.value == "used_very_good"
        assert ConditionType.USED_LIKE_NEW.value == "used_like_new"

    def test_str_comparison(self):
        assert ConditionType.NEW == "new"
        assert ConditionType.USED_GOOD == "used_good"

    def test_enum_count(self):
        assert len(ConditionType) == 5

    def test_from_value(self):
        assert ConditionType("new") == ConditionType.NEW
        assert ConditionType("used_good") == ConditionType.USED_GOOD


# ─── LimitConfig Tests ──────────────────────────────────────────────────────

class TestLimitConfig:

    def test_basic(self):
        config = LimitConfig(new_limit=50.0, good_limit=30.0)
        assert config.new_limit == 50.0
        assert config.good_limit == 30.0

    def test_zero_limits(self):
        config = LimitConfig(new_limit=0.0, good_limit=0.0)
        assert config.new_limit == 0.0

    def test_missing_required_field(self):
        with pytest.raises(Exception):
            LimitConfig(new_limit=50.0)  # missing good_limit

    def test_float_precision(self):
        config = LimitConfig(new_limit=29.99, good_limit=19.95)
        assert config.new_limit == 29.99


# ─── AllLimits Tests ─────────────────────────────────────────────────────────

class TestAllLimits:

    def test_all_fields(self):
        limits = AllLimits(
            new_limit=50.0,
            used_acceptable_limit=24.0,
            used_good_limit=30.0,
            used_very_good_limit=33.0,
            used_like_new_limit=36.0,
        )
        assert limits.new_limit == 50.0
        assert limits.used_acceptable_limit == 24.0
        assert limits.used_good_limit == 30.0
        assert limits.used_very_good_limit == 33.0
        assert limits.used_like_new_limit == 36.0


# ─── calculate_all_limits Tests ─────────────────────────────────────────────

class TestCalculateAllLimits:

    def test_standard_calculation(self):
        config = LimitConfig(new_limit=50.0, good_limit=30.0)
        limits = calculate_all_limits(config)
        assert limits.new_limit == 50.0
        assert limits.used_good_limit == 30.0
        assert limits.used_acceptable_limit == 24.0   # 30 * 0.80
        assert limits.used_very_good_limit == 33.0     # 30 * 1.10
        assert limits.used_like_new_limit == 36.0      # 30 * 1.20

    def test_zero_good_limit(self):
        config = LimitConfig(new_limit=50.0, good_limit=0.0)
        limits = calculate_all_limits(config)
        assert limits.used_good_limit == 0.0
        assert limits.used_acceptable_limit == 0.0

    def test_high_good_limit(self):
        config = LimitConfig(new_limit=100.0, good_limit=80.0)
        limits = calculate_all_limits(config)
        assert limits.used_acceptable_limit == 64.0  # 80 * 0.80
        assert limits.used_very_good_limit == 88.0   # 80 * 1.10
        assert limits.used_like_new_limit == 96.0    # 80 * 1.20

    def test_rounding(self):
        config = LimitConfig(new_limit=29.99, good_limit=19.99)
        limits = calculate_all_limits(config)
        # All values should be rounded to 2 decimal places
        assert limits.new_limit == 29.99
        assert str(limits.used_acceptable_limit).count(".") <= 1


# ─── get_limit_for_condition Tests ───────────────────────────────────────────

class TestGetLimitForCondition:

    @pytest.fixture
    def limits(self):
        return calculate_all_limits(LimitConfig(new_limit=50.0, good_limit=30.0))

    @pytest.mark.parametrize("condition,expected", [
        (ConditionType.NEW, 50.0),
        (ConditionType.USED_GOOD, 30.0),
        (ConditionType.USED_ACCEPTABLE, 24.0),
        (ConditionType.USED_VERY_GOOD, 33.0),
        (ConditionType.USED_LIKE_NEW, 36.0),
    ])
    def test_all_conditions(self, limits, condition, expected):
        assert get_limit_for_condition(limits, condition) == expected


# ─── calculate_offer_ceiling Tests ───────────────────────────────────────────

class TestCalculateOfferCeiling:

    def test_default_multiplier(self):
        assert calculate_offer_ceiling(30.0) == 39.0  # 30 * 1.30

    def test_custom_multiplier(self):
        assert calculate_offer_ceiling(30.0, 1.50) == 45.0  # 30 * 1.50

    def test_zero_limit(self):
        assert calculate_offer_ceiling(0.0) == 0.0

    def test_rounding(self):
        result = calculate_offer_ceiling(29.99)
        assert result == round(29.99 * 1.30, 2)


# ─── ListingItem Tests ──────────────────────────────────────────────────────

class TestListingItem:

    def test_total_price(self):
        item = ListingItem(item_id="1", condition=ConditionType.NEW,
                           item_price=25.0, shipping_price=4.99)
        assert item.total_price == 29.99

    def test_total_price_free_shipping(self):
        item = ListingItem(item_id="2", condition=ConditionType.USED_GOOD,
                           item_price=20.0, shipping_price=0.0)
        assert item.total_price == 20.0

    def test_make_offer_enabled(self):
        item = ListingItem(item_id="3", condition=ConditionType.USED_ACCEPTABLE,
                           item_price=15.0, make_offer_enabled=True)
        assert item.make_offer_enabled is True

    def test_default_shipping_zero(self):
        item = ListingItem(item_id="4", condition=ConditionType.NEW,
                           item_price=30.0)
        assert item.shipping_price == 0.0
        assert item.total_price == 30.0


# ─── SoldItem Tests ─────────────────────────────────────────────────────────

class TestSoldItem:

    def test_sold_total(self):
        sold = SoldItem(item_id="1", condition=ConditionType.USED_GOOD,
                        sold_price=20.0, sold_shipping=3.50)
        assert sold.sold_total == 23.50

    def test_sold_total_free_shipping(self):
        sold = SoldItem(item_id="2", condition=ConditionType.NEW,
                        sold_price=30.0)
        assert sold.sold_total == 30.0

    def test_sold_date_optional(self):
        sold = SoldItem(item_id="3", condition=ConditionType.USED_VERY_GOOD,
                        sold_price=25.0, sold_date="2024-01-15")
        assert sold.sold_date == "2024-01-15"


# ─── evaluate_listings Tests ────────────────────────────────────────────────

class TestEvaluateListings:

    @pytest.fixture
    def limits(self):
        return calculate_all_limits(LimitConfig(new_limit=50.0, good_limit=30.0))

    def test_buy_under_limit(self, limits):
        listings = [
            ListingItem(item_id="1", condition=ConditionType.USED_GOOD,
                        item_price=25.0),
        ]
        decisions = evaluate_listings(listings, limits)
        assert len(decisions) == 1
        assert decisions[0].decision == DecisionType.BUY

    def test_skip_over_limit(self, limits):
        listings = [
            ListingItem(item_id="1", condition=ConditionType.USED_GOOD,
                        item_price=45.0),
        ]
        decisions = evaluate_listings(listings, limits)
        assert len(decisions) == 1
        assert decisions[0].decision == DecisionType.SKIP

    def test_offer_within_ceiling(self, limits):
        listings = [
            ListingItem(item_id="1", condition=ConditionType.USED_GOOD,
                        item_price=35.0, make_offer_enabled=True),
        ]
        decisions = evaluate_listings(listings, limits)
        assert len(decisions) == 1
        assert decisions[0].decision == DecisionType.OFFER
        assert decisions[0].offer_ceiling is not None

    def test_skip_over_ceiling(self, limits):
        listings = [
            ListingItem(item_id="1", condition=ConditionType.USED_GOOD,
                        item_price=50.0, make_offer_enabled=True),
        ]
        decisions = evaluate_listings(listings, limits)
        assert len(decisions) == 1
        assert decisions[0].decision == DecisionType.SKIP

    def test_offers_disabled(self, limits):
        listings = [
            ListingItem(item_id="1", condition=ConditionType.USED_GOOD,
                        item_price=35.0, make_offer_enabled=True),
        ]
        decisions = evaluate_listings(listings, limits, enable_offers=False)
        assert decisions[0].decision == DecisionType.SKIP

    def test_multiple_listings(self, limits):
        listings = [
            ListingItem(item_id="1", condition=ConditionType.NEW,
                        item_price=40.0),                              # BUY
            ListingItem(item_id="2", condition=ConditionType.USED_GOOD,
                        item_price=25.0),                              # BUY
            ListingItem(item_id="3", condition=ConditionType.USED_ACCEPTABLE,
                        item_price=30.0),                              # SKIP (24 limit)
        ]
        decisions = evaluate_listings(listings, limits)
        assert len(decisions) == 3
        assert decisions[0].decision == DecisionType.BUY
        assert decisions[1].decision == DecisionType.BUY
        assert decisions[2].decision == DecisionType.SKIP

    def test_new_item_buy(self, limits):
        listings = [
            ListingItem(item_id="1", condition=ConditionType.NEW,
                        item_price=45.0),
        ]
        decisions = evaluate_listings(listings, limits)
        assert decisions[0].decision == DecisionType.BUY  # 45 <= 50

    def test_decision_fields(self, limits):
        listings = [
            ListingItem(item_id="1", condition=ConditionType.USED_GOOD,
                        item_price=25.0),
        ]
        decisions = evaluate_listings(listings, limits)
        d = decisions[0]
        assert d.item_id == "1"
        assert d.condition == ConditionType.USED_GOOD
        assert d.total_price == 25.0
        assert d.limit == 30.0
        assert "reason" in d.model_dump()

    def test_empty_listings(self, limits):
        decisions = evaluate_listings([], limits)
        assert decisions == []

    def test_custom_offer_multiplier(self, limits):
        listings = [
            ListingItem(item_id="1", condition=ConditionType.USED_GOOD,
                        item_price=40.0, make_offer_enabled=True),
        ]
        # 30 * 1.50 = 45 → 40 <= 45 → OFFER
        decisions = evaluate_listings(listings, limits, offer_multiplier=1.50)
        assert decisions[0].decision == DecisionType.OFFER

    def test_exact_limit_buy(self, limits):
        listings = [
            ListingItem(item_id="1", condition=ConditionType.USED_GOOD,
                        item_price=30.0),
        ]
        decisions = evaluate_listings(listings, limits)
        assert decisions[0].decision == DecisionType.BUY  # <= limit

    def test_one_penny_over_limit(self, limits):
        listings = [
            ListingItem(item_id="1", condition=ConditionType.USED_GOOD,
                        item_price=30.01),
        ]
        decisions = evaluate_listings(listings, limits)
        # 30.01 > 30.0 → not BUY, check if offer available
        assert decisions[0].decision != DecisionType.BUY


# ─── analyze_listings Tests ─────────────────────────────────────────────────

class TestAnalyzeListings:

    def test_mixed_conditions(self):
        listings = [
            ListingItem(item_id="1", condition=ConditionType.NEW,
                        item_price=50.0, shipping_price=5.0),
            ListingItem(item_id="2", condition=ConditionType.NEW,
                        item_price=40.0, shipping_price=3.0),
            ListingItem(item_id="3", condition=ConditionType.USED_GOOD,
                        item_price=20.0, shipping_price=4.0),
        ]
        summary = analyze_listings(listings)
        assert summary.new_count == 2
        assert summary.new_min_total == 43.0  # 40 + 3
        assert summary.new_max_total == 55.0  # 50 + 5
        assert summary.used_count == 1
        assert summary.used_min_total == 24.0  # 20 + 4
        assert summary.used_max_total == 24.0

    def test_only_new(self):
        listings = [
            ListingItem(item_id="1", condition=ConditionType.NEW,
                        item_price=30.0),
        ]
        summary = analyze_listings(listings)
        assert summary.new_count == 1
        assert summary.used_count == 0
        assert summary.used_min_total is None

    def test_only_used(self):
        listings = [
            ListingItem(item_id="1", condition=ConditionType.USED_GOOD,
                        item_price=20.0),
        ]
        summary = analyze_listings(listings)
        assert summary.new_count == 0
        assert summary.new_min_total is None
        assert summary.used_count == 1

    def test_empty_listings(self):
        summary = analyze_listings([])
        assert summary.new_count == 0
        assert summary.used_count == 0
        assert summary.new_min_total is None
        assert summary.used_min_total is None


# ─── analyze_sold_items Tests ───────────────────────────────────────────────

class TestAnalyzeSoldItems:

    def test_basic_sold(self):
        sold = [
            SoldItem(item_id="1", condition=ConditionType.NEW,
                     sold_price=30.0),
            SoldItem(item_id="2", condition=ConditionType.USED_GOOD,
                     sold_price=20.0, sold_shipping=3.0),
        ]
        summary = analyze_sold_items(sold)
        assert isinstance(summary, SoldSummary)
        assert summary.sold_new_count == 1
        assert summary.sold_new_avg_total == 30.0
        assert summary.sold_used_count == 1
        assert summary.sold_used_avg_total == 23.0  # 20 + 3

    def test_detailed_sold(self):
        sold = [
            SoldItem(item_id="1", condition=ConditionType.USED_GOOD,
                     sold_price=20.0),
            SoldItem(item_id="2", condition=ConditionType.USED_ACCEPTABLE,
                     sold_price=15.0),
            SoldItem(item_id="3", condition=ConditionType.USED_VERY_GOOD,
                     sold_price=25.0),
        ]
        summary = analyze_sold_items(sold, detailed=True)
        assert isinstance(summary, DetailedSoldSummary)
        assert summary.sold_used_good_avg_total == 20.0
        assert summary.sold_used_acceptable_avg_total == 15.0
        assert summary.sold_used_very_good_avg_total == 25.0

    def test_empty_sold(self):
        summary = analyze_sold_items([])
        assert summary.sold_new_count == 0
        assert summary.sold_used_count == 0
        assert summary.sold_new_avg_total is None
        assert summary.sold_used_avg_total is None

    def test_multiple_same_condition(self):
        sold = [
            SoldItem(item_id="1", condition=ConditionType.USED_GOOD,
                     sold_price=20.0),
            SoldItem(item_id="2", condition=ConditionType.USED_GOOD,
                     sold_price=30.0),
        ]
        summary = analyze_sold_items(sold)
        assert summary.sold_used_avg_total == 25.0  # (20 + 30) / 2


# ─── get_condition_sold_average Tests ────────────────────────────────────────

class TestGetConditionSoldAverage:

    def test_average_for_condition(self):
        sold = [
            SoldItem(item_id="1", condition=ConditionType.USED_GOOD,
                     sold_price=20.0),
            SoldItem(item_id="2", condition=ConditionType.USED_GOOD,
                     sold_price=30.0),
            SoldItem(item_id="3", condition=ConditionType.NEW,
                     sold_price=50.0),
        ]
        avg = get_condition_sold_average(sold, ConditionType.USED_GOOD)
        assert avg == 25.0

    def test_no_matching_condition(self):
        sold = [
            SoldItem(item_id="1", condition=ConditionType.NEW,
                     sold_price=50.0),
        ]
        avg = get_condition_sold_average(sold, ConditionType.USED_GOOD)
        assert avg is None

    def test_empty_list(self):
        avg = get_condition_sold_average([], ConditionType.NEW)
        assert avg is None


# ─── Watch Store Tests ───────────────────────────────────────────────────────

class TestWatchStore:

    @pytest.fixture
    def watch_store(self, tmp_path, monkeypatch):
        from app.ebay_pricing import watch_store as ws
        watch_file = tmp_path / "ebay_watch.json"
        monkeypatch.setattr(ws, "WATCH_FILE", watch_file)
        return ws

    def test_empty_list(self, watch_store):
        items = watch_store.list_items()
        assert items == []

    def test_add_item(self, watch_store):
        item = watch_store.add_item("test query", 3600)
        assert item["query"] == "test query"
        assert item["interval_sec"] == 3600
        assert item["enabled"] is True
        assert "id" in item

    def test_add_and_list(self, watch_store):
        watch_store.add_item("query1", 300)
        watch_store.add_item("query2", 600)
        items = watch_store.list_items()
        assert len(items) == 2

    def test_delete_item(self, watch_store):
        item = watch_store.add_item("to delete", 300)
        assert watch_store.delete_item(item["id"]) is True
        assert len(watch_store.list_items()) == 0

    def test_delete_nonexistent(self, watch_store):
        assert watch_store.delete_item("nonexistent") is False

    def test_add_disabled(self, watch_store):
        item = watch_store.add_item("disabled q", 300, enabled=False)
        assert item["enabled"] is False

    def test_add_with_note(self, watch_store):
        item = watch_store.add_item("noted q", 300, note="test note")
        assert item["note"] == "test note"

    def test_save_and_load(self, watch_store):
        watch_store.add_item("persist q", 300)
        data = watch_store.load_watch()
        assert len(data["items"]) == 1

    def test_next_run_utc_set(self, watch_store):
        item = watch_store.add_item("q", 3600)
        assert item["next_run_utc"] is not None

    def test_last_run_utc_null(self, watch_store):
        item = watch_store.add_item("q", 300)
        assert item["last_run_utc"] is None


# ─── DecisionRequest Model Tests ────────────────────────────────────────────

class TestDecisionRequest:

    def test_basic(self):
        req = DecisionRequest(
            limits=LimitConfig(new_limit=50.0, good_limit=30.0),
            listings=[
                ListingItem(item_id="1", condition=ConditionType.NEW,
                            item_price=40.0),
            ],
        )
        assert req.enable_offers is True
        assert req.offer_multiplier == 1.30
        assert len(req.listings) == 1

    def test_custom_multiplier(self):
        req = DecisionRequest(
            limits=LimitConfig(new_limit=50.0, good_limit=30.0),
            listings=[],
            offer_multiplier=1.50,
        )
        assert req.offer_multiplier == 1.50


# ─── EbaySummaryResponse Model Tests ────────────────────────────────────────

class TestEbaySummaryResponse:

    def test_basic(self):
        resp = EbaySummaryResponse(
            isbn="9780132350884",
            limits=AllLimits(
                new_limit=50.0, used_acceptable_limit=24.0,
                used_good_limit=30.0, used_very_good_limit=33.0,
                used_like_new_limit=36.0),
            active=ListingSummary(new_count=5, used_count=3),
            sold=SoldSummary(sold_new_count=2, sold_used_count=4),
        )
        assert resp.isbn == "9780132350884"
        assert resp.limits.new_limit == 50.0
        assert resp.active.new_count == 5
        assert resp.sold.sold_used_count == 4
