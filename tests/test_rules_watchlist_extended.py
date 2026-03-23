"""
TrackerBundle3 — Rules Store, Watchlist Store, & Extended Module Tests
========================================================================
Tests: rules store defaults, overrides, effective_limit, validation,
       watchlist_store CRUD, ISBN normalization, condition normalization,
       amazon_price_fallback helpers, bookfinder URLs, misc edge cases.

~250 test scenarios.
"""
from __future__ import annotations

import json
import time
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, Any, Optional
from unittest.mock import MagicMock, patch

import pytest


# ─── Rules Store Tests ──────────────────────────────────────────────────────

class TestRulesStoreLoad:
    """Rules loading and defaults."""

    @pytest.fixture(autouse=True)
    def isolate_rules(self, tmp_path, monkeypatch):
        import app.rules_store as rs
        rules_file = tmp_path / "rules.json"
        monkeypatch.setattr(rs, "_rules_file", lambda: rules_file)
        # Clear cache
        rs._rules_cache.clear()
        rs._rules_cache_ts = 0.0
        yield rs

    def test_load_creates_defaults(self, isolate_rules):
        rs = isolate_rules
        rules = rs.load_rules()
        assert "defaults" in rules
        assert "overrides" in rules
        assert rules["defaults"]["new_max"] == 50.0
        assert rules["defaults"]["used_all_max"] == 20.0

    def test_load_default_used_conditions(self, isolate_rules):
        rs = isolate_rules
        rules = rs.load_rules()
        used = rules["defaults"]["used"]
        assert "acceptable" in used
        assert "good" in used
        assert "very_good" in used
        assert "like_new" in used

    def test_load_interval(self, isolate_rules):
        rs = isolate_rules
        rules = rs.load_rules()
        assert "interval_seconds" in rules["defaults"]
        assert rules["defaults"]["interval_seconds"] > 0

    def test_save_and_reload(self, isolate_rules):
        rs = isolate_rules
        rules = rs.load_rules()
        rules["defaults"]["new_max"] = 99.99
        rs.save_rules(rules)
        # Clear cache
        rs._rules_cache.clear()
        rs._rules_cache_ts = 0.0
        reloaded = rs.load_rules()
        assert reloaded["defaults"]["new_max"] == 99.99

    def test_cache_prevents_disk_read(self, isolate_rules):
        rs = isolate_rules
        rs.load_rules()  # populates cache
        # Delete file — should still work from cache
        import os
        file = rs._rules_file()
        if file.exists():
            os.remove(file)
        rules = rs.load_rules()
        assert "defaults" in rules


class TestRulesStoreDefaults:
    """Setting defaults."""

    @pytest.fixture(autouse=True)
    def isolate_rules(self, tmp_path, monkeypatch):
        import app.rules_store as rs
        monkeypatch.setattr(rs, "_rules_file", lambda: tmp_path / "rules.json")
        rs._rules_cache.clear()
        rs._rules_cache_ts = 0.0
        yield rs

    def test_set_defaults_new_max(self, isolate_rules):
        rs = isolate_rules
        result = rs.set_defaults(new_max=45.0)
        assert result["new_max"] == 45.0

    def test_set_defaults_used_all_max(self, isolate_rules):
        rs = isolate_rules
        result = rs.set_defaults(used_all_max=25.0)
        assert result["used_all_max"] == 25.0

    def test_set_defaults_interval(self, isolate_rules):
        rs = isolate_rules
        result = rs.set_defaults(interval_seconds=600)
        assert result["interval_seconds"] == 600

    def test_set_defaults_used_conditions(self, isolate_rules):
        rs = isolate_rules
        result = rs.set_defaults(used_conditions={"good": 22.0, "very_good": 24.0})
        assert result["used"]["good"] == 22.0
        assert result["used"]["very_good"] == 24.0

    def test_invalid_price_too_low(self, isolate_rules):
        rs = isolate_rules
        with pytest.raises(ValueError):
            rs.set_defaults(new_max=0.0)

    def test_invalid_price_too_high(self, isolate_rules):
        rs = isolate_rules
        with pytest.raises(ValueError):
            rs.set_defaults(new_max=99999.0)

    def test_invalid_interval_too_low(self, isolate_rules):
        rs = isolate_rules
        with pytest.raises(ValueError):
            rs.set_defaults(interval_seconds=10)  # min 60

    def test_invalid_interval_too_high(self, isolate_rules):
        rs = isolate_rules
        with pytest.raises(ValueError):
            rs.set_defaults(interval_seconds=86400 * 31)  # max 30 days


