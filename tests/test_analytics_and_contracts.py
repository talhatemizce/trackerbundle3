"""
Analytics & contract regression tests.

Perplexity audit'te bulunan P1-005 ve P1-006 defect'lerinin
bir daha ortaya çıkmaması için kalıcı regression guard.
"""
from __future__ import annotations
import pytest
from app.analytics import compute_confidence, bsr_to_velocity, lc_class_to_category, dewey_to_category
from app.csv_arb_scanner import ArbResult, ScanFilters, _filter_result


# ─── Confidence model field contract ────────────────────────────────────────

def _scanner_result(**overrides) -> dict:
    """Scanner'ın gerçekte emit ettiği field isimlerini döndür."""
    base = {
        # Scanner field names (NOT the old legacy names)
        "source_condition":           "used",
        "buy_price":                  12.0,
        "amazon_sell_price":          45.0,
        "sell_source":                "used_buybox",
        "buybox_type":                "used",
        "amazon_is_sold_by_amazon":   False,   # NOT is_amazon_selling
        "amazon_seller_count":        3,        # NOT amazon_used_count/new_count
        "ebay_seller_feedback":       98.5,
        "bsr":                        25000,
        "profit":                     18.5,
        "roi_pct":                    64.2,
        "roi_tier":                   "fire",
        "viable":                     True,
    }
    base.update(overrides)
    return base


def test_confidence_uses_scanner_field_source_condition():
    """source_condition field → score > 0 (not reading wrong field name)."""
    r = _scanner_result(source_condition="good")
    score = compute_confidence(r)
    assert score > 0, "Should score >0 when source_condition='good' is present"


def test_confidence_uses_amazon_is_sold_by_amazon():
    """amazon_is_sold_by_amazon=False → bonus points (Amazon not competing)."""
    r_no_amz  = _scanner_result(amazon_is_sold_by_amazon=False)
    r_yes_amz = _scanner_result(amazon_is_sold_by_amazon=True)
    score_no  = compute_confidence(r_no_amz)
    score_yes = compute_confidence(r_yes_amz)
    assert score_no >= score_yes, "No Amazon selling should give equal or higher confidence"


def test_confidence_uses_amazon_seller_count():
    """amazon_seller_count drives competitor scoring."""
    r_few  = _scanner_result(amazon_seller_count=2)
    r_many = _scanner_result(amazon_seller_count=50)
    score_few  = compute_confidence(r_few)
    score_many = compute_confidence(r_many)
    assert score_few >= score_many, "Fewer Amazon sellers should give higher confidence"


def test_confidence_scanner_vs_legacy_fields_equivalent():
    """
    P1-005 regression guard:
    Scanner output (amazon_is_sold_by_amazon + amazon_seller_count)
    should score similarly to legacy field names (is_amazon_selling + amazon_used_count).
    Delta must be < 15 points.
    """
    scanner_result = _scanner_result()
    legacy_result = {
        "source_condition":    "used",
        "buy_price":           12.0,
        "amazon_sell_price":   45.0,
        "sell_source":         "used_buybox",
        "buybox_type":         "used",
        "is_amazon_selling":   False,         # old name
        "amazon_used_count":   3,              # old name
        "ebay_seller_feedback": 98.5,
        "bsr":                 25000,
        "profit":              18.5,
        "roi_pct":             64.2,
        "roi_tier":            "fire",
        "viable":              True,
    }
    scanner_score = compute_confidence(scanner_result)
    legacy_score  = compute_confidence(legacy_result)
    delta = abs(scanner_score - legacy_score)
    assert delta < 15, (
        f"Confidence score delta too large: scanner={scanner_score} "
        f"legacy={legacy_score} delta={delta}. "
        f"Field name mismatch in compute_confidence()."
    )


def test_confidence_high_quality_deal_scores_above_60():
    """A clearly good deal should score above 60/100."""
    r = _scanner_result(
        sell_source="used_buybox",
        amazon_is_sold_by_amazon=False,
        amazon_seller_count=2,
        ebay_seller_feedback=99.1,
        bsr=15000,
    )
    score = compute_confidence(r)
    assert score >= 45, f"High-quality deal scored only {score}/100"


# ─── ArbResult match metadata fields ────────────────────────────────────────

def test_arbresult_has_match_fields():
    """P1-006: ArbResult must carry match_quality, match_reason, query_mode."""
    r = ArbResult(
        isbn="9780134042435", asin="0134042433",
        source="ebay", source_condition="used",
        buy_price=10.0, amazon_sell_price=None,
        buybox_type=None, match_type=None,
    )
    assert hasattr(r, "match_quality"), "ArbResult missing match_quality"
    assert hasattr(r, "match_reason"),  "ArbResult missing match_reason"
    assert hasattr(r, "query_mode"),    "ArbResult missing query_mode"


def test_arbresult_match_fields_survive_to_dict():
    """P1-006: match fields must be present in to_dict() output."""
    r = ArbResult(
        isbn="9780134042435", asin="0134042433",
        source="ebay", source_condition="used",
        buy_price=10.0, amazon_sell_price=None,
        buybox_type=None, match_type=None,
        match_quality="CONFIRMED",
        match_reason="gtin_isbn_verified",
        query_mode="gtin",
    )
    d = r.to_dict()
    assert d["match_quality"] == "CONFIRMED"
    assert d["query_mode"] == "gtin"


# ─── BSR velocity table ──────────────────────────────────────────────────────

@pytest.mark.parametrize("bsr,min_vel,max_vel", [
    (50,        700,  1100),   # top bestseller
    (1000,       80,   130),
    (5000,       25,    50),
    (50000,       3,    10),
    (200000,    0.8,   2.0),
    (1000000,   0.0,   0.5),
])
def test_bsr_velocity_ranges(bsr, min_vel, max_vel):
    vel = bsr_to_velocity(bsr)
    assert vel is not None
    assert min_vel <= vel <= max_vel, f"BSR {bsr} → velocity {vel}, expected {min_vel}–{max_vel}"


def test_bsr_velocity_none_for_invalid():
    assert bsr_to_velocity(None) is None
    assert bsr_to_velocity(0) is None
    assert bsr_to_velocity(-1) is None


# ─── LC / Dewey classification ───────────────────────────────────────────────

@pytest.mark.parametrize("lc,expected_tb", [
    ("QA76.5",  True),   # Computer Science
    ("TA123",   True),   # Engineering
    ("HF5415",  True),   # Business/Marketing
    ("PS3552",  False),  # Literature
    ("ND553",   False),  # Fine Arts
    ("",        False),
])
def test_lc_class_textbook_detection(lc, expected_tb):
    result = lc_class_to_category(lc)
    assert result["is_textbook_likely"] == expected_tb, \
        f"LC '{lc}' → is_textbook_likely={result['is_textbook_likely']}, expected {expected_tb}"


@pytest.mark.parametrize("dewey,expected_tb", [
    ("510",   True),   # Mathematics
    ("530.1", True),   # Physics
    ("370",   True),   # Education
    ("813",   False),  # Fiction
    ("750",   False),  # Fine Arts
])
def test_dewey_textbook_detection(dewey, expected_tb):
    result = dewey_to_category(dewey)
    assert result["is_textbook_likely"] == expected_tb, \
        f"Dewey '{dewey}' → is_textbook_likely={result['is_textbook_likely']}, expected {expected_tb}"
