"""
rules_store.py için kapsamlı testler (daha önce hiç test edilmemiş).
Validation, normalization, effective_limit, get_rule, set_defaults,
set_isbn_override, delete_isbn_override, list_intervals, caching.
"""
from __future__ import annotations
import json
import pytest
from pathlib import Path

# rules_store'u izole etmek için gerçek dosya yerine tmp_path kullanıyoruz
import app.rules_store as rs
from app.rules_store import (
    _valid_price, _valid_interval, _normalize_isbn, _normalize_condition,
    USED_CONDITIONS,
)


@pytest.fixture(autouse=True)
def isolate_rules(tmp_path, monkeypatch):
    """Her test kendi tmp dizininde çalışır — gerçek rules.json korunur."""
    rules_file = tmp_path / "rules.json"
    monkeypatch.setattr(rs, "_rules_file", lambda: rules_file)
    # Cache'i temizle
    rs._rules_cache.clear()
    rs._rules_cache_ts = 0.0
    yield
    rs._rules_cache.clear()
    rs._rules_cache_ts = 0.0


# ── _valid_price() ────────────────────────────────────────────────────────────

class TestValidPrice:
    def test_valid_price_accepted(self):
        assert _valid_price(25.0, "new_max") == 25.0

    def test_price_rounded_to_2_decimals(self):
        assert _valid_price(25.999, "x") == 26.0

    def test_minimum_price_accepted(self):
        assert _valid_price(0.01, "x") == 0.01

    def test_maximum_price_accepted(self):
        assert _valid_price(9999.0, "x") == 9999.0

    def test_below_minimum_raises(self):
        with pytest.raises(ValueError):
            _valid_price(0.0, "x")

    def test_above_maximum_raises(self):
        with pytest.raises(ValueError):
            _valid_price(10000.0, "x")

    def test_negative_raises(self):
        with pytest.raises(ValueError):
            _valid_price(-1.0, "x")

    def test_string_float_accepted(self):
        result = _valid_price("25.50", "x")
        assert result == 25.50


# ── _valid_interval() ─────────────────────────────────────────────────────────

class TestValidInterval:
    def test_valid_interval_accepted(self):
        assert _valid_interval(300) == 300

    def test_minimum_interval(self):
        assert _valid_interval(60) == 60

    def test_maximum_interval(self):
        max_val = 86400 * 30
        assert _valid_interval(max_val) == max_val

    def test_below_minimum_raises(self):
        with pytest.raises(ValueError):
            _valid_interval(59)

    def test_above_maximum_raises(self):
        with pytest.raises(ValueError):
            _valid_interval(86400 * 30 + 1)

    def test_string_int_accepted(self):
        result = _valid_interval("3600")
        assert result == 3600

    def test_zero_raises(self):
        with pytest.raises(ValueError):
            _valid_interval(0)


# ── _normalize_isbn() ─────────────────────────────────────────────────────────

class TestNormalizeIsbn:
    def test_removes_hyphens(self):
        assert _normalize_isbn("978-0-13-235088-4") == "9780132350884"

    def test_removes_spaces(self):
        assert _normalize_isbn("978 013 235088 4") == "9780132350884"

    def test_strips_whitespace(self):
        assert _normalize_isbn("  0132350882  ") == "0132350882"

    def test_empty_string(self):
        assert _normalize_isbn("") == ""

    def test_none_handled(self):
        assert _normalize_isbn(None) == ""

    def test_already_normalized(self):
        assert _normalize_isbn("9780132350884") == "9780132350884"


# ── _normalize_condition() ───────────────────────────────────────────────────