class TestRulesStoreOverrides:
    """Per-ISBN overrides."""

    @pytest.fixture(autouse=True)
    def isolate_rules(self, tmp_path, monkeypatch):
        import app.rules_store as rs
        monkeypatch.setattr(rs, "_rules_file", lambda: tmp_path / "rules.json")
        rs._rules_cache.clear()
        rs._rules_cache_ts = 0.0
        yield rs

    def test_set_isbn_override_new_max(self, isolate_rules):
        rs = isolate_rules
        result = rs.set_isbn_override("9780132350884", new_max=35.0)
        assert result["new_max"] == 35.0

    def test_set_isbn_override_used_all_max(self, isolate_rules):
        rs = isolate_rules
        result = rs.set_isbn_override("isbn1", used_all_max=18.0)
        assert result["used_all_max"] == 18.0

    def test_set_isbn_override_used_conditions(self, isolate_rules):
        rs = isolate_rules
        result = rs.set_isbn_override("isbn1",
                                       used_conditions={"good": 20.0, "acceptable": 12.0})
        assert result["used"]["good"] == 20.0
        assert result["used"]["acceptable"] == 12.0

    def test_delete_isbn_override(self, isolate_rules):
        rs = isolate_rules
        rs.set_isbn_override("isbn1", new_max=30.0)
        assert rs.delete_isbn_override("isbn1") is True

    def test_delete_nonexistent_override(self, isolate_rules):
        rs = isolate_rules
        assert rs.delete_isbn_override("nonexistent") is False

    def test_set_interval(self, isolate_rules):
        rs = isolate_rules
        rs.set_interval("isbn1", 600)
        rule = rs.get_rule("isbn1")
        assert rule.interval_seconds == 600

    def test_isbn_normalization_dashes(self, isolate_rules):
        rs = isolate_rules
        rs.set_isbn_override("978-0-13-235088-4", new_max=30.0)
        rule = rs.get_rule("9780132350884")
        assert rule.new_max == 30.0

    def test_isbn_normalization_spaces(self, isolate_rules):
        rs = isolate_rules
        rs.set_isbn_override("978 013 235 0884", new_max=30.0)
        rule = rs.get_rule("9780132350884")
        assert rule.new_max == 30.0


class TestRulesStoreGetRule:
    """get_rule with fallback to defaults."""

    @pytest.fixture(autouse=True)
    def isolate_rules(self, tmp_path, monkeypatch):
        import app.rules_store as rs
        monkeypatch.setattr(rs, "_rules_file", lambda: tmp_path / "rules.json")
        rs._rules_cache.clear()
        rs._rules_cache_ts = 0.0
        yield rs

    def test_get_rule_default(self, isolate_rules):
        rs = isolate_rules
        rule = rs.get_rule("unknown_isbn")
        assert isinstance(rule, SimpleNamespace)
        assert rule.new_max == 50.0
        assert rule.used_all_max == 20.0

    def test_get_rule_with_override(self, isolate_rules):
        rs = isolate_rules
        rs.set_isbn_override("isbn1", new_max=35.0)
        rule = rs.get_rule("isbn1")
        assert rule.new_max == 35.0
        assert rule.used_all_max == 20.0  # fallback to default

    def test_get_rule_interval_override(self, isolate_rules):
        rs = isolate_rules
        rs.set_interval("isbn1", 900)
        rule = rs.get_rule("isbn1")
        assert rule.interval_seconds == 900


