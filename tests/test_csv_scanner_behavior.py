from __future__ import annotations
import sys, types
import pytest
import app.csv_arb_scanner as scanner
import app.ebay_client as ebay_client

POLICY_CASES = [
    pytest.param("precision", "UNVERIFIED_KEYWORD", 0, id="precision-drops-keyword"),
    pytest.param("balanced",  "CONFIRMED",           1, id="balanced-keeps-confirmed"),
    pytest.param("balanced",  "UNVERIFIED_SUPER_DEAL",1, id="balanced-keeps-superdeal"),
    pytest.param("recall",    "UNVERIFIED_KEYWORD",  1, id="recall-keeps-keyword"),
]

@pytest.mark.asyncio
@pytest.mark.parametrize("policy,match_quality,expected", POLICY_CASES)
async def test_match_policy_filters(monkeypatch, policy, match_quality, expected):
    async def fake_browse(client, isbn, **kw):
        return [{"itemId":"i1","title":"Book","condition":"Good",
                 "_match_quality":match_quality,"_verification_reason":"test","_query_mode":"kw"}]
    monkeypatch.setattr(ebay_client, "browse_search_isbn", fake_browse)
    monkeypatch.setattr(ebay_client, "item_total_price", lambda it, calc_ship_est=None: 9.99)
    monkeypatch.setattr(ebay_client, "normalize_condition", lambda c, ci: "good")

    filters = scanner.ScanFilters(isbn_match_policy=scanner.IsbnMatchPolicy(policy))
    offers = await scanner._get_ebay_offers("9780132350884", filters=filters)
    assert len(offers) == expected

@pytest.mark.asyncio
async def test_buyback_only_works_without_amazon(monkeypatch):
    """buyback_only=True should return accepted results even when Amazon is unavailable."""
    async def no_amazon(asin): return {}
    async def fake_ebay(isbn, filters=None):
        return [{"source":"ebay","source_condition":"used","buy_price":4.0,"item_id":"x",
                 "title":"","url":"","image_url":"","description":"","seller_name":"","seller_feedback":None,
                 "match_quality":"CONFIRMED","match_reason":"","query_mode":"gtin","isbn_normalized":isbn}]
    async def no_bf(isbn): return []
    async def fake_buyback(isbn):
        return {"ok":True,"best_cash":22.0,"best_vendor":"BooksRun","best_url":"https://x.com",
                "offers":[{"vendor":"BooksRun","vendor_id":"booksrun","cash":22.0,"credit":0}]}

    # Provide calc_buyback_profit in buyback module
    fake_bb = types.ModuleType("app.buyback_client")
    fake_bb.calc_buyback_profit = lambda buy, cash: {"profit": round(cash-buy-3.99,2),"roi_pct":200.0}
    fake_bb.fetch_buyback_prices = fake_buyback
    sys.modules["app.buyback_client"] = fake_bb

    monkeypatch.setattr(scanner, "_get_amazon_prices", no_amazon)
    monkeypatch.setattr(scanner, "_get_ebay_offers", fake_ebay)
    monkeypatch.setattr(scanner, "_get_bookfinder_offers", no_bf)
    monkeypatch.setattr(scanner, "_get_buyback_prices", fake_buyback)

    results = await scanner._scan_one(
        "9780132350884", scanner.ScanFilters(buyback_only=True), scanner.DEFAULT_FEES)
    assert any(r.accepted for r in results), f"Expected accepted results, got: {[(r.accepted,r.reason) for r in results]}"

@pytest.mark.asyncio
async def test_buyback_filter_rejects_no_buyback_data(monkeypatch):
    """buyback_only=True should reject when no buyback data."""
    async def fake_amazon(asin):
        return {"used": {"buybox": {"total": 45.0}, "top2": []}, "new": {}}
    async def fake_ebay(isbn, filters=None):
        return [{"source":"ebay","source_condition":"used","buy_price":10.0,"item_id":"x2",
                 "title":"","url":"","image_url":"","description":"","seller_name":"","seller_feedback":None,
                 "match_quality":"CONFIRMED","match_reason":"","query_mode":"gtin","isbn_normalized":isbn}]
    async def no_bf(isbn): return []
    async def no_buyback(isbn): return {"ok": False}

    fake_bb = types.ModuleType("app.buyback_client")
    fake_bb.calc_buyback_profit = lambda buy, cash: {"profit": 0.0, "roi_pct": 0.0}
    fake_bb.fetch_buyback_prices = no_buyback
    sys.modules["app.buyback_client"] = fake_bb

    monkeypatch.setattr(scanner, "_get_amazon_prices", fake_amazon)
    monkeypatch.setattr(scanner, "_get_ebay_offers", fake_ebay)
    monkeypatch.setattr(scanner, "_get_bookfinder_offers", no_bf)
    monkeypatch.setattr(scanner, "_get_buyback_prices", no_buyback)

    results = await scanner._scan_one(
        "9780132350884", scanner.ScanFilters(buyback_only=True), scanner.DEFAULT_FEES)
    assert not any(r.accepted for r in results)
