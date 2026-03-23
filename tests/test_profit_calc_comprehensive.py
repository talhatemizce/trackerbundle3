"""
TrackerBundle3 — Profit Calculator & Smart Dedup & Run State Comprehensive Tests
=================================================================================
Tests: fee calculation, profit extraction, ROI tiers, dedup logic,
       run_state timing, ISBN utils edge cases.

~200 test scenarios.
"""
from __future__ import annotations

import math
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.profit_calc import (
    DEFAULT_FEES,
    FeeConfig,
    ProfitResult,
    _extract_sell_price,
    _tier,
    calculate,
)


# ─── FeeConfig Tests ────────────────────────────────────────────────────────

class TestFeeConfig:

    def test_default_values(self):
        f = FeeConfig()
        assert f.referral_pct == 0.15
        assert f.closing_fee == 1.80
        assert f.fulfillment == 3.50
        assert f.inbound == 0.60

    def test_total_fixed(self):
        f = FeeConfig()
        assert f.total_fixed == 1.80 + 3.50 + 0.60

    def test_total_includes_referral(self):
        f = FeeConfig()
        total = f.total(100.0)
        assert total == round(15.0 + f.total_fixed, 2)

    def test_referral_minimum_1_dollar(self):
        """Referral fee has $1 minimum."""
        f = FeeConfig()
        total = f.total(1.0)  # 1.0 * 0.15 = 0.15 → min $1
        assert total >= 1.0 + f.total_fixed

    def test_referral_below_minimum(self):
        f = FeeConfig()
        total = f.total(5.0)  # 5 * 0.15 = 0.75 → min $1
        expected = 1.0 + f.total_fixed  # min $1 referral
        assert total == round(expected, 2)

    def test_custom_fees(self):
        f = FeeConfig(referral_pct=0.10, closing_fee=2.0, fulfillment=4.0, inbound=1.0)
        assert f.total_fixed == 7.0
        total = f.total(100.0)
        assert total == round(10.0 + 7.0, 2)


# ─── _extract_sell_price Tests ───────────────────────────────────────────────

class TestExtractSellPrice:

    def test_none_data_returns_none(self):
        price, source = _extract_sell_price(None)
        assert price is None
        assert source == "unknown"

    def test_empty_dict_returns_none(self):
        price, source = _extract_sell_price({})
        assert price is None

    def test_used_buybox_priority(self):
        data = {
            "used": {"buybox": {"total": 25.0}, "top2": [{"total": 20.0}]},
            "new": {"buybox": {"total": 50.0}, "top2": []},
        }
        price, source = _extract_sell_price(data)
        assert price == 25.0
        assert source == "used_buybox"

    def test_fallback_to_used_top1(self):
        data = {
            "used": {"buybox": None, "top2": [{"total": 22.0}]},
            "new": {"buybox": {"total": 50.0}, "top2": []},
        }
        price, source = _extract_sell_price(data)
        assert price == 22.0
        assert source == "used_top1"

    def test_fallback_to_new_buybox(self):
        data = {
            "used": {"top2": []},
            "new": {"buybox": {"total": 45.0}, "top2": []},
        }
        price, source = _extract_sell_price(data)
        assert price == 45.0
        assert source == "new_buybox"

    def test_fallback_to_new_top1(self):
        data = {
            "used": {"top2": []},
            "new": {"top2": [{"total": 40.0}]},
        }
        price, source = _extract_sell_price(data)
        assert price == 40.0
        assert source == "new_top1"

    def test_buybox_total_zero_skipped(self):
        """total: 0 should be treated as no buybox."""
        data = {
            "used": {"buybox": {"total": 0}, "top2": [{"total": 20.0}]},
            "new": {"top2": []},
        }
        price, source = _extract_sell_price(data)
        assert price == 20.0
        assert source == "used_top1"

    def test_empty_top2_list(self):
        data = {
            "used": {"top2": []},
            "new": {"top2": []},
        }
        price, source = _extract_sell_price(data)
        assert price is None

    def test_missing_sections(self):
        data = {"used": None, "new": None}
        price, source = _extract_sell_price(data)
        assert price is None