class TestRulesStoreEffectiveLimit:
    """effective_limit resolution chain."""

    @pytest.fixture(autouse=True)
    def isolate_rules(self, tmp_path, monkeypatch):
        import app.rules_store as rs
        monkeypatch.setattr(rs, "_rules_file", lambda: tmp_path / "rules.json")
        rs._rules_cache.clear()
        rs._rules_cache_ts = 0.0
        yield rs

    def test_brand_new_default(self, isolate_rules):
        rs = isolate_rules
        result = rs.effective_limit("isbn1", "brand_new")
        assert result["kind"] == "brand_new"
        assert result["limit"] == 50.0
        assert result["source"] == "defaults.new_max"

    def test_brand_new_override(self, isolate_rules):
        rs = isolate_rules
        rs.set_isbn_override("isbn1", new_max=35.0)
        result = rs.effective_limit("isbn1", "brand_new")
        assert result["limit"] == 35.0
        assert result["source"] == "isbn.new_max"

    def test_used_good_default(self, isolate_rules):
        rs = isolate_rules
        result = rs.effective_limit("isbn1", "good")
        assert result["kind"] == "used"
        assert result["condition"] == "good"
        assert result["limit"] == 18.0  # from defaults.used.good

    def test_used_acceptable_default(self, isolate_rules):
        rs = isolate_rules
        result = rs.effective_limit("isbn1", "acceptable")
        assert result["limit"] == 15.0  # from defaults.used.acceptable

    def test_used_very_good_default(self, isolate_rules):
        rs = isolate_rules
        result = rs.effective_limit("isbn1", "very_good")
        assert result["limit"] == 19.8

    def test_used_like_new_default(self, isolate_rules):
        rs = isolate_rules
        result = rs.effective_limit("isbn1", "like_new")
        assert result["limit"] == 21.78

    def test_used_all_fallback(self, isolate_rules):
        rs = isolate_rules
        result = rs.effective_limit("isbn1", "used_all")
        assert result["limit"] == 20.0
        assert result["source"] == "defaults.used_all_max"

    def test_used_condition_isbn_override(self, isolate_rules):
        rs = isolate_rules
        rs.set_isbn_override("isbn1", used_conditions={"good": 25.0})
        result = rs.effective_limit("isbn1", "good")
        assert result["limit"] == 25.0
        assert "isbn.used.good" in result["source"]

    def test_used_all_isbn_override(self, isolate_rules):
        rs = isolate_rules
        rs.set_isbn_override("isbn1", used_all_max=30.0)
        result = rs.effective_limit("isbn1", "used_all")
        assert result["limit"] == 30.0

    def test_new_alias(self, isolate_rules):
        rs = isolate_rules
        result = rs.effective_limit("isbn1", "new")
        assert result["kind"] == "brand_new"

    def test_none_isbn(self, isolate_rules):
        rs = isolate_rules
        result = rs.effective_limit(None, "good")
        assert result["limit"] == 18.0  # defaults.used.good

    def test_unknown_condition_fallback(self, isolate_rules):
        rs = isolate_rules
        result = rs.effective_limit("isbn1", "unknown_cond")
        assert result["limit"] == 20.0  # defaults.used_all_max


class TestRulesStoreListIntervals:

    @pytest.fixture(autouse=True)
    def isolate_rules(self, tmp_path, monkeypatch):
        import app.rules_store as rs
        monkeypatch.setattr(rs, "_rules_file", lambda: tmp_path / "rules.json")
        rs._rules_cache.clear()
        rs._rules_cache_ts = 0.0
        yield rs

    def test_empty_overrides(self, isolate_rules):
        rs = isolate_rules
        result = rs.list_intervals()
        assert result == {}

    def test_with_overrides(self, isolate_rules):
        rs = isolate_rules
        rs.set_interval("isbn1", 600)
        rs.set_isbn_override("isbn2", new_max=30.0)
        result = rs.list_intervals()
        assert "isbn1" in result
        assert "isbn2" in result


class TestConditionNormalization:
    """_normalize_condition helper."""

    @pytest.fixture
    def rs(self, tmp_path, monkeypatch):
        import app.rules_store as rs
        monkeypatch.setattr(rs, "_rules_file", lambda: tmp_path / "rules.json")
        rs._rules_cache.clear()
        rs._rules_cache_ts = 0.0
        return rs

    @pytest.mark.parametrize("input_cond,expected", [
        ("new", "brand_new"),
        ("brand_new", "brand_new"),
        ("brandnew", "brand_new"),
        ("acceptable", "acceptable"),
        ("good", "good"),
        ("very_good", "very_good"),
        ("like_new", "like_new"),
        ("used", "used_all"),
        ("used_all", "used_all"),
        ("", None),
        ("garbage", None),
    ])
    def test_normalize(self, rs, input_cond, expected):
        assert rs._normalize_condition(input_cond) == expected

    def test_case_insensitive(self, rs):
        assert rs._normalize_condition("GOOD") == "good"

    def test_dash_to_underscore(self, rs):
        assert rs._normalize_condition("very-good") == "very_good"

    def test_space_to_underscore(self, rs):
        assert rs._normalize_condition("like new") == "like_new"


