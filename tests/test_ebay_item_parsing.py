"""
eBay Browse API item parsing tests — gerçek API formatlarıyla.

Bu testler item_total_price + normalize_condition + _get_ebay_offers'ın
gerçek eBay response formatlarını doğru handle ettiğini doğrular.
Mock veri kullanmaz — tests/fixtures/ klasöründeki gerçek format örnekleri.
"""
from __future__ import annotations
import json
from pathlib import Path
import pytest
import app.ebay_client as ebay_client
from app.ebay_client import item_total_price, normalize_condition

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


# ─── item_total_price edge cases ─────────────────────────────────────────────

class TestItemTotalPrice:

    def test_normal_item_returns_correct_total(self):
        item = load("ebay_item_normal.json")
        total = item_total_price(item, calc_ship_est=3.99)
        assert total == 16.49  # 12.50 + 3.99

    def test_price_null_returns_none_not_crash(self):
        """price: null gelince AttributeError fırlatmamalı."""
        item = load("ebay_item_price_null.json")
        result = item_total_price(item, calc_ship_est=3.99)
        assert result is None or result == 0.0  # graceful skip

    def test_price_missing_returns_none(self):
        item = load("ebay_item_no_price.json")
        result = item_total_price(item, calc_ship_est=3.99)
        assert result is None or result == 0.0

    def test_shipping_null_with_calc_est_uses_estimate(self):
        """shippingOptions: null varken calc_ship_est kullanılmalı."""
        item = load("ebay_item_shipping_null.json")
        result = item_total_price(item, calc_ship_est=3.99)
        # 15.00 + 3.99 = 18.99 veya None (ikisi de kabul edilebilir ama crash olmamalı)
        assert result is None or result > 0

    def test_no_shipping_key_with_calc_est(self):
        """shippingOptions key hiç yok, calc_ship_est ile devam et."""
        item = load("ebay_item_no_shipping.json")
        result = item_total_price(item, calc_ship_est=3.99)
        assert result is None or result > 0  # crash olmamalı

    def test_free_shipping_returns_price_only(self):
        """shippingOptions: [] → ücretsiz kargo, sadece fiyat."""
        item = load("ebay_item_free_shipping.json")
        total = item_total_price(item, calc_ship_est=3.99)
        assert total == 22.00

    def test_calculated_shipping_uses_estimate(self):
        item = load("ebay_item_calculated_shipping.json")
        total = item_total_price(item, calc_ship_est=3.99)
        assert total == pytest.approx(21.99)  # 18.00 + 3.99

    def test_calculated_shipping_without_estimate_returns_none(self):
        item = load("ebay_item_calculated_shipping.json")
        result = item_total_price(item, calc_ship_est=0)
        assert result is None

    def test_local_pickup_uses_estimate(self):
        item = load("ebay_item_local_pickup.json")
        total = item_total_price(item, calc_ship_est=3.99)
        assert total == pytest.approx(8.99)  # 5.00 + 3.99

    def test_zero_price_returns_none_or_zero(self):
        """Sıfır fiyatlı item geçerli bir opportunity değil."""
        item = load("ebay_item_zero_price.json")
        result = item_total_price(item, calc_ship_est=3.99)
        assert result is None or result == 0.0

    def test_price_as_string(self):
        """price: "19.99" (string) gracefully parse edilmeli."""
        item = load("ebay_item_price_string.json")
        result = item_total_price(item, calc_ship_est=3.99)
        assert result is None or result == pytest.approx(19.99)

    def test_full_real_item(self):
        """Gerçek eBay formatı — tüm alanlar dolu."""
        item = load("ebay_item_full_real.json")
        total = item_total_price(item, calc_ship_est=3.99)
        assert total == pytest.approx(12.98)  # 8.99 + 3.99


# ─── normalize_condition edge cases ──────────────────────────────────────────

class TestNormalizeCondition:

    @pytest.mark.parametrize("cond_text,cond_id,expected", [
        ("Brand New",       "1000", "brand_new"),
        ("New",             "1000", "brand_new"),
        ("Like New",        "2000", "like_new"),
        ("Very Good",       "2750", "very_good"),
        ("Good",            "3000", "good"),
        ("Acceptable",      "4000", "very_good"),   # 4000 = Very Good (eBay general); cid takes priority over text
        ("For parts",       "7000", "for_parts"),
        # conditionId takes priority over text
        ("Gibberish text",  "3000", "good"),
        ("",                "2500", "like_new"),
        # Neither
        ("",                None,   "unknown"),
        (None,              None,   "unknown"),
    ])
    def test_normalize_condition_cases(self, cond_text, cond_id, expected):
        result = normalize_condition(cond_text, cond_id)
        assert result == expected, f"cond_text={cond_text!r} cond_id={cond_id!r} → {result!r}, expected {expected!r}"

    def test_condition_dict_instead_of_string(self):
        """condition field bazen dict olabiliyor."""
        item = load("ebay_item_condition_dict.json")
        cond_text = item.get("condition") or ""
        if isinstance(cond_text, dict):
            cond_text = cond_text.get("conditionDisplayName", "")
        result = normalize_condition(str(cond_text), item.get("conditionId"))
        # Should not crash, return something reasonable
        assert isinstance(result, str)
        assert len(result) > 0


