from __future__ import annotations
import pytest
from app.profit_calc import calculate, DEFAULT_FEES
from app.csv_arb_scanner import ScanFilters, _filter_result, ArbResult

# ── profit_calc ───────────────────────────────────────────────────────────────

def _make_amazon(used_bb=None, new_bb=None):
    data = {}
    if used_bb:
        data["used"] = {"buybox": {"total": used_bb}, "top2": []}
    else:
        data["used"] = {"top2": []}
    if new_bb:
        data["new"] = {"buybox": {"total": new_bb}, "top2": []}
    else:
        data["new"] = {"top2": []}
    return data

def test_profit_with_used_buybox():
    amz = _make_amazon(used_bb=35.0)
    r = calculate(10.0, amz, DEFAULT_FEES)
    assert r is not None
    assert r.sell_source == "used_buybox"
    assert r.profit > 0

def test_profit_fallback_to_new_when_no_used():
    """When no used buybox, falls back to new — this is by design for generic /alerts/details."""
    amz = _make_amazon(new_bb=50.0)
    r = calculate(10.0, amz, DEFAULT_FEES)
    assert r is not None
    assert "new" in r.sell_source

def test_profit_returns_none_when_no_amazon():
    r = calculate(10.0, {}, DEFAULT_FEES)
    assert r is None

def test_profit_negative_is_not_viable():
    amz = _make_amazon(used_bb=12.0)
    r = calculate(11.0, amz, DEFAULT_FEES)
    # fees eat the margin
    assert r is not None
    assert not r.viable or r.profit <= 0

# ── ScanFilters ───────────────────────────────────────────────────────────────

def _make_result(**kw):
    defaults = dict(isbn="x", asin="y", source="ebay", source_condition="used",
                    buy_price=10.0, amazon_sell_price=40.0, buybox_type="used",
                    match_type="used_buybox", profit=15.0, roi_pct=60.0,
                    roi_tier="fire", viable=True, accepted=True)
    defaults.update(kw)
    r = ArbResult(**{k: defaults[k] for k in ArbResult.__dataclass_fields__ if k in defaults})
    r.profit = defaults["profit"]
    r.roi_pct = defaults["roi_pct"]
    r.viable = defaults["viable"]
    return r

def test_filter_passes_viable():
    r = _make_result()
    assert _filter_result(r, ScanFilters()) == ""

def test_filter_rejects_below_min_roi():
    r = _make_result(roi_pct=10.0)
    assert "roi_below" in _filter_result(r, ScanFilters(min_roi_pct=20.0))

def test_filter_rejects_buyback_only_when_no_buyback():
    r = _make_result()
    r.buyback_profit = None
    reason = _filter_result(r, ScanFilters(buyback_only=True))
    assert reason == "buyback_not_profitable"

def test_filter_passes_buyback_only_when_profitable():
    r = _make_result()
    r.buyback_profit = 8.0
    assert _filter_result(r, ScanFilters(buyback_only=True)) == ""