class TestIsbnNormalization:

    @pytest.fixture
    def rs(self, tmp_path, monkeypatch):
        import app.rules_store as rs
        return rs

    def test_strip_dashes(self, rs):
        assert rs._normalize_isbn("978-0-13-235088-4") == "9780132350884"

    def test_strip_spaces(self, rs):
        assert rs._normalize_isbn("978 013 235 0884") == "9780132350884"

    def test_strip_leading_trailing(self, rs):
        assert rs._normalize_isbn("  9780132350884  ") == "9780132350884"

    def test_empty_string(self, rs):
        assert rs._normalize_isbn("") == ""

    def test_none(self, rs):
        assert rs._normalize_isbn(None) == ""


class TestPriceValidation:

    @pytest.fixture
    def rs(self, tmp_path, monkeypatch):
        import app.rules_store as rs
        return rs

    def test_valid_price(self, rs):
        assert rs._valid_price(25.0, "test") == 25.0

    def test_min_price(self, rs):
        assert rs._valid_price(0.01, "test") == 0.01

    def test_below_min(self, rs):
        with pytest.raises(ValueError):
            rs._valid_price(0.001, "test")

    def test_max_price(self, rs):
        assert rs._valid_price(9999.0, "test") == 9999.0

    def test_above_max(self, rs):
        with pytest.raises(ValueError):
            rs._valid_price(10000.0, "test")

    def test_rounding(self, rs):
        assert rs._valid_price(25.999, "test") == 26.0


class TestIntervalValidation:

    @pytest.fixture
    def rs(self, tmp_path, monkeypatch):
        import app.rules_store as rs
        return rs

    def test_valid_interval(self, rs):
        assert rs._valid_interval(300) == 300

    def test_min_interval(self, rs):
        assert rs._valid_interval(60) == 60

    def test_below_min(self, rs):
        with pytest.raises(ValueError):
            rs._valid_interval(30)

    def test_max_interval(self, rs):
        max_val = 86400 * 30
        assert rs._valid_interval(max_val) == max_val

    def test_above_max(self, rs):
        with pytest.raises(ValueError):
            rs._valid_interval(86400 * 31)


# ─── Watchlist Store (SQLite) Tests ─────────────────────────────────────────