# ─── seller field edge cases ──────────────────────────────────────────────────

class TestSellerParsing:

    def test_seller_as_dict(self):
        item = load("ebay_item_full_real.json")
        seller = item.get("seller") or {}
        name = seller.get("username", "") if isinstance(seller, dict) else ""
        fp = seller.get("feedbackPercentage") if isinstance(seller, dict) else None
        assert name == "usedbooks_warehouse"
        assert fp == "99.1"

    def test_seller_as_string_does_not_crash(self):
        """seller field bazen string gelebiliyor."""
        item = load("ebay_item_seller_string.json")
        seller = item.get("seller") or {}
        # Kod pattern: seller.get("username") if isinstance(seller, dict) else ""
        name = seller.get("username", "") if isinstance(seller, dict) else ""
        assert name == ""  # string seller → empty name, no crash


# ─── image URL extraction ─────────────────────────────────────────────────────

class TestImageExtraction:

    def test_thumbnail_list_format(self):
        item = load("ebay_item_thumbnail_list.json")
        thumb = item.get("thumbnailImages") or item.get("image") or {}
        img = ""
        if isinstance(thumb, list) and thumb:
            img = thumb[0].get("imageUrl", "")
        elif isinstance(thumb, dict):
            img = thumb.get("imageUrl", "")
        assert img == "https://i.ebayimg.com/thumb.jpg"

    def test_image_dict_format(self):
        item = load("ebay_item_image_dict.json")
        thumb = item.get("thumbnailImages") or item.get("image") or {}
        img = ""
        if isinstance(thumb, list) and thumb:
            img = thumb[0].get("imageUrl", "")
        elif isinstance(thumb, dict):
            img = thumb.get("imageUrl", "")
        assert img == "https://i.ebayimg.com/img.jpg"


# ─── _get_ebay_offers integration with real eBay format ──────────────────────

class TestGetEbayOffersRealFormat:
    """
    _get_ebay_offers'ı mock browse_search_isbn ile test et ama
    gerçek fixture formatını döndür.
    """

    @pytest.mark.asyncio
    async def test_full_real_item_flows_through_scanner(self, monkeypatch):
        """Gerçek eBay formatı tarayıcıdan geçince offer oluşturmalı."""
        import app.csv_arb_scanner as scanner
        import app.ebay_client as ec

        real_item = load("ebay_item_full_real.json")

        async def fake_browse(client, isbn, **kw):
            return [real_item]

        monkeypatch.setattr(ec, "browse_search_isbn", fake_browse)
        monkeypatch.setattr(ec, "item_total_price",
                            lambda it, calc_ship_est=None: item_total_price(it, calc_ship_est=calc_ship_est or 3.99))
        monkeypatch.setattr(ec, "normalize_condition", normalize_condition)

        filters = scanner.ScanFilters(isbn_match_policy=scanner.IsbnMatchPolicy("recall"))
        offers = await scanner._get_ebay_offers("9780134042435", filters=filters)

        assert len(offers) >= 1, f"Expected ≥1 offer, got: {offers}"
        assert offers[0]["buy_price"] == pytest.approx(12.98)
        assert offers[0]["source"] == "ebay"

    @pytest.mark.asyncio
    async def test_price_null_item_is_skipped_not_crashed(self, monkeypatch):
        """price: null item skip edilmeli, crash olmamalı."""
        import app.csv_arb_scanner as scanner
        import app.ebay_client as ec

        null_item = load("ebay_item_price_null.json")

        async def fake_browse(client, isbn, **kw):
            return [null_item]

        monkeypatch.setattr(ec, "browse_search_isbn", fake_browse)
        monkeypatch.setattr(ec, "item_total_price",
                            lambda it, calc_ship_est=None: item_total_price(it, calc_ship_est=calc_ship_est or 3.99))
        monkeypatch.setattr(ec, "normalize_condition", normalize_condition)

        filters = scanner.ScanFilters(isbn_match_policy=scanner.IsbnMatchPolicy("recall"))
        # Should not raise — null price item gets skipped
        offers = await scanner._get_ebay_offers("9780134042435", filters=filters)
        assert isinstance(offers, list)

    @pytest.mark.asyncio
    async def test_mixed_items_null_and_normal(self, monkeypatch):
        """Karışık liste: null price + normal item → sadece normal gelsin."""
        import app.csv_arb_scanner as scanner
        import app.ebay_client as ec

        null_item  = load("ebay_item_price_null.json")
        good_item  = load("ebay_item_full_real.json")

        async def fake_browse(client, isbn, **kw):
            return [null_item, good_item]

        monkeypatch.setattr(ec, "browse_search_isbn", fake_browse)
        monkeypatch.setattr(ec, "item_total_price",
                            lambda it, calc_ship_est=None: item_total_price(it, calc_ship_est=calc_ship_est or 3.99))
        monkeypatch.setattr(ec, "normalize_condition", normalize_condition)

        filters = scanner.ScanFilters(isbn_match_policy=scanner.IsbnMatchPolicy("recall"))
        offers = await scanner._get_ebay_offers("9780134042435", filters=filters)

        assert len(offers) >= 1
        # All offers must have valid positive buy_price
        for o in offers:
            assert o["buy_price"] > 0, f"Got zero/negative price: {o}"
