"""
ArbResult dataclass ve _filter_result() için genişletilmiş testler.
Önceki testlerden FARKLI senaryolar: tüm filter parametreleri,
ArbResult alanları, _isbn13_to_asin, _calc_profit_strict, IsbnMatchPolicy,
condition_in, source_in, max_buy_ratio_pct, min/max amazon price.
"""
from __future__ import annotations
import pytest
from app.csv_arb_scanner import (
    ArbResult, ScanFilters, _filter_result, IsbnMatchPolicy, InvalidIsbnPolicy,
    _isbn13_to_asin, _calc_profit_strict,
)
from app.profit_calc import DEFAULT_FEES


# ── _isbn13_to_asin() ─────────────────────────────────────────────────────────

class TestIsbn13ToAsin:
    def test_isbn13_to_asin_correct(self):
        asin = _isbn13_to_asin("9780132350884")
        assert asin == "0132350882"

    def test_isbn10_passthrough(self):
        asin = _isbn13_to_asin("0132350882")
        assert asin == "0132350882"

    def test_isbn10_with_x(self):
        asin = _isbn13_to_asin("020161622X")
        assert asin == "020161622X"

    def test_979_prefix_returns_none(self):
        asin = _isbn13_to_asin("9791032300824")
        assert asin is None

    def test_invalid_checksum_still_converts(self):
        """_isbn13_to_asin does NOT validate ISBN-13 checksum — only converts body."""
        asin = _isbn13_to_asin("9780132350885")  # wrong check digit in ISBN-13
        # Still extracts ASIN from body (3:12) → same result as valid
        assert asin == "0132350882"

    def test_non_numeric_returns_none(self):
        asin = _isbn13_to_asin("978013235088A")
        assert asin is None

    def test_empty_string_returns_none(self):
        asin = _isbn13_to_asin("")
        assert asin is None

    def test_short_string_returns_none(self):
        asin = _isbn13_to_asin("1234")
        assert asin is None

    def test_isbn10_invalid_checksum_returns_none(self):
        asin = _isbn13_to_asin("0132350883")
        assert asin is None

    def test_isbn13_with_dashes(self):
        asin = _isbn13_to_asin("978-0-13-235088-4")
        assert asin == "0132350882"

    def test_x_only_acceptable_at_last_position_isbn10(self):
        # X in middle position → invalid
        asin = _isbn13_to_asin("013X350882")
        assert asin is None


# ── ArbResult dataclass ───────────────────────────────────────────────────────

class TestArbResult:
    def _make(self, **kw):
        defaults = dict(isbn="9780132350884", asin="0132350882",
                        source="ebay", source_condition="used", buy_price=10.0)
        defaults.update(kw)
        return ArbResult(**defaults)

    def test_default_profit_zero(self):
        r = self._make()
        assert r.profit == 0.0

    def test_default_roi_pct_zero(self):
        r = self._make()
        assert r.roi_pct == 0.0

    def test_default_viable_false(self):
        r = self._make()
        assert r.viable is False

    def test_default_accepted_false(self):
        r = self._make()
        assert r.accepted is False

    def test_default_roi_tier_loss(self):
        r = self._make()
        assert r.roi_tier == "loss"

    def test_default_bsr_none(self):
        r = self._make()
        assert r.bsr is None

    def test_default_confidence_none(self):
        r = self._make()
        assert r.confidence is None

    def test_default_match_quality_empty(self):
        r = self._make()
        assert r.match_quality == ""

    def test_default_nyt_bestseller_false(self):
        r = self._make()
        assert r.nyt_bestseller is False

    def test_default_buyback_profit_none(self):
        r = self._make()
        assert r.buyback_profit is None

    def test_to_dict_contains_isbn(self):
        r = self._make()
        d = r.to_dict()
        assert d["isbn"] == "9780132350884"

    def test_to_dict_contains_source(self):
        r = self._make()
        assert r.to_dict()["source"] == "ebay"

    def test_to_dict_all_fields_serializable(self):
        r = self._make()
        d = r.to_dict()
        import json
        json.dumps(d)  # should not raise

    def test_setting_profit(self):
        r = self._make()
        r.profit = 15.5
        assert r.profit == 15.5

    def test_setting_match_fields(self):
        r = self._make(match_quality="CONFIRMED", match_reason="gtin", query_mode="gtin")
        assert r.match_quality == "CONFIRMED"
        assert r.match_reason == "gtin"
        assert r.query_mode == "gtin"

    def test_to_dict_match_fields_present(self):
        r = self._make(match_quality="CONFIRMED", query_mode="gtin")
        d = r.to_dict()
        assert "match_quality" in d
        assert "query_mode" in d


# ── ScanFilters ───────────────────────────────────────────────────────────────