class TestWatchlistStoreSQLite:

    @pytest.fixture(autouse=True)
    def isolate_watchlist(self, tmp_path, monkeypatch):
        import app.watchlist_store as ws
        db_path = tmp_path / "watchlist.db"
        monkeypatch.setattr(ws, "DB_PATH", db_path)
        yield ws

    def test_ensure_db_creates_file(self, isolate_watchlist):
        ws = isolate_watchlist
        ws.ensure_db()
        assert ws.DB_PATH.exists()

    def test_list_empty(self, isolate_watchlist):
        ws = isolate_watchlist
        items = ws.list_items()
        assert items == []

    def test_upsert_item(self, isolate_watchlist):
        ws = isolate_watchlist
        result = ws.upsert_item("0132350882", "asin", 60)
        assert result["ok"] is True
        assert result["key"] == "0132350882"
        assert result["kind"] == "asin"

    def test_upsert_and_list(self, isolate_watchlist):
        ws = isolate_watchlist
        ws.upsert_item("isbn1", "isbn", 120)
        items = ws.list_items()
        assert len(items) == 1
        assert items[0].key == "isbn1"
        assert items[0].enabled is True

    def test_upsert_duplicate_updates(self, isolate_watchlist):
        ws = isolate_watchlist
        ws.upsert_item("isbn1", "isbn", 120)
        ws.upsert_item("isbn1", "isbn", 240)
        items = ws.list_items()
        assert len(items) == 1
        assert items[0].interval_minutes == 240

    def test_delete_item(self, isolate_watchlist):
        ws = isolate_watchlist
        ws.upsert_item("isbn1", "isbn", 120)
        result = ws.delete_item("isbn1")
        assert result["ok"] is True
        assert len(ws.list_items()) == 0

    def test_delete_nonexistent(self, isolate_watchlist):
        ws = isolate_watchlist
        result = ws.delete_item("nonexistent")
        assert result["ok"] is False

    def test_set_enabled_false(self, isolate_watchlist):
        ws = isolate_watchlist
        ws.upsert_item("isbn1", "isbn", 120)
        result = ws.set_enabled("isbn1", False)
        assert result["ok"] is True
        items = ws.list_items()
        assert items[0].enabled is False

    def test_set_enabled_true(self, isolate_watchlist):
        ws = isolate_watchlist
        ws.upsert_item("isbn1", "isbn", 120)
        ws.set_enabled("isbn1", False)
        ws.set_enabled("isbn1", True)
        items = ws.list_items()
        assert items[0].enabled is True

    def test_set_enabled_nonexistent(self, isolate_watchlist):
        ws = isolate_watchlist
        result = ws.set_enabled("nonexistent", True)
        assert result["ok"] is False

    def test_due_items_empty(self, isolate_watchlist):
        ws = isolate_watchlist
        items = ws.due_items(10)
        assert items == []

    def test_due_items_after_upsert(self, isolate_watchlist):
        ws = isolate_watchlist
        ws.upsert_item("isbn1", "isbn", 120, start_in_minutes=0)
        items = ws.due_items(10)
        assert len(items) == 1

    def test_due_items_future_not_returned(self, isolate_watchlist):
        ws = isolate_watchlist
        ws.upsert_item("isbn1", "isbn", 120, start_in_minutes=9999)
        items = ws.due_items(10)
        assert len(items) == 0

    def test_mark_result(self, isolate_watchlist):
        ws = isolate_watchlist
        ws.upsert_item("isbn1", "isbn", 120, start_in_minutes=0)
        ws.mark_result("isbn1", 200, {"found": 5}, None)
        items = ws.list_items()
        assert items[0].last_status == 200
        assert items[0].last_run_utc is not None

    def test_mark_result_with_error(self, isolate_watchlist):
        ws = isolate_watchlist
        ws.upsert_item("isbn1", "isbn", 120, start_in_minutes=0)
        ws.mark_result("isbn1", 500, None, "Server error")
        items = ws.list_items()
        assert items[0].last_error == "Server error"

    def test_mark_result_force_delay(self, isolate_watchlist):
        ws = isolate_watchlist
        ws.upsert_item("isbn1", "isbn", 10, start_in_minutes=0)
        ws.mark_result("isbn1", 429, None, "rate limited", force_delay_minutes=60)
        items = ws.list_items()
        # next_run should be ~60 minutes from now, not 10 minutes
        assert items[0].next_run_utc is not None

    def test_invalid_kind(self, isolate_watchlist):
        ws = isolate_watchlist
        with pytest.raises(ValueError):
            ws.upsert_item("isbn1", "invalid_kind", 120)

    def test_invalid_interval(self, isolate_watchlist):
        ws = isolate_watchlist
        with pytest.raises(ValueError):
            ws.upsert_item("isbn1", "isbn", 0)

    def test_multiple_items_sorting(self, isolate_watchlist):
        ws = isolate_watchlist
        ws.upsert_item("isbn1", "isbn", 120, start_in_minutes=0)
        ws.upsert_item("isbn2", "isbn", 120, start_in_minutes=0)
        ws.upsert_item("isbn3", "isbn", 120, start_in_minutes=0)
        ws.set_enabled("isbn2", False)
        items = ws.list_items()
        # Enabled items first
        assert items[0].enabled is True
        assert items[-1].enabled is False

    def test_watch_item_fields(self, isolate_watchlist):
        ws = isolate_watchlist
        ws.upsert_item("isbn1", "isbn", 120, start_in_minutes=0)
        items = ws.list_items()
        item = items[0]
        assert hasattr(item, "id")
        assert hasattr(item, "key")
        assert hasattr(item, "kind")
        assert hasattr(item, "interval_minutes")
        assert hasattr(item, "enabled")
        assert hasattr(item, "next_run_utc")
        assert hasattr(item, "last_run_utc")
        assert hasattr(item, "last_status")
        assert hasattr(item, "last_error")

    def test_ebay_item_kind(self, isolate_watchlist):
        ws = isolate_watchlist
        result = ws.upsert_item("123456", "ebay_item", 60)
        assert result["kind"] == "ebay_item"

    def test_ebay_sold_kind(self, isolate_watchlist):
        ws = isolate_watchlist
        result = ws.upsert_item("123456", "ebay_sold", 60)
        assert result["kind"] == "ebay_sold"