class TestNormalizeCondition:
    def test_new_returns_brand_new(self):
        assert _normalize_condition("new") == "brand_new"

    def test_brand_new_returns_brand_new(self):
        assert _normalize_condition("brand_new") == "brand_new"

    def test_brandnew_returns_brand_new(self):
        assert _normalize_condition("brandnew") == "brand_new"

    def test_acceptable_returned(self):
        assert _normalize_condition("acceptable") == "acceptable"

    def test_good_returned(self):
        assert _normalize_condition("good") == "good"

    def test_very_good_returned(self):
        assert _normalize_condition("very_good") == "very_good"

    def test_like_new_returned(self):
        assert _normalize_condition("like_new") == "like_new"

    def test_used_returns_used_all(self):
        assert _normalize_condition("used") == "used_all"

    def test_used_all_returns_used_all(self):
        assert _normalize_condition("used_all") == "used_all"

    def test_unknown_returns_none(self):
        assert _normalize_condition("damaged") is None

    def test_empty_returns_none(self):
        assert _normalize_condition("") is None

    def test_none_returns_none(self):
        assert _normalize_condition(None) is None

    def test_case_insensitive(self):
        assert _normalize_condition("GOOD") == "good"

    def test_hyphen_to_underscore(self):
        assert _normalize_condition("very-good") == "very_good"

    def test_space_to_underscore(self):
        assert _normalize_condition("very good") == "very_good"


# ── load_rules() / save_rules() ──────────────────────────────────────────────

class TestLoadSaveRules:
    def test_load_creates_default_when_missing(self):
        rules = rs.load_rules()
        assert "defaults" in rules
        assert "overrides" in rules

    def test_default_new_max(self):
        rules = rs.load_rules()
        assert rules["defaults"]["new_max"] == 50.0

    def test_default_used_all_max(self):
        rules = rs.load_rules()
        assert rules["defaults"]["used_all_max"] == 20.0

    def test_save_and_load_roundtrip(self):
        custom_rules = {
            "defaults": {"new_max": 75.0, "used_all_max": 30.0, "interval_seconds": 600, "used": {}},
            "overrides": {}
        }
        rs.save_rules(custom_rules)
        loaded = rs.load_rules()
        assert loaded["defaults"]["new_max"] == 75.0

    def test_cache_used_on_second_call(self):
        rs.load_rules()  # fills cache
        # Modify file directly — cache should still return old value
        rules_path = rs._rules_file()
        if rules_path.exists():
            data = json.loads(rules_path.read_text())
            data["defaults"]["new_max"] = 999.0
            rules_path.write_text(json.dumps(data))
        # Within TTL, cache returns old value
        cached = rs.load_rules()
        assert cached["defaults"]["new_max"] != 999.0


# ── effective_limit() ─────────────────────────────────────────────────────────

class TestEffectiveLimit:
    def test_new_condition_default_limit(self):
        result = rs.effective_limit(None, "new")
        assert result["kind"] == "brand_new"
        assert result["limit"] == 50.0

    def test_used_condition_default_limit(self):
        result = rs.effective_limit(None, "good")
        assert result["kind"] == "used"

    def test_used_all_fallback(self):
        result = rs.effective_limit(None, "used")
        assert result["limit"] == 20.0

    def test_isbn_override_new_max(self):
        rs.set_isbn_override("9780132350884", new_max=30.0)
        result = rs.effective_limit("9780132350884", "new")
        assert result["limit"] == 30.0
        assert result["source"] == "isbn.new_max"

    def test_isbn_override_used_specific_condition(self):
        rs.set_isbn_override("9780132350884", used_conditions={"good": 15.0})
        result = rs.effective_limit("9780132350884", "good")
        assert result["limit"] == 15.0

    def test_isbn_override_fallback_to_defaults(self):
        # Override exists but doesn't specify used_all_max
        rs.set_isbn_override("9780132350884", new_max=30.0)
        result = rs.effective_limit("9780132350884", "good")
        # Falls back to defaults.used.good
        assert result["limit"] == rs.load_rules()["defaults"]["used"]["good"]

    def test_isbn_override_used_all_max(self):
        rs.set_isbn_override("9780132350884", used_all_max=12.0)
        result = rs.effective_limit("9780132350884", "used")
        assert result["limit"] == 12.0

    def test_isbn_with_hyphens_normalized(self):
        rs.set_isbn_override("9780132350884", new_max=25.0)
        result = rs.effective_limit("978-0-13-235088-4", "new")
        assert result["limit"] == 25.0

    def test_unknown_condition_falls_back_to_used_all_max(self):
        result = rs.effective_limit(None, "unknown_cond")
        assert result["kind"] == "used"
        assert result["limit"] > 0