class TestScanFilters:
    def test_default_only_viable_true(self):
        f = ScanFilters()
        assert f.only_viable is True

    def test_default_strict_mode_true(self):
        f = ScanFilters()
        assert f.strict_mode is True

    def test_default_isbn_match_policy_balanced(self):
        f = ScanFilters()
        assert f.isbn_match_policy == IsbnMatchPolicy.BALANCED

    def test_precision_policy(self):
        f = ScanFilters(isbn_match_policy=IsbnMatchPolicy.PRECISION)
        assert f.isbn_match_policy == IsbnMatchPolicy.PRECISION

    def test_recall_policy(self):
        f = ScanFilters(isbn_match_policy=IsbnMatchPolicy.RECALL)
        assert f.isbn_match_policy == IsbnMatchPolicy.RECALL

    def test_invalid_isbn_policy_default(self):
        f = ScanFilters()
        assert f.invalid_isbn_policy == InvalidIsbnPolicy.BEST_EFFORT


# ── _filter_result() — Extended Scenarios ────────────────────────────────────

def _make_result(**kw):
    defaults = dict(isbn="9780132350884", asin="0132350882",
                    source="ebay", source_condition="used",
                    buy_price=10.0, amazon_sell_price=40.0,
                    buybox_type="used", match_type="USED→USED",
                    profit=15.0, roi_pct=60.0, roi_tier="fire", viable=True, accepted=True)
    defaults.update(kw)
    r = ArbResult(**{k: defaults[k] for k in ArbResult.__dataclass_fields__ if k in defaults})
    r.profit = defaults.get("profit", 0.0)
    r.roi_pct = defaults.get("roi_pct", 0.0)
    r.viable = defaults.get("viable", False)
    if "amazon_sell_price" in defaults:
        r.amazon_sell_price = defaults["amazon_sell_price"]
    return r


class TestFilterResultExtended:
    def test_passes_with_all_defaults(self):
        r = _make_result()
        assert _filter_result(r, ScanFilters()) == ""

    def test_rejects_below_min_buy_price(self):
        r = _make_result(buy_price=3.0)
        reason = _filter_result(r, ScanFilters(min_buy_price=5.0))
        assert "buy_price_below_min" in reason

    def test_passes_above_min_buy_price(self):
        r = _make_result(buy_price=10.0)
        assert _filter_result(r, ScanFilters(min_buy_price=5.0)) == ""

    def test_rejects_above_max_buy_price(self):
        r = _make_result(buy_price=20.0)
        reason = _filter_result(r, ScanFilters(max_buy_price=15.0))
        assert "buy_price_above_max" in reason

    def test_passes_below_max_buy_price(self):
        r = _make_result(buy_price=10.0)
        assert _filter_result(r, ScanFilters(max_buy_price=15.0)) == ""

    def test_rejects_no_amazon_price(self):
        r = _make_result(amazon_sell_price=None)
        reason = _filter_result(r, ScanFilters())
        assert reason != ""

    def test_rejects_below_min_amazon_price(self):
        r = _make_result(amazon_sell_price=20.0)
        reason = _filter_result(r, ScanFilters(min_amazon_price=30.0))
        assert "amazon_price_below_min" in reason

    def test_passes_above_min_amazon_price(self):
        r = _make_result(amazon_sell_price=40.0)
        assert _filter_result(r, ScanFilters(min_amazon_price=30.0)) == ""

    def test_rejects_above_max_amazon_price(self):
        r = _make_result(amazon_sell_price=100.0)
        reason = _filter_result(r, ScanFilters(max_amazon_price=80.0))
        assert "amazon_price_above_max" in reason

    def test_rejects_not_viable_when_only_viable(self):
        r = _make_result(viable=False, profit=-5.0)
        reason = _filter_result(r, ScanFilters(only_viable=True))
        assert reason == "not_viable"

    def test_passes_not_viable_when_only_viable_false(self):
        r = _make_result(viable=False, profit=-5.0, roi_pct=-20.0)
        # only_viable=False → don't filter by viability
        # But min_roi_pct default is None → no ROI filter
        reason = _filter_result(r, ScanFilters(only_viable=False))
        assert reason == ""

    def test_rejects_below_min_profit(self):
        r = _make_result(profit=3.0)
        reason = _filter_result(r, ScanFilters(min_profit_usd=5.0, only_viable=False))
        assert "profit_below_min" in reason

    def test_passes_above_min_profit(self):
        r = _make_result(profit=10.0)
        assert _filter_result(r, ScanFilters(min_profit_usd=5.0)) == ""

    def test_rejects_above_max_roi(self):
        r = _make_result(roi_pct=200.0)
        reason = _filter_result(r, ScanFilters(max_roi_pct=150.0))
        assert "roi_above_max" in reason

    def test_passes_below_max_roi(self):
        r = _make_result(roi_pct=50.0)
        assert _filter_result(r, ScanFilters(max_roi_pct=100.0)) == ""

    def test_rejects_condition_not_in_list(self):
        r = _make_result(source_condition="used")
        reason = _filter_result(r, ScanFilters(condition_in=["new"]))
        assert "condition_not_in" in reason

    def test_passes_condition_in_list(self):
        r = _make_result(source_condition="used")
        assert _filter_result(r, ScanFilters(condition_in=["used", "new"])) == ""

    def test_rejects_source_not_in_list(self):
        r = _make_result(source="ebay")
        reason = _filter_result(r, ScanFilters(source_in=["thriftbooks"]))
        assert "source_not_in" in reason

    def test_passes_source_in_list(self):
        r = _make_result(source="ebay")
        assert _filter_result(r, ScanFilters(source_in=["ebay", "abebooks"])) == ""

    def test_max_buy_ratio_rejects(self):
        # buy_price=10, amazon=40 → ratio=25%. max=20% → reject
        r = _make_result(buy_price=10.0, amazon_sell_price=40.0)
        reason = _filter_result(r, ScanFilters(max_buy_ratio_pct=20.0))
        assert "buy_ratio_too_high" in reason

    def test_max_buy_ratio_passes(self):
        # buy_price=10, amazon=40 → ratio=25%. max=30% → pass
        r = _make_result(buy_price=10.0, amazon_sell_price=40.0)
        assert _filter_result(r, ScanFilters(max_buy_ratio_pct=30.0)) == ""

    def test_buyback_only_rejects_zero_profit(self):
        r = _make_result()
        r.buyback_profit = 0.0
        reason = _filter_result(r, ScanFilters(buyback_only=True))
        assert reason == "buyback_not_profitable"

    def test_buyback_only_passes_positive_profit(self):
        r = _make_result()
        r.buyback_profit = 5.0
        assert _filter_result(r, ScanFilters(buyback_only=True)) == ""

    def test_min_buyback_profit_rejects(self):
        r = _make_result()
        r.buyback_profit = 3.0
        reason = _filter_result(r, ScanFilters(min_buyback_profit=5.0))
        assert "buyback_profit_below_min" in reason

    def test_min_buyback_profit_passes(self):
        r = _make_result()
        r.buyback_profit = 8.0
        assert _filter_result(r, ScanFilters(min_buyback_profit=5.0)) == ""

    def test_min_buyback_profit_none_rejects(self):
        r = _make_result()
        r.buyback_profit = None
        reason = _filter_result(r, ScanFilters(min_buyback_profit=5.0))
        assert "buyback_profit_below_min" in reason