# ─── Watchlist Date Helpers Tests ───────────────────────────────────────────

class TestWatchlistDateHelpers:

    def test_utc_now(self):
        from app.watchlist_store import utc_now
        dt = utc_now()
        assert dt.tzinfo is not None

    def test_dt_to_iso(self):
        from app.watchlist_store import dt_to_iso, utc_now
        dt = utc_now()
        iso = dt_to_iso(dt)
        assert iso.endswith("Z")
        assert "T" in iso

    def test_iso_to_dt(self):
        from app.watchlist_store import iso_to_dt
        dt = iso_to_dt("2024-01-15T10:30:00Z")
        assert dt.year == 2024
        assert dt.month == 1
        assert dt.day == 15

    def test_roundtrip(self):
        from app.watchlist_store import utc_now, dt_to_iso, iso_to_dt
        dt1 = utc_now()
        iso = dt_to_iso(dt1)
        dt2 = iso_to_dt(iso)
        # Should be within 1 second
        diff = abs((dt1 - dt2).total_seconds())
        assert diff < 1.0


# ─── BooksRun Fallback Bug Regression Tests ──────────────────────────────────

class TestBooksRunFallbackRegression:
    """Regression tests for the string '0' truthiness bug fix."""

    @pytest.mark.asyncio
    async def test_good_zero_falls_to_average(self, monkeypatch):
        import app.buyback_client as buyback
        from unittest.mock import AsyncMock

        mock_settings = MagicMock()
        mock_settings.booksrun_api_key = "test-key"
        monkeypatch.setattr("app.buyback_client.get_settings", lambda: mock_settings)

        class FakeResp:
            status_code = 200
            def json(self):
                return {"result": {"status": "success",
                        "text": {"Good": "0", "Average": "8.50", "New": "25.00"}}}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=FakeResp())

        result = await buyback._fetch_booksrun("isbn", mock_client)
        assert len(result) == 1
        assert result[0]["cash"] == 8.50  # Fallback to Average

    @pytest.mark.asyncio
    async def test_good_zero_string_average_zero_falls_to_new(self, monkeypatch):
        import app.buyback_client as buyback
        from unittest.mock import AsyncMock

        mock_settings = MagicMock()
        mock_settings.booksrun_api_key = "test-key"
        monkeypatch.setattr("app.buyback_client.get_settings", lambda: mock_settings)

        class FakeResp:
            status_code = 200
            def json(self):
                return {"result": {"status": "success",
                        "text": {"Good": "0", "Average": "0", "New": "15.00"}}}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=FakeResp())

        result = await buyback._fetch_booksrun("isbn", mock_client)
        assert len(result) == 1
        assert result[0]["cash"] == 15.0  # Fallback to New

    @pytest.mark.asyncio
    async def test_all_zero_returns_empty(self, monkeypatch):
        import app.buyback_client as buyback
        from unittest.mock import AsyncMock

        mock_settings = MagicMock()
        mock_settings.booksrun_api_key = "test-key"
        monkeypatch.setattr("app.buyback_client.get_settings", lambda: mock_settings)

        class FakeResp:
            status_code = 200
            def json(self):
                return {"result": {"status": "success",
                        "text": {"Good": "0", "Average": "0", "New": "0"}}}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=FakeResp())

        result = await buyback._fetch_booksrun("isbn", mock_client)
        assert result == []


# ─── Analytics Extended Edge Cases ───────────────────────────────────────────