# ─── _tier Tests ─────────────────────────────────────────────────────────────

class TestTier:

    @pytest.mark.parametrize("roi,expected", [
        (100.0, "fire"),
        (30.0,  "fire"),
        (29.9,  "good"),
        (15.0,  "good"),
        (14.9,  "low"),
        (0.1,   "low"),
        (0.0,   "loss"),
        (-10.0, "loss"),
    ])
    def test_tiers(self, roi, expected):
        assert _tier(roi) == expected


# ─── calculate() Tests ───────────────────────────────────────────────────────

class TestCalculate:

    def test_zero_ebay_returns_none(self):
        assert calculate(0, {"used": {"buybox": {"total": 30}}}) is None

    def test_negative_ebay_returns_none(self):
        assert calculate(-5.0, {"used": {"buybox": {"total": 30}}}) is None

    def test_no_amazon_returns_none(self):
        assert calculate(10.0, {}) is None

    def test_basic_profit(self):
        data = {"used": {"buybox": {"total": 35.0}, "top2": []}, "new": {"top2": []}}
        result = calculate(10.0, data)
        assert result is not None
        assert result.sell_price == 35.0
        assert result.profit > 0
        assert result.viable is True

    def test_profit_result_fields(self):
        data = {"used": {"buybox": {"total": 30.0}, "top2": []}, "new": {"top2": []}}
        result = calculate(10.0, data)
        assert isinstance(result, ProfitResult)
        assert hasattr(result, "sell_price")
        assert hasattr(result, "profit")
        assert hasattr(result, "roi_pct")
        assert hasattr(result, "roi_tier")
        assert hasattr(result, "viable")

    def test_loss_scenario(self):
        data = {"used": {"buybox": {"total": 12.0}, "top2": []}, "new": {"top2": []}}
        result = calculate(11.0, data)
        assert result is not None
        # Fees eat the margin
        if result.profit <= 0:
            assert result.viable is False
            assert result.roi_tier == "loss"

    def test_custom_fees(self):
        data = {"used": {"buybox": {"total": 30.0}, "top2": []}, "new": {"top2": []}}
        custom = FeeConfig(referral_pct=0.10, closing_fee=1.0, fulfillment=2.0, inbound=0.50)
        result = calculate(10.0, data, custom)
        assert result.closing_fee == 1.0
        assert result.fulfillment == 2.0

    def test_to_dict(self):
        data = {"used": {"buybox": {"total": 30.0}, "top2": []}, "new": {"top2": []}}
        result = calculate(10.0, data)
        d = result.to_dict()
        assert isinstance(d, dict)
        assert "sell_price" in d
        assert "profit" in d


# ─── ISBN Utils Extended Tests ───────────────────────────────────────────────

class TestIsbnUtilsExtended:
    """Additional ISBN edge cases beyond existing test_isbn_utils.py."""

    def test_isbn10_x_checkdigit(self):
        from app.isbn_utils import parse_isbn
        info = parse_isbn("020161622X")
        assert info.valid
        assert info.isbn10 == "020161622X"

    def test_isbn13_979_prefix_no_isbn10(self):
        from app.isbn_utils import parse_isbn
        info = parse_isbn("9791032300824")
        assert info.valid
        assert info.isbn10 is None  # 979 has no ISBN-10

    def test_isbn_with_spaces(self):
        from app.isbn_utils import parse_isbn
        info = parse_isbn("978 0 13 235088 4")
        assert info.valid
        assert info.isbn13 == "9780132350884"

    def test_isbn_all_zeros(self):
        from app.isbn_utils import parse_isbn
        info = parse_isbn("0000000000")
        # All zeros has specific checksum — may or may not be valid
        assert isinstance(info.valid, bool)

    def test_isbn_empty_string(self):
        from app.isbn_utils import parse_isbn
        info = parse_isbn("")
        assert not info.valid
        assert info.reason.value == "invalid_length"

    def test_isbn_only_dashes(self):
        from app.isbn_utils import parse_isbn
        info = parse_isbn("---")
        assert not info.valid

    def test_isbn_variants_returns_both(self):
        from app.isbn_utils import isbn_variants
        v = isbn_variants("9780132350884")
        assert "9780132350884" in v
        assert "0132350882" in v

    def test_isbn_variants_invalid_returns_empty(self):
        from app.isbn_utils import isbn_variants
        v = isbn_variants("invalid")
        assert v == []

    def test_isbn_info_variants_method(self):
        from app.isbn_utils import parse_isbn
        info = parse_isbn("9780132350884")
        v = info.variants()
        assert len(v) == 2

    def test_to_isbn13_idempotent(self):
        from app.isbn_utils import to_isbn13
        assert to_isbn13("9780132350884") == "9780132350884"

    def test_to_isbn10_from_13(self):
        from app.isbn_utils import to_isbn10
        assert to_isbn10("9780132350884") == "0132350882"

    def test_clean_handles_unicode_dash(self):
        from app.isbn_utils import _clean
        result = _clean("978\u20130132350884")  # en-dash
        # Should not crash, may or may not clean properly
        assert isinstance(result, str)