# ── get_rule() ────────────────────────────────────────────────────────────────

class TestGetRule:
    def test_defaults_when_no_override(self):
        rule = rs.get_rule("9780132350884")
        assert rule.new_max == 50.0
        assert rule.used_all_max == 20.0
        assert rule.interval_seconds == rs.load_rules()["defaults"]["interval_seconds"]

    def test_override_new_max(self):
        rs.set_isbn_override("9780132350884", new_max=35.0)
        rule = rs.get_rule("9780132350884")
        assert rule.new_max == 35.0

    def test_override_used_all_max(self):
        rs.set_isbn_override("9780132350884", used_all_max=8.0)
        rule = rs.get_rule("9780132350884")
        assert rule.used_all_max == 8.0

    def test_rule_has_interval_attribute(self):
        rule = rs.get_rule("9780132350884")
        assert hasattr(rule, "interval_seconds")

    def test_rule_has_new_max_attribute(self):
        rule = rs.get_rule("9780132350884")
        assert hasattr(rule, "new_max")

    def test_rule_has_used_all_max_attribute(self):
        rule = rs.get_rule("9780132350884")
        assert hasattr(rule, "used_all_max")


# ── set_defaults() ────────────────────────────────────────────────────────────

class TestSetDefaults:
    def test_set_new_max(self):
        rs.set_defaults(new_max=60.0)
        rules = rs.load_rules()
        assert rules["defaults"]["new_max"] == 60.0

    def test_set_used_all_max(self):
        rs.set_defaults(used_all_max=25.0)
        rules = rs.load_rules()
        assert rules["defaults"]["used_all_max"] == 25.0

    def test_set_interval(self):
        rs.set_defaults(interval_seconds=600)
        rules = rs.load_rules()
        assert rules["defaults"]["interval_seconds"] == 600

    def test_set_used_conditions(self):
        rs.set_defaults(used_conditions={"good": 20.0, "acceptable": 12.0})
        rules = rs.load_rules()
        assert rules["defaults"]["used"]["good"] == 20.0
        assert rules["defaults"]["used"]["acceptable"] == 12.0

    def test_returns_defaults_dict(self):
        result = rs.set_defaults(new_max=55.0)
        assert isinstance(result, dict)
        assert result["new_max"] == 55.0

    def test_invalid_price_raises(self):
        with pytest.raises(ValueError):
            rs.set_defaults(new_max=0.0)

    def test_invalid_interval_raises(self):
        with pytest.raises(ValueError):
            rs.set_defaults(interval_seconds=10)

    def test_unknown_condition_ignored(self):
        rs.set_defaults(used_conditions={"nonexistent_cond": 10.0})
        rules = rs.load_rules()
        # unknown condition should not be saved
        assert "nonexistent_cond" not in rules["defaults"].get("used", {})


# ── set_isbn_override() ───────────────────────────────────────────────────────