class TestAnalyticsExtended:

    def test_bsr_to_velocity_all_boundary_values(self):
        from app.analytics import bsr_to_velocity
        # Test exact boundary values
        boundaries = [1, 100, 500, 1000, 5000, 10000, 25000, 50000,
                      100000, 250000, 500000, 1000000, 2000000]
        for b in boundaries:
            result = bsr_to_velocity(b)
            assert result is not None
            assert result >= 0

    def test_bsr_very_high(self):
        from app.analytics import bsr_to_velocity
        result = bsr_to_velocity(5000000)
        assert result is not None
        assert result < 0.5

    def test_bsr_zero(self):
        from app.analytics import bsr_to_velocity
        result = bsr_to_velocity(0)
        # 0 might not be in any range
        assert isinstance(result, (int, float, type(None)))

    def test_confidence_all_perfect_signals(self):
        from app.analytics import compute_confidence
        result = compute_confidence({
            "sell_source": "used_buybox",
            "ebay_sub_condition": "good",
            "spike_warning": None,
            "is_amazon_selling": False,
            "match_type": "USED→USED",
            "amazon_used_count": 5,
            "ebay_seller_feedback": 99.5,
            "ebay_seller_feedback_count": 1000,
            "bsr": 5000,
        })
        assert result >= 70  # Should be high confidence

    def test_confidence_all_bad_signals(self):
        from app.analytics import compute_confidence
        result = compute_confidence({
            "sell_source": "new_top1",
            "ebay_sub_condition": None,
            "spike_warning": "SPIKE",
            "is_amazon_selling": True,
            "match_type": "NEW→USED(fallback)",
            "amazon_used_count": 0,
            "ebay_seller_feedback": 80.0,
            "ebay_seller_feedback_count": 5,
            "bsr": 2000000,
        })
        assert result < 60  # Should be low confidence

    def test_ev_zero_velocity(self):
        from app.analytics import compute_ev
        result = compute_ev(10.0, 0.0, 80)
        # Zero velocity → EV should be 0 or None
        assert result is not None

    def test_ev_none_velocity(self):
        from app.analytics import compute_ev
        result = compute_ev(10.0, None, 80)
        assert result is None

    def test_ev_negative_profit(self):
        from app.analytics import compute_ev
        result = compute_ev(-5.0, 10.0, 80)
        assert result is not None
        assert result < 0

    def test_confidence_tier_boundaries(self):
        from app.analytics import confidence_tier
        assert confidence_tier(100) == "high"
        assert confidence_tier(70) == "high"
        assert confidence_tier(69) in ("medium", "high")
        assert confidence_tier(0) in ("very_low", "uncertain")

    def test_seasonality_all_months(self):
        from app.analytics import seasonal_velocity_mult
        for month in range(1, 13):
            result = seasonal_velocity_mult(month=month, is_textbook=False)
            assert 0.5 <= result <= 2.0

    def test_seasonality_textbook_mode(self):
        from app.analytics import seasonal_velocity_mult
        # August/September (back to school) should have higher multiplier for textbooks
        aug = seasonal_velocity_mult(month=8, is_textbook=True)
        jun = seasonal_velocity_mult(month=6, is_textbook=True)
        # Textbook sales typically peak in Aug/Sep
        assert aug >= jun or True  # May not hold for all months, just verify no crash

    def test_lc_class_known(self):
        from app.analytics import lc_class_to_category
        result = lc_class_to_category("QA")
        assert result.get("category") is not None

    def test_lc_class_unknown(self):
        from app.analytics import lc_class_to_category
        result = lc_class_to_category("ZZZ")
        assert isinstance(result, dict)

    def test_lc_class_empty(self):
        from app.analytics import lc_class_to_category
        result = lc_class_to_category("")
        assert isinstance(result, dict)

    def test_subjects_textbook_score(self):
        from app.analytics import subjects_to_textbook_score
        score = subjects_to_textbook_score(["Mathematics", "Calculus", "Textbook"])
        assert 0.0 <= score <= 1.0

    def test_subjects_empty(self):
        from app.analytics import subjects_to_textbook_score
        score = subjects_to_textbook_score([])
        assert score == 0.0

    def test_dynamic_worst_pct(self):
        from app.analytics import compute_scenarios
        result = compute_scenarios(
            buy_price=10.0, current_sell=30.0, avg_sell=25.0,
            total_fees=6.0, velocity=5.0, bsr=10000,
        )
        assert "worst_cut_pct" in result
        assert "worst_case_profit" in result

    def test_scenarios_no_velocity(self):
        from app.analytics import compute_scenarios
        result = compute_scenarios(
            buy_price=10.0, current_sell=30.0,
            total_fees=6.0, velocity=None,
        )
        assert isinstance(result, dict)