# ── _calc_profit_strict() ────────────────────────────────────────────────────

class TestCalcProfitStrict:
    def _amz(self, used_bb=None, new_bb=None):
        data = {}
        data["used"] = {"buybox": {"total": used_bb}} if used_bb else {}
        data["new"]  = {"buybox": {"total": new_bb}}  if new_bb  else {}
        return data

    def test_new_condition_strict_gets_new_price(self):
        amz = self._amz(new_bb=50.0)
        price, bb_type, match, reason = _calc_profit_strict(10.0, "new", amz, strict_mode=True)
        assert price == 50.0
        assert bb_type == "new"
        assert match == "NEW→NEW"

    def test_new_condition_strict_no_new_price_returns_none(self):
        amz = self._amz(used_bb=40.0)
        price, bb_type, match, reason = _calc_profit_strict(10.0, "new", amz, strict_mode=True)
        assert price is None
        assert "missing_new_buybox" in reason

    def test_used_condition_strict_gets_used_price(self):
        amz = self._amz(used_bb=35.0)
        price, bb_type, match, reason = _calc_profit_strict(10.0, "used", amz, strict_mode=True)
        assert price == 35.0
        assert bb_type == "used"
        assert match == "USED→USED"

    def test_used_condition_strict_no_used_price_returns_none(self):
        amz = self._amz(new_bb=50.0)
        price, bb_type, match, reason = _calc_profit_strict(10.0, "used", amz, strict_mode=True)
        assert price is None
        assert "missing_used_buybox" in reason

    def test_new_condition_non_strict_falls_back_to_used(self):
        amz = self._amz(used_bb=40.0)
        price, bb_type, match, reason = _calc_profit_strict(10.0, "new", amz, strict_mode=False)
        assert price == 40.0
        assert "fallback" in match.lower()

    def test_used_condition_non_strict_falls_back_to_new(self):
        amz = self._amz(new_bb=50.0)
        price, bb_type, match, reason = _calc_profit_strict(10.0, "used", amz, strict_mode=False)
        assert price == 50.0
        assert "fallback" in match.lower()

    def test_unknown_condition_returns_none(self):
        amz = self._amz(used_bb=35.0)
        price, bb_type, match, reason = _calc_profit_strict(10.0, "unknown", amz, strict_mode=True)
        assert price is None
        assert "unknown_condition" in reason

    def test_empty_amazon_data_returns_none(self):
        price, _, _, reason = _calc_profit_strict(10.0, "used", {}, strict_mode=True)
        assert price is None

    def test_uppercase_condition_handled(self):
        amz = self._amz(used_bb=35.0)
        price, bb_type, match, reason = _calc_profit_strict(10.0, "USED", amz, strict_mode=True)
        assert price == 35.0
