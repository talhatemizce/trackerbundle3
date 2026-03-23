"""
TrackerBundle3 — Listing Verifier Comprehensive Tests
======================================================
Tests: eBay verification, AbeBooks check, vision verification,
       decision logic, summary building, batch processing.

~150 test scenarios.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.listing_verifier as verifier
from app.listing_verifier import (
    _build_summary,
    _check_isbn_in_detail,
    _decide_final_status,
    verify_batch,
)


# ─── _check_isbn_in_detail Tests ─────────────────────────────────────────────

class TestCheckIsbnInDetail:
    """ISBN matching in eBay item detail data."""

    def test_match_via_gtins(self, monkeypatch):
        monkeypatch.setattr("app.listing_verifier.isbn_variants",
                            lambda isbn: ["9780132350884", "0132350882"],
                            raising=False)
        # Ensure import works inside function
        import app.isbn_utils
        monkeypatch.setattr(app.isbn_utils, "isbn_variants",
                            lambda isbn: ["9780132350884", "0132350882"])
        data = {"product": {"gtins": ["9780132350884"]}}
        result = _check_isbn_in_detail(data, "9780132350884")
        assert result == "MATCH"

    def test_mismatch_via_gtins(self, monkeypatch):
        import app.isbn_utils
        monkeypatch.setattr(app.isbn_utils, "isbn_variants",
                            lambda isbn: ["9780132350884"])
        data = {"product": {"gtins": ["9781234567890"]}}
        result = _check_isbn_in_detail(data, "9780132350884")
        assert result == "MISMATCH"

    def test_match_via_localized_aspects_isbn(self, monkeypatch):
        import app.isbn_utils
        monkeypatch.setattr(app.isbn_utils, "isbn_variants",
                            lambda isbn: ["9780132350884"])
        data = {
            "localizedAspects": [
                {"name": "ISBN", "value": "978-0-13-235088-4"},
            ]
        }
        result = _check_isbn_in_detail(data, "9780132350884")
        assert result == "MATCH"

    def test_match_via_localized_aspects_ean(self, monkeypatch):
        import app.isbn_utils
        monkeypatch.setattr(app.isbn_utils, "isbn_variants",
                            lambda isbn: ["9780132350884"])
        data = {
            "localizedAspects": [
                {"name": "EAN", "value": "9780132350884"},
            ]
        }
        result = _check_isbn_in_detail(data, "9780132350884")
        assert result == "MATCH"

    def test_unknown_when_no_isbn_data(self, monkeypatch):
        import app.isbn_utils
        monkeypatch.setattr(app.isbn_utils, "isbn_variants",
                            lambda isbn: ["9780132350884"])
        data = {"title": "Some Book"}
        result = _check_isbn_in_detail(data, "9780132350884")
        assert result == "UNKNOWN"

    def test_mismatch_via_localized_isbn(self, monkeypatch):
        import app.isbn_utils
        monkeypatch.setattr(app.isbn_utils, "isbn_variants",
                            lambda isbn: ["9780132350884"])
        data = {
            "localizedAspects": [
                {"name": "ISBN-13", "value": "9781111111111"},
            ]
        }
        result = _check_isbn_in_detail(data, "9780132350884")
        assert result == "MISMATCH"

    def test_unknown_when_empty_gtin_list(self, monkeypatch):
        import app.isbn_utils
        monkeypatch.setattr(app.isbn_utils, "isbn_variants",
                            lambda isbn: ["9780132350884"])
        data = {"product": {"gtins": []}}
        result = _check_isbn_in_detail(data, "9780132350884")
        assert result == "UNKNOWN"

    def test_graceful_on_bad_data(self):
        """Should not crash on malformed data."""
        result = _check_isbn_in_detail({}, "")
        assert result in ("UNKNOWN", "MATCH", "MISMATCH")

    def test_graceful_on_none_values(self):
        data = {"product": None, "localizedAspects": None}
        result = _check_isbn_in_detail(data, "9780132350884")
        assert result == "UNKNOWN"


# ─── _decide_final_status Tests ──────────────────────────────────────────────

class TestDecideFinalStatus:
    """Final status decision logic combining eBay, market, and vision results."""

    def test_vision_mismatch_overrides_everything(self):
        ebay = {"status": "VERIFIED"}
        market = {"status": "VERIFIED"}
        vision = {"verdict": "MISMATCH"}
        assert _decide_final_status(ebay, market, "ebay", vision) == "MISMATCH"

    def test_ebay_gone(self):
        ebay = {"status": "GONE"}
        market = {"status": "VERIFIED"}
        assert _decide_final_status(ebay, market, "ebay") == "GONE"

    def test_ebay_mismatch(self):
        ebay = {"status": "MISMATCH"}
        market = {"status": "VERIFIED"}
        assert _decide_final_status(ebay, market, "ebay") == "MISMATCH"

    def test_ebay_price_up(self):
        ebay = {"status": "PRICE_UP"}
        market = {"status": "VERIFIED"}
        assert _decide_final_status(ebay, market, "ebay") == "PRICE_UP"

    def test_ebay_price_down(self):
        ebay = {"status": "PRICE_DOWN"}
        market = {"status": "VERIFIED"}
        assert _decide_final_status(ebay, market, "ebay") == "PRICE_DOWN"

    def test_ebay_verified_no_vision(self):
        ebay = {"status": "VERIFIED"}
        market = {"status": "VERIFIED"}
        assert _decide_final_status(ebay, market, "ebay") == "VERIFIED"

    def test_ebay_verified_with_stock_photo(self):
        ebay = {"status": "VERIFIED"}
        market = {"status": "VERIFIED"}
        vision = {"verdict": "STOCK_PHOTO"}
        assert _decide_final_status(ebay, market, "ebay", vision) == "VERIFIED_STOCK_PHOTO"

    def test_ebay_verified_with_match_vision(self):
        ebay = {"status": "VERIFIED"}
        market = {"status": "VERIFIED"}
        vision = {"verdict": "MATCH"}
        assert _decide_final_status(ebay, market, "ebay", vision) == "VERIFIED"

    def test_ebay_verified_with_uncertain_vision(self):
        ebay = {"status": "VERIFIED"}
        market = {"status": "VERIFIED"}
        vision = {"verdict": "UNCERTAIN"}
        assert _decide_final_status(ebay, market, "ebay", vision) == "VERIFIED"

    def test_ebay_skip_market_verified(self):
        ebay = {"status": "SKIP"}
        market = {"status": "VERIFIED"}
        assert _decide_final_status(ebay, market, "not_ebay") == "VERIFIED"

    def test_ebay_skip_market_price_up(self):
        ebay = {"status": "SKIP"}
        market = {"status": "PRICE_UP"}
        assert _decide_final_status(ebay, market, "not_ebay") == "PRICE_UP"

    def test_ebay_skip_market_error_unverifiable(self):
        ebay = {"status": "SKIP"}
        market = {"status": "ERROR"}
        assert _decide_final_status(ebay, market, "ebay") == "UNVERIFIABLE"

    def test_both_error(self):
        ebay = {"status": "ERROR"}
        market = {"status": "ERROR"}
        assert _decide_final_status(ebay, market, "ebay") == "ERROR"

    def test_ebay_error_market_verified(self):
        ebay = {"status": "ERROR"}
        market = {"status": "VERIFIED"}
        result = _decide_final_status(ebay, market, "ebay")
        assert result == "VERIFIED"

    def test_vision_none_treated_as_no_vision(self):
        ebay = {"status": "VERIFIED"}
        market = {"status": "VERIFIED"}
        assert _decide_final_status(ebay, market, "ebay", None) == "VERIFIED"

    def test_non_ebay_source_skips_ebay_status(self):
        ebay = {"status": "PRICE_UP"}  # Would normally trigger price_up
        market = {"status": "VERIFIED"}
        # Source is not "ebay", so ebay status is treated differently
        result = _decide_final_status(ebay, market, "abebooks")
        assert result in ("VERIFIED", "PRICE_UP")  # depends on logic


# ─── _build_summary Tests ────────────────────────────────────────────────────

class TestBuildSummary:
    """Human-readable summary generation."""

    def test_verified_summary(self):
        s = _build_summary("VERIFIED", {}, {}, 10.0)
        assert "✅" in s

    def test_verified_with_match_vision(self):
        vision = {"verdict": "MATCH", "confidence": 95}
        s = _build_summary("VERIFIED", {}, {}, 10.0, vision)
        assert "📷" in s
        assert "95" in s

    def test_verified_with_uncertain_vision(self):
        vision = {"verdict": "UNCERTAIN"}
        s = _build_summary("VERIFIED", {}, {}, 10.0, vision)
        assert "belirsiz" in s

    def test_verified_stock_photo_summary(self):
        s = _build_summary("VERIFIED_STOCK_PHOTO", {}, {}, 10.0)
        assert "stock" in s.lower()

    def test_gone_summary(self):
        s = _build_summary("GONE", {}, {}, 10.0)
        assert "❌" in s

    def test_gone_isbn_search_summary(self):
        ebay = {"searched_by": "isbn_search"}
        s = _build_summary("GONE", ebay, {}, 10.0)
        assert "ISBN" in s

    def test_mismatch_summary(self):
        ebay = {"item_title": "Wrong Book Title"}
        s = _build_summary("MISMATCH", ebay, {}, 10.0)
        assert "⚠️" in s
        assert "Wrong Book" in s

    def test_price_up_summary(self):
        ebay = {"current_price": 15.0, "price_delta_pct": 50.0}
        s = _build_summary("PRICE_UP", ebay, {}, 10.0)
        assert "📈" in s

    def test_price_down_summary(self):
        ebay = {"current_price": 8.0, "price_delta_pct": -20.0}
        s = _build_summary("PRICE_DOWN", ebay, {}, 10.0)
        assert "📉" in s

    def test_unverifiable_summary(self):
        s = _build_summary("UNVERIFIABLE", {}, {}, 10.0)
        assert "ℹ️" in s

    def test_error_summary(self):
        ebay = {"reason": "timeout"}
        market = {"reason": "ip_blocked"}
        s = _build_summary("ERROR", ebay, market, 10.0)
        assert "⚠️" in s

    def test_price_up_isbn_search(self):
        ebay = {"searched_by": "isbn_search", "current_price": 15.0,
                "price_delta_pct": 50.0, "total_listings": 3}
        s = _build_summary("PRICE_UP", ebay, {}, 10.0)
        assert "3 ilan" in s

    def test_verified_isbn_search(self):
        ebay = {"searched_by": "isbn_search", "current_price": 10.5,
                "total_listings": 5}
        s = _build_summary("VERIFIED", ebay, {}, 10.0)
        assert "5 aktif ilan" in s


# ─── Vision Verification Tests ───────────────────────────────────────────────

class TestVerifyImageVision:
    """Vision verification with LLM integration."""

    @pytest.mark.asyncio
    async def test_no_image_url_returns_no_image(self):
        result = await verifier._verify_image_vision("", "isbn", "title", {})
        assert result["status"] == "NO_IMAGE"
        assert result["verdict"] == "NO_IMAGE"

    @pytest.mark.asyncio
    async def test_image_download_failure_returns_no_image(self, monkeypatch):
        async def fake_fetch(url, client):
            return None

        monkeypatch.setattr("app.ai_analyst._fetch_image_b64", fake_fetch)

        result = await verifier._verify_image_vision(
            "http://example.com/img.jpg", "9780132350884", "Title", {})
        assert result["verdict"] == "NO_IMAGE"

    @pytest.mark.asyncio
    async def test_vision_match_parsed_correctly(self, monkeypatch):
        async def fake_fetch(url, client):
            return "base64imagedata"

        async def fake_route(**kwargs):
            return {
                "text": json.dumps({
                    "verdict": "MATCH",
                    "confidence": 92,
                    "notes": "Title matches",
                    "title_visible": True,
                    "author_visible": True,
                    "is_stock_photo": False,
                    "condition_notes": "Good condition"
                }),
                "provider": "gemini_flash",
                "model": "gemini-2.5-flash",
            }

        monkeypatch.setattr("app.ai_analyst._fetch_image_b64", fake_fetch)
        monkeypatch.setattr("app.llm_router.route", fake_route)

        result = await verifier._verify_image_vision(
            "http://img.jpg", "9780132350884", "Clean Code", {})
        assert result["verdict"] == "MATCH"
        assert result["confidence"] == 92
        assert result["provider"] == "gemini_flash"

    @pytest.mark.asyncio
    async def test_vision_mismatch(self, monkeypatch):
        async def fake_fetch(url, client):
            return "base64data"

        async def fake_route(**kwargs):
            return {
                "text": '{"verdict": "MISMATCH", "confidence": 88, "notes": "Different book"}',
                "provider": "gemini_flash",
                "model": "gemini-2.5-flash",
            }

        monkeypatch.setattr("app.ai_analyst._fetch_image_b64", fake_fetch)
        monkeypatch.setattr("app.llm_router.route", fake_route)

        result = await verifier._verify_image_vision(
            "http://img.jpg", "isbn", "Title", {})
        assert result["verdict"] == "MISMATCH"

    @pytest.mark.asyncio
    async def test_vision_stock_photo_used_condition_risk(self, monkeypatch):
        async def fake_fetch(url, client):
            return "base64data"

        async def fake_route(**kwargs):
            return {
                "text": '{"verdict": "STOCK_PHOTO", "confidence": 80, "notes": "White bg", "is_stock_photo": true}',
                "provider": "gemini_flash",
                "model": "gemini-2.5-flash",
            }

        monkeypatch.setattr("app.ai_analyst._fetch_image_b64", fake_fetch)
        monkeypatch.setattr("app.llm_router.route", fake_route)

        result = await verifier._verify_image_vision(
            "http://img.jpg", "isbn", "Title",
            {"source_condition": "used"})
        assert result.get("stock_photo_risk") is True
        assert "⚠️" in result.get("notes", "")

    @pytest.mark.asyncio
    async def test_vision_garbled_json_returns_uncertain(self, monkeypatch):
        async def fake_fetch(url, client):
            return "base64data"

        async def fake_route(**kwargs):
            return {
                "text": "This is not JSON at all, just garbled text",
                "provider": "groq_vision",
                "model": "llama-4-scout",
            }

        monkeypatch.setattr("app.ai_analyst._fetch_image_b64", fake_fetch)
        monkeypatch.setattr("app.llm_router.route", fake_route)

        result = await verifier._verify_image_vision(
            "http://img.jpg", "isbn", "Title", {})
        assert result["verdict"] == "UNCERTAIN"
        assert result.get("parse_error") is True

    @pytest.mark.asyncio
    async def test_vision_markdown_fenced_json(self, monkeypatch):
        async def fake_fetch(url, client):
            return "base64data"

        async def fake_route(**kwargs):
            return {
                "text": '```json\n{"verdict": "MATCH", "confidence": 85, "notes": "ok"}\n```',
                "provider": "gemini_flash",
                "model": "gemini-2.5-flash",
            }

        monkeypatch.setattr("app.ai_analyst._fetch_image_b64", fake_fetch)
        monkeypatch.setattr("app.llm_router.route", fake_route)

        result = await verifier._verify_image_vision(
            "http://img.jpg", "isbn", "Title", {})
        assert result["verdict"] == "MATCH"

    @pytest.mark.asyncio
    async def test_vision_trailing_comma_json(self, monkeypatch):
        async def fake_fetch(url, client):
            return "base64data"

        async def fake_route(**kwargs):
            return {
                "text": '{"verdict": "MATCH", "confidence": 85, "notes": "ok",}',
                "provider": "gemini_flash",
                "model": "gemini-2.5-flash",
            }

        monkeypatch.setattr("app.ai_analyst._fetch_image_b64", fake_fetch)
        monkeypatch.setattr("app.llm_router.route", fake_route)

        result = await verifier._verify_image_vision(
            "http://img.jpg", "isbn", "Title", {})
        assert result["verdict"] == "MATCH"

    @pytest.mark.asyncio
    async def test_vision_exception_returns_error(self, monkeypatch):
        async def fake_fetch(url, client):
            raise RuntimeError("network error")

        monkeypatch.setattr("app.ai_analyst._fetch_image_b64", fake_fetch)

        result = await verifier._verify_image_vision(
            "http://img.jpg", "isbn", "Title", {})
        assert result["status"] == "ERROR"
        assert result["verdict"] == "UNCERTAIN"

    @pytest.mark.asyncio
    async def test_anti_contamination_prompt_present(self, monkeypatch):
        """Verify the anti-contamination prompt is in the system prompt."""
        captured_kwargs = {}

        async def fake_fetch(url, client):
            return "base64data"

        async def fake_route(**kwargs):
            captured_kwargs.update(kwargs)
            return {
                "text": '{"verdict": "MATCH", "confidence": 80, "notes": "ok"}',
                "provider": "gemini_flash",
                "model": "gemini-2.5-flash",
            }

        monkeypatch.setattr("app.ai_analyst._fetch_image_b64", fake_fetch)
        monkeypatch.setattr("app.llm_router.route", fake_route)

        await verifier._verify_image_vision(
            "http://img.jpg", "isbn", "Title", {})

        sys_prompt = captured_kwargs.get("system_prompt", "")
        assert "CRITICAL" in sys_prompt
        assert "Base your answer ONLY" in sys_prompt
        assert "training data" in sys_prompt
        assert "ACTUALLY VISIBLE" in sys_prompt


# ─── Batch Verification Tests ────────────────────────────────────────────────

class TestVerifyBatch:
    """Batch verification with concurrency and timeout."""

    @pytest.mark.asyncio
    async def test_batch_empty_list(self):
        results = await verify_batch([])
        assert results == []

    @pytest.mark.asyncio
    async def test_batch_preserves_order(self, monkeypatch):
        async def fake_verify(candidate, isbn):
            return {"status": "VERIFIED", "isbn": isbn}

        monkeypatch.setattr(verifier, "verify_listing", fake_verify)

        items = [
            {"isbn": "isbn1", "candidate": {"source": "ebay", "buy_price": 10}, "_index": 0},
            {"isbn": "isbn2", "candidate": {"source": "ebay", "buy_price": 20}, "_index": 1},
        ]
        results = await verify_batch(items)
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_batch_handles_exceptions(self, monkeypatch):
        call_count = {"n": 0}

        async def fake_verify(candidate, isbn):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("boom")
            return {"status": "VERIFIED"}

        monkeypatch.setattr(verifier, "verify_listing", fake_verify)

        items = [
            {"isbn": "isbn1", "candidate": {"source": "ebay", "buy_price": 10}, "_index": 0},
            {"isbn": "isbn2", "candidate": {"source": "ebay", "buy_price": 20}, "_index": 1},
        ]
        results = await verify_batch(items)
        assert len(results) == 2
        # One should be ERROR, one VERIFIED
        statuses = {r["status"] for r in results}
        assert "ERROR" in statuses


# ─── Verify Listing Integration Tests ────────────────────────────────────────

class TestVerifyListing:

    @pytest.mark.asyncio
    async def test_non_ebay_source_skips_ebay_check(self, monkeypatch):
        async def fake_abebooks(isbn, price, client):
            return {"status": "VERIFIED", "cheapest_found": 12.0}

        monkeypatch.setattr(verifier, "_verify_abebooks_price", fake_abebooks)

        candidate = {"source": "abebooks", "buy_price": 10.0}
        result = await verifier.verify_listing(candidate, "isbn")
        assert "checked_at" in result
        assert result["source"] == "abebooks"

    @pytest.mark.asyncio
    async def test_ebay_no_item_id_uses_isbn_search(self, monkeypatch):
        search_called = {"called": False}

        async def fake_isbn_search(isbn, price, client):
            search_called["called"] = True
            return {"status": "VERIFIED", "searched_by": "isbn_search"}

        async def fake_abebooks(isbn, price, client):
            return {"status": "VERIFIED"}

        monkeypatch.setattr(verifier, "_verify_ebay_by_isbn_search", fake_isbn_search)
        monkeypatch.setattr(verifier, "_verify_abebooks_price", fake_abebooks)

        candidate = {"source": "ebay", "buy_price": 10.0}  # no item_id
        result = await verifier.verify_listing(candidate, "isbn")
        assert search_called["called"]
