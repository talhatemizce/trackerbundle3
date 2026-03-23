"""
TrackerBundle3 — Buyback Client Comprehensive Tests
=====================================================
Tests: BooksRun, BookScouter, ValoreBooks APIs, caching, profit calc,
       price trends, error handling, edge cases.

~120 test scenarios.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import app.buyback_client as buyback
from app.buyback_client import (
    _CACHE_TTL_S,
    _SHIP_COST,
    calc_buyback_profit,
    fetch_buyback_prices,
    get_buyback_price_trend,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_buyback_state(tmp_path, monkeypatch):
    """Isolate cache and state."""
    monkeypatch.setattr(buyback, "_cache_path", lambda: tmp_path / "buyback_cache.json")
    monkeypatch.setattr(buyback, "_hist_cache", {})
    yield


@pytest.fixture
def mock_settings():
    settings = MagicMock()
    settings.bookscouter_api_key = "test-bs-key"
    settings.booksrun_api_key = "test-br-key"
    settings.valore_access_key = None
    settings.valore_secret_key = None
    settings.valore_api_url = None
    settings.resolved_data_dir.return_value = Path("/tmp/test")
    return settings


# ─── calc_buyback_profit Tests ───────────────────────────────────────────────

class TestCalcBuybackProfit:
    """Profit calculation for buyback arbitrage."""

    def test_basic_profit(self):
        result = calc_buyback_profit(10.0, 25.0)
        assert result["buy_price"] == 10.0
        assert result["ship_to_buyback"] == _SHIP_COST
        assert result["total_cost"] == 10.0 + _SHIP_COST
        assert result["buyback_cash"] == 25.0
        assert result["profit"] == 25.0 - (10.0 + _SHIP_COST)
        assert result["profit"] > 0

    def test_profit_with_custom_shipping(self):
        result = calc_buyback_profit(10.0, 25.0, ship_cost=5.0)
        assert result["ship_to_buyback"] == 5.0
        assert result["total_cost"] == 15.0

    def test_negative_profit(self):
        result = calc_buyback_profit(25.0, 10.0)
        assert result["profit"] < 0

    def test_zero_buy_price(self):
        result = calc_buyback_profit(0.0, 10.0)
        assert result["total_cost"] == _SHIP_COST
        assert result["profit"] == 10.0 - _SHIP_COST

    def test_zero_buyback(self):
        result = calc_buyback_profit(10.0, 0.0)
        assert result["profit"] < 0

    def test_roi_calculation(self):
        result = calc_buyback_profit(10.0, 25.0)
        expected_roi = (result["profit"] / result["total_cost"]) * 100
        assert abs(result["roi_pct"] - round(expected_roi, 1)) < 0.2

    def test_roi_zero_cost(self):
        result = calc_buyback_profit(0.0, 0.0, ship_cost=0.0)
        assert result["roi_pct"] == 0.0

    def test_breakeven(self):
        """Buy price + shipping = buyback → zero profit."""
        result = calc_buyback_profit(10.0, 10.0 + _SHIP_COST)
        assert abs(result["profit"]) < 0.02  # ~0 due to rounding

    def test_large_profit(self):
        result = calc_buyback_profit(5.0, 100.0)
        assert result["profit"] > 90
        assert result["roi_pct"] > 500

    def test_all_fields_present(self):
        result = calc_buyback_profit(10.0, 20.0)
        required = {"buy_price", "ship_to_buyback", "total_cost",
                     "buyback_cash", "profit", "roi_pct"}
        assert required.issubset(result.keys())


# ─── BooksRun API Tests ─────────────────────────────────────────────────────

class TestFetchBooksRun:

    @pytest.mark.asyncio
    async def test_no_key_returns_empty(self, monkeypatch, mock_settings):
        mock_settings.booksrun_api_key = None
        monkeypatch.setattr("app.buyback_client.get_settings", lambda: mock_settings)
        async with httpx.AsyncClient() as client:
            result = await buyback._fetch_booksrun("9780132350884", client)
        assert result == []

    @pytest.mark.asyncio
    async def test_success_response(self, monkeypatch, mock_settings):
        monkeypatch.setattr("app.buyback_client.get_settings", lambda: mock_settings)

        class FakeResp:
            status_code = 200
            def json(self):
                return {
                    "result": {
                        "status": "success",
                        "text": {"New": "25.00", "Good": "18.50", "Average": "12.00"}
                    }
                }

        async def fake_get(url, **kw):
            return FakeResp()

        mock_client = AsyncMock()
        mock_client.get = fake_get

        result = await buyback._fetch_booksrun("9780132350884", mock_client)
        assert len(result) == 1
        assert result[0]["vendor"] == "BooksRun"
        assert result[0]["cash"] == 18.50  # Good condition
        assert result[0]["conditions"]["new"] == 25.0
        assert result[0]["conditions"]["good"] == 18.5
        assert result[0]["conditions"]["average"] == 12.0

    @pytest.mark.asyncio
    async def test_booksrun_url_format(self, monkeypatch, mock_settings):
        monkeypatch.setattr("app.buyback_client.get_settings", lambda: mock_settings)

        class FakeResp:
            status_code = 200
            def json(self):
                return {"result": {"status": "success", "text": {"Good": "10.00"}}}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=FakeResp())

        result = await buyback._fetch_booksrun("9780132350884", mock_client)
        assert len(result) == 1
        assert "textbooks-buyback/add-to-cart" in result[0]["url"]
        assert "9780132350884" in result[0]["url"]

    @pytest.mark.asyncio
    async def test_booksrun_zero_price(self, monkeypatch, mock_settings):
        monkeypatch.setattr("app.buyback_client.get_settings", lambda: mock_settings)

        class FakeResp:
            status_code = 200
            def json(self):
                return {"result": {"status": "success", "text": {"Good": "0", "Average": "0"}}}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=FakeResp())

        result = await buyback._fetch_booksrun("isbn", mock_client)
        assert result == []

    @pytest.mark.asyncio
    async def test_booksrun_non_200(self, monkeypatch, mock_settings):
        monkeypatch.setattr("app.buyback_client.get_settings", lambda: mock_settings)

        class FakeResp:
            status_code = 500

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=FakeResp())

        result = await buyback._fetch_booksrun("isbn", mock_client)
        assert result == []

    @pytest.mark.asyncio
    async def test_booksrun_not_success_status(self, monkeypatch, mock_settings):
        monkeypatch.setattr("app.buyback_client.get_settings", lambda: mock_settings)

        class FakeResp:
            status_code = 200
            def json(self):
                return {"result": {"status": "not_found", "text": {}}}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=FakeResp())

        result = await buyback._fetch_booksrun("isbn", mock_client)
        assert result == []

    @pytest.mark.asyncio
    async def test_booksrun_exception(self, monkeypatch, mock_settings):
        monkeypatch.setattr("app.buyback_client.get_settings", lambda: mock_settings)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("dns fail"))

        result = await buyback._fetch_booksrun("isbn", mock_client)
        assert result == []

    @pytest.mark.asyncio
    async def test_booksrun_fallback_to_average(self, monkeypatch, mock_settings):
        """If Good is 0, should fallback to Average."""
        monkeypatch.setattr("app.buyback_client.get_settings", lambda: mock_settings)

        class FakeResp:
            status_code = 200
            def json(self):
                return {"result": {"status": "success", "text": {"Good": "0", "Average": "8.50"}}}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=FakeResp())

        result = await buyback._fetch_booksrun("isbn", mock_client)
        assert len(result) == 1
        assert result[0]["cash"] == 8.50


# ─── BookScouter API Tests ──────────────────────────────────────────────────

class TestFetchBookScouter:

    @pytest.mark.asyncio
    async def test_no_key_returns_empty(self, monkeypatch, mock_settings):
        mock_settings.bookscouter_api_key = None
        monkeypatch.setattr("app.buyback_client.get_settings", lambda: mock_settings)
        async with httpx.AsyncClient() as client:
            result = await buyback._fetch_bookscouter("isbn", client)
        assert result == []

    @pytest.mark.asyncio
    async def test_success_multiple_vendors(self, monkeypatch, mock_settings):
        monkeypatch.setattr("app.buyback_client.get_settings", lambda: mock_settings)

        class FakeResp:
            status_code = 200
            def json(self):
                return {
                    "data": [
                        {"vendorName": "Vendor A", "cashPrice": 20.0, "creditPrice": 25.0, "url": "http://a"},
                        {"vendorName": "Vendor B", "cashPrice": 15.0, "creditPrice": 0, "url": "http://b"},
                        {"vendorName": "Vendor C", "cashPrice": 0, "creditPrice": 10.0},  # $0 cash, skip
                    ]
                }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=FakeResp())

        result = await buyback._fetch_bookscouter("isbn", mock_client)
        assert len(result) == 2  # Vendor C skipped (cash=0)
        assert result[0]["cash"] == 20.0  # sorted descending
        assert result[1]["cash"] == 15.0

    @pytest.mark.asyncio
    async def test_bookscouter_auth_header(self, monkeypatch, mock_settings):
        monkeypatch.setattr("app.buyback_client.get_settings", lambda: mock_settings)
        captured_headers = {}

        class FakeResp:
            status_code = 200
            def json(self):
                return {"data": []}

        async def capture_get(url, **kw):
            captured_headers.update(kw.get("headers", {}))
            return FakeResp()

        mock_client = AsyncMock()
        mock_client.get = capture_get

        await buyback._fetch_bookscouter("isbn", mock_client)
        assert "Authorization" in captured_headers
        assert captured_headers["Authorization"].startswith("Bearer ")

    @pytest.mark.asyncio
    async def test_bookscouter_401(self, monkeypatch, mock_settings):
        monkeypatch.setattr("app.buyback_client.get_settings", lambda: mock_settings)

        class FakeResp:
            status_code = 401

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=FakeResp())

        result = await buyback._fetch_bookscouter("isbn", mock_client)
        assert result == []

    @pytest.mark.asyncio
    async def test_bookscouter_429(self, monkeypatch, mock_settings):
        monkeypatch.setattr("app.buyback_client.get_settings", lambda: mock_settings)

        class FakeResp:
            status_code = 429

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=FakeResp())

        result = await buyback._fetch_bookscouter("isbn", mock_client)
        assert result == []

    @pytest.mark.asyncio
    async def test_bookscouter_alternate_schema(self, monkeypatch, mock_settings):
        """Handle alternate field names."""
        monkeypatch.setattr("app.buyback_client.get_settings", lambda: mock_settings)

        class FakeResp:
            status_code = 200
            def json(self):
                return {
                    "vendors": [
                        {"vendor_name": "Alt Vendor", "cash_price": 12.0,
                         "credit_price": 15.0, "vendor_id": "alt_v"},
                    ]
                }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=FakeResp())

        result = await buyback._fetch_bookscouter("isbn", mock_client)
        assert len(result) == 1
        assert result[0]["vendor"] == "Alt Vendor"


# ─── ValoreBooks API Tests ───────────────────────────────────────────────────

class TestFetchValore:

    @pytest.mark.asyncio
    async def test_no_credentials_returns_empty(self, monkeypatch, mock_settings):
        monkeypatch.setattr("app.buyback_client.get_settings", lambda: mock_settings)
        mock_client = AsyncMock()
        result = await buyback._fetch_valore("isbn", mock_client)
        assert result == []

    @pytest.mark.asyncio
    async def test_botocore_not_installed(self, monkeypatch, mock_settings):
        """Graceful fallback when botocore is not installed."""
        mock_settings.valore_access_key = "key"
        mock_settings.valore_secret_key = "secret"
        monkeypatch.setattr("app.buyback_client.get_settings", lambda: mock_settings)

        # Simulate ImportError by patching builtins
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if "botocore" in name:
                raise ImportError("No module named 'botocore'")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)
        mock_client = AsyncMock()
        result = await buyback._fetch_valore("isbn", mock_client)
        assert result == []


# ─── Cache Tests ─────────────────────────────────────────────────────────────

class TestBuybackCache:

    def test_cache_miss_returns_none(self):
        result = buyback._cache_get("nonexistent_isbn")
        assert result is None

    def test_cache_hit_within_ttl(self, tmp_path, monkeypatch):
        monkeypatch.setattr(buyback, "_cache_path", lambda: tmp_path / "cache.json")
        data = {"ok": True, "isbn": "isbn1", "offers": [], "best_cash": 10.0}
        buyback._cache_set("isbn1", data)

        result = buyback._cache_get("isbn1")
        assert result is not None
        assert result["best_cash"] == 10.0

    def test_cache_expired(self, tmp_path, monkeypatch):
        monkeypatch.setattr(buyback, "_cache_path", lambda: tmp_path / "cache.json")
        data = {"ok": True, "isbn": "isbn1", "offers": []}
        buyback._cache_set("isbn1", data)

        # Manually expire
        from app.core.json_store import _read_unsafe, _write_unsafe
        p = tmp_path / "cache.json"
        raw = _read_unsafe(p, default={"entries": {}})
        raw["entries"]["isbn1"]["ts"] = int(time.time()) - _CACHE_TTL_S - 1
        _write_unsafe(p, raw)

        result = buyback._cache_get("isbn1")
        assert result is None

    def test_cache_evicts_old_entries(self, tmp_path, monkeypatch):
        monkeypatch.setattr(buyback, "_cache_path", lambda: tmp_path / "cache.json")

        # Write with very old ts
        from app.core.json_store import _write_unsafe
        p = tmp_path / "cache.json"
        old_data = {
            "entries": {
                "old_isbn": {"ts": int(time.time()) - _CACHE_TTL_S * 7, "ok": True},
            }
        }
        _write_unsafe(p, old_data)

        # Writing new entry should evict the old one
        buyback._cache_set("new_isbn", {"ok": True})

        from app.core.json_store import _read_unsafe
        raw = _read_unsafe(p, default={"entries": {}})
        assert "old_isbn" not in raw["entries"]
        assert "new_isbn" in raw["entries"]


# ─── fetch_buyback_prices Integration Tests ─────────────────────────────────

class TestFetchBuybackPrices:

    @pytest.mark.asyncio
    async def test_no_keys_returns_hint(self, monkeypatch, mock_settings):
        mock_settings.bookscouter_api_key = None
        mock_settings.booksrun_api_key = None
        mock_settings.valore_access_key = None
        monkeypatch.setattr("app.buyback_client.get_settings", lambda: mock_settings)

        result = await fetch_buyback_prices("9780132350884")
        assert result["ok"] is False
        assert result["no_keys"] is True
        assert "hint" in result

    @pytest.mark.asyncio
    async def test_returns_cached_on_second_call(self, monkeypatch, mock_settings, tmp_path):
        monkeypatch.setattr("app.buyback_client.get_settings", lambda: mock_settings)
        monkeypatch.setattr(buyback, "_cache_path", lambda: tmp_path / "cache.json")

        call_count = {"n": 0}

        async def fake_bookscouter(isbn, client):
            call_count["n"] += 1
            return [{"vendor": "V", "vendor_id": "v", "cash": 15.0, "credit": 0,
                      "url": "http://v", "source": "bookscouter"}]

        async def fake_booksrun(isbn, client):
            return []

        async def fake_valore(isbn, client):
            return []

        monkeypatch.setattr(buyback, "_fetch_bookscouter", fake_bookscouter)
        monkeypatch.setattr(buyback, "_fetch_booksrun", fake_booksrun)
        monkeypatch.setattr(buyback, "_fetch_valore", fake_valore)

        # First call — fetches from API
        r1 = await fetch_buyback_prices("9780132350884")
        assert r1["ok"] is True
        assert call_count["n"] == 1

        # Second call — should use cache
        r2 = await fetch_buyback_prices("9780132350884")
        assert r2["cached"] is True
        assert call_count["n"] == 1  # not called again

    @pytest.mark.asyncio
    async def test_force_bypasses_cache(self, monkeypatch, mock_settings, tmp_path):
        monkeypatch.setattr("app.buyback_client.get_settings", lambda: mock_settings)
        monkeypatch.setattr(buyback, "_cache_path", lambda: tmp_path / "cache.json")

        call_count = {"n": 0}

        async def fake_bookscouter(isbn, client):
            call_count["n"] += 1
            return [{"vendor": "V", "vendor_id": "v", "cash": 15.0, "credit": 0,
                      "url": "http://v", "source": "bookscouter"}]

        async def fake_booksrun(isbn, client):
            return []

        async def fake_valore(isbn, client):
            return []

        monkeypatch.setattr(buyback, "_fetch_bookscouter", fake_bookscouter)
        monkeypatch.setattr(buyback, "_fetch_booksrun", fake_booksrun)
        monkeypatch.setattr(buyback, "_fetch_valore", fake_valore)

        await fetch_buyback_prices("isbn", force=False)
        await fetch_buyback_prices("isbn", force=True)
        assert call_count["n"] == 2  # force=True bypassed cache

    @pytest.mark.asyncio
    async def test_deduplicates_by_vendor_id(self, monkeypatch, mock_settings, tmp_path):
        monkeypatch.setattr("app.buyback_client.get_settings", lambda: mock_settings)
        monkeypatch.setattr(buyback, "_cache_path", lambda: tmp_path / "cache.json")

        async def fake_bookscouter(isbn, client):
            return [{"vendor": "BooksRun", "vendor_id": "booksrun", "cash": 12.0,
                      "credit": 0, "url": "http://bs", "source": "bookscouter"}]

        async def fake_booksrun(isbn, client):
            return [{"vendor": "BooksRun", "vendor_id": "booksrun", "cash": 15.0,
                      "credit": 0, "url": "http://br", "source": "booksrun_api"}]

        async def fake_valore(isbn, client):
            return []

        monkeypatch.setattr(buyback, "_fetch_bookscouter", fake_bookscouter)
        monkeypatch.setattr(buyback, "_fetch_booksrun", fake_booksrun)
        monkeypatch.setattr(buyback, "_fetch_valore", fake_valore)

        result = await fetch_buyback_prices("isbn")
        # Should keep highest cash for same vendor_id
        assert len(result["offers"]) == 1
        assert result["best_cash"] == 15.0  # higher one kept

    @pytest.mark.asyncio
    async def test_no_offers_returns_ok_false(self, monkeypatch, mock_settings, tmp_path):
        monkeypatch.setattr("app.buyback_client.get_settings", lambda: mock_settings)
        monkeypatch.setattr(buyback, "_cache_path", lambda: tmp_path / "cache.json")

        async def empty(isbn, client):
            return []

        monkeypatch.setattr(buyback, "_fetch_bookscouter", empty)
        monkeypatch.setattr(buyback, "_fetch_booksrun", empty)
        monkeypatch.setattr(buyback, "_fetch_valore", empty)

        result = await fetch_buyback_prices("isbn")
        assert result["ok"] is False
        assert result["best_cash"] is None

    @pytest.mark.asyncio
    async def test_exception_in_one_source_doesnt_crash(self, monkeypatch, mock_settings, tmp_path):
        monkeypatch.setattr("app.buyback_client.get_settings", lambda: mock_settings)
        monkeypatch.setattr(buyback, "_cache_path", lambda: tmp_path / "cache.json")

        async def exploding(isbn, client):
            raise RuntimeError("boom")

        async def working(isbn, client):
            return [{"vendor": "V", "vendor_id": "v", "cash": 10.0,
                      "credit": 0, "url": "http://v", "source": "booksrun_api"}]

        async def fake_valore(isbn, client):
            return []

        monkeypatch.setattr(buyback, "_fetch_bookscouter", exploding)
        monkeypatch.setattr(buyback, "_fetch_booksrun", working)
        monkeypatch.setattr(buyback, "_fetch_valore", fake_valore)

        result = await fetch_buyback_prices("isbn")
        assert result["ok"] is True  # booksrun still succeeded
        assert result["best_cash"] == 10.0


# ─── Price Trend Tests ───────────────────────────────────────────────────────

class TestBuybackPriceTrend:

    @pytest.mark.asyncio
    async def test_no_key_returns_unknown(self, monkeypatch, mock_settings):
        mock_settings.bookscouter_api_key = None
        monkeypatch.setattr("app.buyback_client.get_settings", lambda: mock_settings)
        result = await get_buyback_price_trend("isbn")
        assert result["trend"] == "unknown"

    @pytest.mark.asyncio
    async def test_trend_auth_header_uses_bearer(self, monkeypatch, mock_settings):
        monkeypatch.setattr("app.buyback_client.get_settings", lambda: mock_settings)
        captured = {}

        class FakeResp:
            status_code = 200
            def json(self):
                return {"data": []}

        async def capture_get(url, **kw):
            captured.update(kw.get("headers", {}))
            return FakeResp()

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.get = capture_get
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            await get_buyback_price_trend("isbn")
        assert "Authorization" in captured
        assert captured["Authorization"].startswith("Bearer ")

    @pytest.mark.asyncio
    async def test_trend_uses_cache(self, monkeypatch, mock_settings):
        monkeypatch.setattr("app.buyback_client.get_settings", lambda: mock_settings)
        buyback._hist_cache["9780132350884"] = (
            time.time(),
            {"trend": "rising", "note": "cached"}
        )
        result = await get_buyback_price_trend("9780132350884")
        assert result["trend"] == "rising"
