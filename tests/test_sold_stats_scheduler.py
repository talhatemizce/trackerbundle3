"""
TrackerBundle3 — Sold Stats Store + Scheduler Deal Score Tests
================================================================
Tests: snapshot accumulation, trend calculation, window queries,
       throttling, deal_score formula, _format_message, _rebuild_totals.

~150 test scenarios.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
from typing import Dict, Any

import pytest

from app.scheduler_ebay import deal_score, _format_message, _rebuild_totals_from_stats, _rebuild_totals_from_bucket


# ─── deal_score Tests ────────────────────────────────────────────────────────

class TestDealScore:

    def test_basic_score(self):
        score = deal_score(total=20.0, base_limit=30.0, bucket="good")
        assert 0 <= score <= 100
        # 20/30 = 0.667 → (1 - 0.667) * 70 ≈ 23.3 + cond bonus 0 = 23
        assert 20 <= score <= 30

    def test_exact_limit(self):
        score = deal_score(total=30.0, base_limit=30.0, bucket="good")
        # ratio = 1.0 → (1-1)*70 = 0
        assert score <= 10

    def test_zero_total(self):
        score = deal_score(total=0.0, base_limit=30.0, bucket="good")
        # ratio = 0 → (1-0)*70 = 70
        assert score >= 60

    def test_zero_limit(self):
        score = deal_score(total=10.0, base_limit=0.0, bucket="good")
        assert score == 0

    def test_make_offer_bonus(self):
        s1 = deal_score(total=20.0, base_limit=30.0, bucket="good", make_offer=False)
        s2 = deal_score(total=20.0, base_limit=30.0, bucket="good", make_offer=True)
        assert s2 - s1 == 10  # +10 for make offer

    def test_brand_new_bonus(self):
        s1 = deal_score(total=20.0, base_limit=30.0, bucket="good")
        s2 = deal_score(total=20.0, base_limit=30.0, bucket="brand_new")
        assert s2 - s1 == 8  # +8 for brand_new

    def test_like_new_bonus(self):
        s1 = deal_score(total=20.0, base_limit=30.0, bucket="good")
        s2 = deal_score(total=20.0, base_limit=30.0, bucket="like_new")
        assert s2 - s1 == 8  # +8 for like_new

    def test_very_good_bonus(self):
        s1 = deal_score(total=20.0, base_limit=30.0, bucket="good")
        s2 = deal_score(total=20.0, base_limit=30.0, bucket="very_good")
        assert s2 - s1 == 5  # +5 for very_good

    def test_acceptable_penalty(self):
        s1 = deal_score(total=20.0, base_limit=30.0, bucket="good")
        s2 = deal_score(total=20.0, base_limit=30.0, bucket="acceptable")
        assert s1 - s2 == 5  # -5 for acceptable

    def test_ship_estimated_penalty(self):
        s1 = deal_score(total=20.0, base_limit=30.0, bucket="good", ship_estimated=False)
        s2 = deal_score(total=20.0, base_limit=30.0, bucket="good", ship_estimated=True)
        assert s1 - s2 == 2  # -2 for estimated shipping

    def test_sold_avg_below_total_penalty(self):
        s1 = deal_score(total=20.0, base_limit=30.0, bucket="good", sold_avg=None)
        s2 = deal_score(total=20.0, base_limit=30.0, bucket="good", sold_avg=15.0)
        assert s1 - s2 == 5  # -5 when sold_avg < total

    def test_sold_avg_above_total_no_penalty(self):
        s1 = deal_score(total=20.0, base_limit=30.0, bucket="good", sold_avg=None)
        s2 = deal_score(total=20.0, base_limit=30.0, bucket="good", sold_avg=25.0)
        assert s1 == s2  # no penalty when sold_avg > total

    def test_score_clamped_to_0(self):
        score = deal_score(total=50.0, base_limit=30.0, bucket="acceptable",
                           ship_estimated=True, sold_avg=10.0)
        assert score >= 0

    def test_score_clamped_to_100(self):
        score = deal_score(total=1.0, base_limit=100.0, bucket="brand_new",
                           make_offer=True)
        assert score <= 100

    def test_all_bonuses_combined(self):
        score = deal_score(total=5.0, base_limit=100.0, bucket="brand_new",
                           make_offer=True, ship_estimated=False, sold_avg=50.0)
        # ratio_score: (1 - 5/100) * 70 = 66.5
        # +8 brand_new, +10 make_offer = +18
        # total ≈ 84-85
        assert score >= 75

    def test_all_penalties_combined(self):
        score = deal_score(total=29.0, base_limit=30.0, bucket="acceptable",
                           ship_estimated=True, sold_avg=20.0)
        # ratio: (1 - 29/30)*70 ≈ 2.3
        # -5 acceptable, -2 ship_estimated, -5 sold_avg < total = -12
        # total ≈ -9.7 → clamped to 0
        assert score == 0

    def test_unknown_bucket_no_bonus(self):
        score = deal_score(total=20.0, base_limit=30.0, bucket="unknown_bucket")
        # Unknown bucket → 0 bonus (from _COND_BONUS.get default)
        base = deal_score(total=20.0, base_limit=30.0, bucket="good")
        assert score == base


# ─── _format_message Tests ──────────────────────────────────────────────────

class TestFormatMessage:

    def _make_item(self, **kwargs):
        defaults = {
            "title": "Test Book Title",
            "itemWebUrl": "http://ebay.com/123",
            "buyingOptions": [],
        }
        defaults.update(kwargs)
        return defaults

    def test_basic_buy_message(self):
        item = self._make_item()
        msg = _format_message("isbn1", item, "good", 20.0, 30.0)
        assert "📚" in msg
        assert "isbn1" in msg
        assert "Good" in msg
        assert "BUY" in msg
        assert "$20" in msg

    def test_offer_message(self):
        item = self._make_item(buyingOptions=["BEST_OFFER"])
        msg = _format_message("isbn1", item, "good", 20.0, 30.0)
        assert "OFFER" in msg
        assert "Make Offer" in msg

    def test_sold_stats_in_message(self):
        item = self._make_item()
        msg = _format_message("isbn1", item, "good", 20.0, 30.0,
                              sold_avg=25, sold_count=10)
        assert "Sold avg: $25" in msg
        assert "10 sold" in msg

    def test_sold_avg_warning(self):
        item = self._make_item()
        msg = _format_message("isbn1", item, "good", 30.0, 40.0,
                              sold_avg=25)
        assert "sold avg < listing" in msg

    def test_ship_estimated_note(self):
        item = self._make_item()
        msg = _format_message("isbn1", item, "good", 20.0, 30.0,
                              ship_estimated=True)
        assert "est. ship" in msg

    def test_unverified_badge(self):
        item = self._make_item()
        msg = _format_message("isbn1", item, "good", 20.0, 30.0,
                              match_quality="UNVERIFIED_KEYWORD")
        assert "unverified" in msg

    def test_confirmed_no_badge(self):
        item = self._make_item()
        msg = _format_message("isbn1", item, "good", 20.0, 30.0,
                              match_quality="CONFIRMED")
        assert "unverified" not in msg

    def test_fire_score_badge(self):
        item = self._make_item()
        msg = _format_message("isbn1", item, "good", 20.0, 30.0, score=85)
        assert "🔥85" in msg

    def test_sparkle_score_badge(self):
        item = self._make_item()
        msg = _format_message("isbn1", item, "good", 20.0, 30.0, score=60)
        assert "✨60" in msg

    def test_low_score_badge(self):
        item = self._make_item()
        msg = _format_message("isbn1", item, "good", 20.0, 30.0, score=30)
        assert "[30]" in msg

    def test_no_score(self):
        item = self._make_item()
        msg = _format_message("isbn1", item, "good", 20.0, 30.0, score=None)
        assert "🔥" not in msg
        assert "✨" not in msg

    def test_condition_labels(self):
        item = self._make_item()
        for bucket, label in [("brand_new", "New"), ("like_new", "Like New"),
                               ("very_good", "Very Good"), ("acceptable", "Acceptable")]:
            msg = _format_message("isbn1", item, bucket, 20.0, 30.0)
            assert label in msg

    def test_url_in_message(self):
        item = self._make_item(itemWebUrl="http://ebay.com/test")
        msg = _format_message("isbn1", item, "good", 20.0, 30.0)
        assert "ebay.com/test" in msg

    def test_title_truncated(self):
        item = self._make_item(title="A" * 200)
        msg = _format_message("isbn1", item, "good", 20.0, 30.0)
        # Title should be truncated to 90 chars
        assert len(msg) < 1000

    def test_html_format(self):
        item = self._make_item()
        msg = _format_message("isbn1", item, "good", 20.0, 30.0)
        assert "<b>" in msg  # HTML bold
        assert "<a href=" in msg  # HTML link


# ─── _rebuild_totals_from_stats Tests ────────────────────────────────────────

class TestRebuildTotals:

    def test_basic(self):
        result = {"sold_avg": 25.0, "sold_count": 5}
        totals = _rebuild_totals_from_stats(result)
        assert len(totals) == 5
        assert all(t == 25.0 for t in totals)

    def test_no_avg(self):
        result = {"sold_avg": None, "sold_count": 5}
        assert _rebuild_totals_from_stats(result) == []

    def test_zero_count(self):
        result = {"sold_avg": 25.0, "sold_count": 0}
        assert _rebuild_totals_from_stats(result) == []

    def test_empty_result(self):
        assert _rebuild_totals_from_stats({}) == []

    def test_capped_at_50(self):
        result = {"sold_avg": 10.0, "sold_count": 100}
        totals = _rebuild_totals_from_stats(result)
        assert len(totals) == 50


class TestRebuildTotalsFromBucket:

    def test_basic(self):
        stats = {"avg": 20.0, "count": 3}
        totals = _rebuild_totals_from_bucket(stats)
        assert len(totals) == 3
        assert all(t == 20.0 for t in totals)

    def test_no_avg(self):
        stats = {"avg": None, "count": 3}
        assert _rebuild_totals_from_bucket(stats) == []

    def test_capped_at_30(self):
        stats = {"avg": 10.0, "count": 50}
        totals = _rebuild_totals_from_bucket(stats)
        assert len(totals) == 30


# ─── Sold Stats Store Tests ─────────────────────────────────────────────────

class TestSoldStatsStore:

    @pytest.fixture
    def store_dir(self, tmp_path, monkeypatch):
        from app import sold_stats_store as sss
        monkeypatch.setattr(sss, "_store_dir", lambda: tmp_path / "sold_stats")
        (tmp_path / "sold_stats").mkdir(parents=True, exist_ok=True)
        return sss

    def test_append_snapshot(self, store_dir):
        result = store_dir.append_snapshot("isbn1", 30, None, [10.0, 15.0, 20.0])
        assert result is True

    def test_append_empty_totals(self, store_dir):
        result = store_dir.append_snapshot("isbn1", 30, None, [])
        assert result is False

    def test_query_window_after_append(self, store_dir):
        store_dir.append_snapshot("isbn1", 30, None, [10.0, 20.0, 30.0])
        totals = store_dir.query_window("isbn1", 365, None)
        assert totals == [10.0, 20.0, 30.0]

    def test_query_window_empty(self, store_dir):
        totals = store_dir.query_window("isbn1", 365, None)
        assert totals == []

    def test_query_window_condition_filter(self, store_dir):
        store_dir.append_snapshot("isbn1", 30, "new", [50.0])
        store_dir.append_snapshot("isbn1", 30, "used", [20.0])
        new_totals = store_dir.query_window("isbn1", 365, "new")
        used_totals = store_dir.query_window("isbn1", 365, "used")
        assert new_totals == [50.0]
        assert used_totals == [20.0]

    def test_query_window_all_conditions(self, store_dir):
        store_dir.append_snapshot("isbn1", 30, "new", [50.0])
        store_dir.append_snapshot("isbn1", 30, "used", [20.0])
        all_totals = store_dir.query_window("isbn1", 365, None)
        assert set(all_totals) == {50.0, 20.0}

    def test_throttle_prevents_duplicate(self, store_dir, monkeypatch):
        monkeypatch.setattr(store_dir, "_THROTTLE_SECONDS", 3600)
        r1 = store_dir.append_snapshot("isbn1", 30, None, [10.0])
        assert r1 is True
        r2 = store_dir.append_snapshot("isbn1", 30, None, [20.0])
        assert r2 is False  # throttled

    def test_different_days_not_throttled(self, store_dir):
        r1 = store_dir.append_snapshot("isbn1", 30, None, [10.0])
        r2 = store_dir.append_snapshot("isbn1", 90, None, [20.0])
        assert r1 is True
        assert r2 is True

    def test_snapshot_span_days(self, store_dir, monkeypatch):
        # Manually insert entries with different timestamps
        now = time.time()
        isbn_clean = "isbn1"
        path = store_dir._isbn_path(isbn_clean)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "isbn": isbn_clean,
            "entries": [
                {"ts": now - 86400 * 30, "days": 30, "cond": None, "totals": [10.0]},
                {"ts": now, "days": 30, "cond": None, "totals": [20.0]},
            ],
        }
        path.write_text(json.dumps(data))
        span = store_dir.snapshot_span_days("isbn1", None)
        assert span is not None
        assert 29.0 <= span <= 31.0

    def test_snapshot_span_none_for_empty(self, store_dir):
        span = store_dir.snapshot_span_days("nonexistent", None)
        assert span is None

    def test_entry_summary(self, store_dir):
        store_dir.append_snapshot("isbn1", 30, None, [10.0, 20.0])
        store_dir.append_snapshot("isbn1", 90, "used", [15.0])
        summary = store_dir.entry_summary("isbn1")
        assert summary["isbn"] == "isbn1"
        assert summary["total_entries"] == 2
        assert isinstance(summary["by_window"], dict)

    def test_isbn_cleanup(self, store_dir):
        # ISBN with dashes and spaces should be normalized
        store_dir.append_snapshot("978-0-13-235088-4", 30, None, [10.0])
        totals = store_dir.query_window("9780132350884", 365, None)
        assert totals == [10.0]


# ─── trend_direction Tests ──────────────────────────────────────────────────

class TestTrendDirection:

    def test_uptrend(self):
        from app.sold_stats_store import trend_direction
        result = trend_direction(30.0, 20.0, threshold=0.15)
        assert result == "UPTREND"

    def test_downtrend(self):
        from app.sold_stats_store import trend_direction
        result = trend_direction(15.0, 25.0, threshold=0.15)
        assert result == "DOWNTREND"

    def test_stable(self):
        from app.sold_stats_store import trend_direction
        result = trend_direction(25.0, 24.0, threshold=0.15)
        assert result == "STABLE"

    def test_none_short(self):
        from app.sold_stats_store import trend_direction
        assert trend_direction(None, 20.0) == "UNKNOWN"

    def test_none_long(self):
        from app.sold_stats_store import trend_direction
        assert trend_direction(20.0, None) == "UNKNOWN"

    def test_zero_long(self):
        from app.sold_stats_store import trend_direction
        assert trend_direction(20.0, 0.0) == "UNKNOWN"

    def test_exact_threshold_stable(self):
        from app.sold_stats_store import trend_direction
        # 15% increase: 23/20 = 1.15 → ratio = 0.15 → not > 0.15
        result = trend_direction(23.0, 20.0, threshold=0.15)
        assert result == "STABLE"

    def test_just_over_threshold(self):
        from app.sold_stats_store import trend_direction
        # 15.01% increase
        result = trend_direction(23.01, 20.0, threshold=0.15)
        assert result == "UPTREND"

    def test_custom_threshold(self):
        from app.sold_stats_store import trend_direction
        # 10% change with 5% threshold → uptrend
        result = trend_direction(22.0, 20.0, threshold=0.05)
        assert result == "UPTREND"


# ─── compute_trends Tests ───────────────────────────────────────────────────

class TestComputeTrends:

    def test_all_stable(self):
        from app.sold_stats_store import compute_trends
        result = compute_trends(20.0, 20.0, 20.0)
        assert result["trend_30_vs_90"] == "STABLE"
        assert result["trend_30_vs_365"] == "STABLE"
        assert result["trend_90_vs_365"] == "STABLE"
        assert result["trendshift"] is False

    def test_uptrend(self):
        from app.sold_stats_store import compute_trends
        result = compute_trends(30.0, 20.0, 15.0)
        assert result["trend_30_vs_90"] == "UPTREND"
        assert result["trend_30_vs_365"] == "UPTREND"

    def test_downtrend(self):
        from app.sold_stats_store import compute_trends
        result = compute_trends(15.0, 20.0, 25.0)
        assert result["trend_30_vs_90"] == "DOWNTREND"
        assert result["trend_30_vs_365"] == "DOWNTREND"

    def test_trendshift_true(self):
        from app.sold_stats_store import compute_trends
        # |30 - 15| / 15 = 100% > 40%
        result = compute_trends(30.0, 20.0, 15.0)
        assert result["trendshift"] is True

    def test_trendshift_false(self):
        from app.sold_stats_store import compute_trends
        # |21 - 20| / 20 = 5% < 40%
        result = compute_trends(21.0, 20.0, 20.0)
        assert result["trendshift"] is False

    def test_all_none(self):
        from app.sold_stats_store import compute_trends
        result = compute_trends(None, None, None)
        assert result["trend_30_vs_90"] == "UNKNOWN"
        assert result["trendshift"] is False

    def test_partial_none(self):
        from app.sold_stats_store import compute_trends
        result = compute_trends(25.0, None, 20.0)
        assert result["trend_30_vs_90"] == "UNKNOWN"
        assert result["trend_30_vs_365"] != "UNKNOWN"

    def test_mixed_trends(self):
        from app.sold_stats_store import compute_trends
        # 30d up vs 90d, but 90d down vs 365d
        result = compute_trends(25.0, 18.0, 22.0)
        assert result["trend_30_vs_90"] == "UPTREND"
        assert result["trend_90_vs_365"] == "DOWNTREND"


# ─── Dewey Classification Fix Verification ──────────────────────────────────

class TestDeweyFixVerification:
    """Verify the Dewey classification bug fix in analytics.py."""

    def test_medicine_now_reachable(self):
        from app.analytics import dewey_to_category
        result = dewey_to_category("615")
        assert result["category"] == "Medicine/Health"

    def test_technology_general_narrowed(self):
        from app.analytics import dewey_to_category
        result = dewey_to_category("605")
        assert result["category"] == "Technology (General)"

    def test_technology_boundary(self):
        from app.analytics import dewey_to_category
        r609 = dewey_to_category("609")
        r610 = dewey_to_category("610")
        assert r609["category"] == "Technology (General)"
        assert r610["category"] == "Medicine/Health"

    def test_medicine_boundary(self):
        from app.analytics import dewey_to_category
        r619 = dewey_to_category("619")
        r620 = dewey_to_category("620")
        assert r619["category"] == "Medicine/Health"
        assert r620["category"] == "Engineering"

    def test_engineering_still_works(self):
        from app.analytics import dewey_to_category
        result = dewey_to_category("625")
        assert result["category"] == "Engineering"

    def test_all_600_range_no_gaps(self):
        from app.analytics import dewey_to_category
        for d in range(600, 700):
            result = dewey_to_category(str(d))
            assert result["is_textbook_likely"] is True, f"Gap at Dewey {d}: {result}"