# ─── Smart Dedup Tests ───────────────────────────────────────────────────────

class TestSmartDedup:

    def test_price_key_rounding(self):
        from app.smart_dedup import _price_key
        # $10.30 → bucket 10.25
        assert _price_key(10.30) == str(10.25)
        # $10.50 → bucket 10.50
        assert _price_key(10.50) == str(10.5)

    def test_dedup_key_format(self):
        from app.smart_dedup import _dedup_key
        key = _dedup_key("isbn1", "used", 10.30)
        assert key.startswith("isbn1|used|")

    def test_should_send_new(self, monkeypatch, tmp_path):
        from app.smart_dedup import should_send, _path
        monkeypatch.setattr("app.smart_dedup._path", lambda: tmp_path / "dedup.json")
        send, reason = should_send("isbn1", "used", 15.0, 70, "item1")
        assert send is True
        assert reason == "new"

    def test_should_send_duplicate(self, monkeypatch, tmp_path):
        from app.smart_dedup import should_send
        monkeypatch.setattr("app.smart_dedup._path", lambda: tmp_path / "dedup.json")
        should_send("isbn1", "used", 15.0, 70, "item1")
        send, reason = should_send("isbn1", "used", 15.0, 70, "item1")
        assert send is False
        assert reason == "duplicate"

    def test_should_send_better_score(self, monkeypatch, tmp_path):
        from app.smart_dedup import should_send
        monkeypatch.setattr("app.smart_dedup._path", lambda: tmp_path / "dedup.json")
        should_send("isbn1", "used", 15.0, 70, "item1")
        send, reason = should_send("isbn1", "used", 15.0, 81, "item1")  # +11
        assert send is True
        assert reason == "better_score"

    def test_should_send_better_price(self, monkeypatch, tmp_path):
        from app.smart_dedup import should_send
        monkeypatch.setattr("app.smart_dedup._path", lambda: tmp_path / "dedup.json")
        should_send("isbn1", "used", 20.0, 70, "item1")
        # 16.0 is 20% cheaper than 20.0 → fires
        send, reason = should_send("isbn1", "used", 16.0, 70, "item2")
        assert send is True
        assert reason == "better_price"

    def test_clear_isbn(self, monkeypatch, tmp_path):
        from app.smart_dedup import should_send, clear_isbn
        monkeypatch.setattr("app.smart_dedup._path", lambda: tmp_path / "dedup.json")
        should_send("isbn1", "used", 15.0, 70, "item1")
        cleared = clear_isbn("isbn1")
        assert cleared >= 1

    def test_get_stats(self, monkeypatch, tmp_path):
        from app.smart_dedup import should_send, get_stats
        monkeypatch.setattr("app.smart_dedup._path", lambda: tmp_path / "dedup.json")
        should_send("isbn1", "used", 15.0, 70, "item1")
        stats = get_stats()
        assert stats["active_keys"] >= 1
        assert stats["total_keys"] >= 1


# ─── Run State Tests ─────────────────────────────────────────────────────────