class TestSetIsbnOverride:
    def test_set_new_max_override(self):
        rs.set_isbn_override("0132350882", new_max=20.0)
        rules = rs.load_rules()
        assert rules["overrides"]["0132350882"]["new_max"] == 20.0

    def test_isbn_normalized_in_override(self):
        rs.set_isbn_override("978-0-13-235088-4", new_max=40.0)
        rules = rs.load_rules()
        assert "9780132350884" in rules["overrides"]

    def test_set_used_conditions_override(self):
        rs.set_isbn_override("0132350882", used_conditions={"good": 10.0})
        rules = rs.load_rules()
        assert rules["overrides"]["0132350882"]["used"]["good"] == 10.0

    def test_returns_override_dict(self):
        result = rs.set_isbn_override("0132350882", new_max=15.0)
        assert isinstance(result, dict)
        assert result["new_max"] == 15.0

    def test_multiple_overrides_stored(self):
        rs.set_isbn_override("0132350882", new_max=15.0)
        rs.set_isbn_override("9780060185398", new_max=25.0)
        rules = rs.load_rules()
        assert "0132350882" in rules["overrides"]
        assert "9780060185398" in rules["overrides"]

    def test_invalid_price_raises(self):
        with pytest.raises(ValueError):
            rs.set_isbn_override("0132350882", new_max=-5.0)


# ── set_interval() ───────────────────────────────────────────────────────────

class TestSetInterval:
    def test_set_interval_persisted(self):
        rs.set_interval("0132350882", 1800)
        rules = rs.load_rules()
        assert rules["overrides"]["0132350882"]["interval_seconds"] == 1800

    def test_invalid_interval_raises(self):
        with pytest.raises(ValueError):
            rs.set_interval("0132350882", 30)

    def test_existing_override_not_overwritten(self):
        rs.set_isbn_override("0132350882", new_max=20.0)
        rs.set_interval("0132350882", 900)
        rules = rs.load_rules()
        # both override and interval should exist
        assert rules["overrides"]["0132350882"]["new_max"] == 20.0
        assert rules["overrides"]["0132350882"]["interval_seconds"] == 900


# ── delete_isbn_override() ───────────────────────────────────────────────────

class TestDeleteIsbnOverride:
    def test_delete_existing_override(self):
        rs.set_isbn_override("0132350882", new_max=15.0)
        result = rs.delete_isbn_override("0132350882")
        assert result is True
        rules = rs.load_rules()
        assert "0132350882" not in rules["overrides"]

    def test_delete_nonexistent_returns_false(self):
        result = rs.delete_isbn_override("0000000000")
        assert result is False

    def test_delete_with_hyphens(self):
        rs.set_isbn_override("9780132350884", new_max=40.0)
        result = rs.delete_isbn_override("978-0-13-235088-4")
        assert result is True

    def test_delete_does_not_affect_others(self):
        rs.set_isbn_override("0132350882", new_max=15.0)
        rs.set_isbn_override("0060185392", new_max=25.0)
        rs.delete_isbn_override("0132350882")
        rules = rs.load_rules()
        assert "0060185392" in rules["overrides"]


# ── list_intervals() ─────────────────────────────────────────────────────────

class TestListIntervals:
    def test_empty_when_no_overrides(self):
        result = rs.list_intervals()
        assert result == {}

    def test_isbn_with_override_present(self):
        rs.set_isbn_override("0132350882", new_max=15.0)
        result = rs.list_intervals()
        assert "0132350882" in result

    def test_interval_seconds_present(self):
        rs.set_interval("0132350882", 600)
        result = rs.list_intervals()
        assert result["0132350882"]["interval_seconds"] == 600

    def test_new_max_present(self):
        rs.set_isbn_override("0132350882", new_max=22.0)
        result = rs.list_intervals()
        assert result["0132350882"]["new_max"] == 22.0

    def test_used_all_max_present(self):
        rs.set_isbn_override("0132350882", used_all_max=12.0)
        result = rs.list_intervals()
        assert result["0132350882"]["used_all_max"] == 12.0

    def test_no_override_new_max_is_none(self):
        # Only set interval, not new_max
        rs.set_interval("0132350882", 300)
        result = rs.list_intervals()
        assert result["0132350882"]["new_max"] is None

    def test_multiple_isbns(self):
        rs.set_isbn_override("0132350882", new_max=15.0)
        rs.set_isbn_override("0060185392", new_max=20.0)
        result = rs.list_intervals()
        assert len(result) == 2