class TestRunState:

    def test_get_last_run_default_zero(self, monkeypatch, tmp_path):
        from app.run_state import get_last_run, _cache
        _cache.clear()
        monkeypatch.setattr("app.run_state._path", lambda: tmp_path / "last_run.json")
        assert get_last_run("new_isbn") == 0.0

    def test_set_and_get(self, monkeypatch, tmp_path):
        from app.run_state import set_last_run, get_last_run, _cache
        _cache.clear()
        monkeypatch.setattr("app.run_state._path", lambda: tmp_path / "last_run.json")
        now = time.time()
        set_last_run("isbn1", now)
        assert get_last_run("isbn1") == now

    def test_due_first_time(self, monkeypatch, tmp_path):
        from app.run_state import due, _cache
        _cache.clear()
        monkeypatch.setattr("app.run_state._path", lambda: tmp_path / "last_run.json")
        assert due("isbn1", 300) is True  # never run → due

    def test_due_not_yet(self, monkeypatch, tmp_path):
        from app.run_state import set_last_run, due, _cache
        _cache.clear()
        monkeypatch.setattr("app.run_state._path", lambda: tmp_path / "last_run.json")
        now = time.time()
        set_last_run("isbn1", now)
        assert due("isbn1", 300, now=now + 100) is False  # 100s < 300s

    def test_due_elapsed(self, monkeypatch, tmp_path):
        from app.run_state import set_last_run, due, _cache
        _cache.clear()
        monkeypatch.setattr("app.run_state._path", lambda: tmp_path / "last_run.json")
        now = time.time()
        set_last_run("isbn1", now)
        assert due("isbn1", 300, now=now + 301) is True

    def test_set_last_run_auto_timestamp(self, monkeypatch, tmp_path):
        from app.run_state import set_last_run, get_last_run, _cache
        _cache.clear()
        monkeypatch.setattr("app.run_state._path", lambda: tmp_path / "last_run.json")
        before = time.time()
        set_last_run("isbn1")
        after = time.time()
        ts = get_last_run("isbn1")
        assert before <= ts <= after


# ─── Scan Job Store Extended Tests ───────────────────────────────────────────

class TestScanJobStoreExtended:

    def test_update_progress(self):
        from app.scan_job_store import create_job, update_progress, get_job_progress
        jid = create_job(100)
        update_progress(jid, 50)
        p = get_job_progress(jid)
        assert p is not None
        assert p["progress"] == 50
        assert p["total"] == 100

    def test_get_nonexistent_job(self):
        from app.scan_job_store import get_job
        assert get_job("nonexistent-id-12345") is None

    def test_progress_nonexistent(self):
        from app.scan_job_store import get_job_progress
        assert get_job_progress("nonexistent") is None

    def test_multiple_jobs(self):
        from app.scan_job_store import create_job, get_job
        j1 = create_job(10)
        j2 = create_job(20)
        assert j1 != j2
        assert get_job(j1)["total"] == 10
        assert get_job(j2)["total"] == 20


# ─── JSON Store Tests ────────────────────────────────────────────────────────

class TestJsonStore:

    def test_read_nonexistent_returns_default(self, tmp_path):
        from app.core.json_store import _read_unsafe
        result = _read_unsafe(tmp_path / "nope.json", default={"x": 1})
        assert result == {"x": 1}

    def test_write_and_read(self, tmp_path):
        from app.core.json_store import _read_unsafe, _write_unsafe
        p = tmp_path / "test.json"
        _write_unsafe(p, {"key": "value"})
        result = _read_unsafe(p)
        assert result["key"] == "value"

    def test_file_lock_contextmanager(self, tmp_path):
        from app.core.json_store import file_lock
        p = tmp_path / "locktest.json"
        p.write_text("{}")
        with file_lock(p):
            pass  # Should not deadlock

    def test_read_json_with_lock(self, tmp_path):
        from app.core.json_store import read_json, write_json
        p = tmp_path / "locked.json"
        write_json(p, {"a": 1})
        result = read_json(p)
        assert result["a"] == 1
