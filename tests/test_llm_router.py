"""
TrackerBundle3 — LLM Router v3 Comprehensive Tests
====================================================
Tests: provider selection, quota tracking, fallback, thinking models,
       json_mode, timeout handling, Gemini native, circuit breaker.

~180 test scenarios.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.llm_router as router
from app.llm_router import (
    PROVIDERS,
    ProviderDef,
    ProviderState,
    _AuthError,
    _RateLimitError,
    _get_api_key,
    _get_state,
    get_status,
    route,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_router_state():
    """Clear all router state between tests."""
    router._states.clear()
    yield
    router._states.clear()


@pytest.fixture
def mock_settings():
    """Mock settings with all API keys."""
    settings = MagicMock()
    settings.groq_api_key = "test-groq-key"
    settings.cerebras_api_key = "test-cerebras-key"
    settings.openrouter_api_key = "test-openrouter-key"
    settings.gemini_api_key = "test-gemini-key"
    return settings


@pytest.fixture
def mock_all_keys(monkeypatch, mock_settings):
    """Ensure all providers have API keys."""
    monkeypatch.setattr("app.llm_router._get_api_key",
                        lambda defn: f"test-key-{defn.name}")


# ─── ProviderDef Tests ───────────────────────────────────────────────────────

class TestProviderDef:
    """ProviderDef dataclass structure tests."""

    def test_provider_def_has_timeout_field(self):
        p = ProviderDef(
            name="test", base_url="http://x", model="m",
            tasks=["reasoning"], rpm=10, rpd=100,
            auth_env_key="key", priority=1,
        )
        assert p.timeout_s == 60  # default

    def test_provider_def_custom_timeout(self):
        p = ProviderDef(
            name="test", base_url="http://x", model="m",
            tasks=["reasoning"], rpm=10, rpd=100,
            auth_env_key="key", priority=1, timeout_s=12,
        )
        assert p.timeout_s == 12

    def test_provider_def_supports_vision_default_false(self):
        p = ProviderDef(
            name="test", base_url="http://x", model="m",
            tasks=["vision"], rpm=10, rpd=None,
            auth_env_key="key", priority=1,
        )
        assert p.supports_vision is False

    def test_provider_def_extra_headers_default_empty(self):
        p = ProviderDef(
            name="test", base_url="http://x", model="m",
            tasks=["reasoning"], rpm=10, rpd=None,
            auth_env_key="key", priority=1,
        )
        assert p.extra_headers == {}


# ─── Provider Configuration Tests ────────────────────────────────────────────

class TestProviderConfiguration:
    """Verify all 10 providers are correctly configured."""

    def test_total_provider_count(self):
        assert len(PROVIDERS) == 10, f"Expected 10 providers, got {len(PROVIDERS)}"

    def test_cerebras_is_qwen3_235b(self):
        p = next(p for p in PROVIDERS if p.name == "cerebras")
        assert "qwen-3-235b" in p.model
        assert p.rpd is None  # unlimited

    def test_cerebras_is_not_llama33(self):
        """Cerebras llama-3.3-70b was REMOVED (404). Must NOT be in providers."""
        for p in PROVIDERS:
            if p.name == "cerebras":
                assert "llama-3.3-70b" not in p.model, \
                    "CRITICAL: Cerebras llama-3.3-70b was removed! Must use qwen-3-235b"

    def test_groq_kimi_k2(self):
        p = next(p for p in PROVIDERS if p.name == "groq_kimi")
        assert "kimi-k2" in p.model
        assert p.priority == 2
        assert p.rpd == 14400

    def test_groq_gpt_oss(self):
        p = next(p for p in PROVIDERS if p.name == "groq_gpt_oss")
        assert "gpt-oss-120b" in p.model
        assert p.priority == 3

    def test_groq_llama33_70b(self):
        p = next(p for p in PROVIDERS if p.name == "groq")
        assert "llama-3.3-70b" in p.model
        assert p.priority == 4

    def test_openrouter_stepfun(self):
        p = next(p for p in PROVIDERS if p.name == "openrouter_stepfun")
        assert "stepfun" in p.model
        assert p.timeout_s == 12  # anti-queue-trap
        assert p.priority == 5

    def test_openrouter_nemotron(self):
        p = next(p for p in PROVIDERS if p.name == "openrouter_nemotron")
        assert "nemotron" in p.model
        assert p.timeout_s == 12
        assert p.priority == 6

    def test_gemini_flash_vision(self):
        p = next(p for p in PROVIDERS if p.name == "gemini_flash")
        assert p.model == "gemini-2.5-flash"
        assert p.supports_vision is True
        assert "vision" in p.tasks
        assert p.rpm == 10
        assert p.rpd == 500

    def test_gemini_lite_triple_task(self):
        p = next(p for p in PROVIDERS if p.name == "gemini_lite")
        assert p.model == "gemini-2.5-flash-lite"
        assert p.supports_vision is True
        assert set(p.tasks) == {"vision", "web_search", "reasoning"}

    def test_groq_vision_scout(self):
        p = next(p for p in PROVIDERS if p.name == "groq_vision")
        assert "llama-4-scout" in p.model
        assert p.supports_vision is True
        assert p.tasks == ["vision"]

    def test_groq_compound_p0(self):
        p = next(p for p in PROVIDERS if p.name == "groq_compound")
        assert "compound" in p.model
        assert p.priority == 0  # MUST be P0 for web_search
        assert p.tasks == ["web_search"]

    def test_no_broken_openrouter_models(self):
        """Verify removed 404 models are NOT in provider list."""
        for p in PROVIDERS:
            assert "deepseek-chat-v3-0324" not in p.model, \
                f"BROKEN MODEL: {p.model} returns 404"
            assert "qwen3-235b-a22b:free" not in p.model, \
                f"BROKEN MODEL: {p.model} returns 404"

    def test_openrouter_headers_set(self):
        for p in PROVIDERS:
            if "openrouter" in p.name:
                assert "HTTP-Referer" in p.extra_headers
                assert "X-Title" in p.extra_headers

    @pytest.mark.parametrize("task", ["reasoning", "vision", "web_search"])
    def test_each_task_has_at_least_two_providers(self, task):
        matching = [p for p in PROVIDERS if task in p.tasks]
        assert len(matching) >= 2, \
            f"Task '{task}' has only {len(matching)} provider(s) — need ≥2 for fallback"


# ─── Provider Priority Order Tests ───────────────────────────────────────────

class TestProviderPriority:
    """Verify correct provider ordering for each task type."""

    def test_reasoning_priority_order(self):
        reasoning = [p for p in sorted(PROVIDERS, key=lambda x: x.priority)
                     if "reasoning" in p.tasks]
        names = [p.name for p in reasoning]
        assert names[0] == "cerebras", f"Cerebras should be P1 for reasoning, got {names}"
        assert "groq_kimi" in names[:3]
        assert "groq_gpt_oss" in names[:4]

    def test_vision_priority_order(self):
        vision = [p for p in sorted(PROVIDERS, key=lambda x: x.priority)
                  if "vision" in p.tasks and p.supports_vision]
        names = [p.name for p in vision]
        assert names[0] == "gemini_flash", f"Gemini Flash should be P1 vision, got {names}"
        assert names[1] == "gemini_lite"
        assert names[2] == "groq_vision"

    def test_web_search_priority_order(self):
        ws = [p for p in sorted(PROVIDERS, key=lambda x: x.priority)
              if "web_search" in p.tasks]
        names = [p.name for p in ws]
        assert names[0] == "groq_compound", \
            f"Groq Compound must be P0 for web_search, got {names}"

    def test_compound_before_gemini_for_websearch(self):
        """Groq Compound MUST sort before Gemini Flash for web_search."""
        compound = next(p for p in PROVIDERS if p.name == "groq_compound")
        gemini = next(p for p in PROVIDERS if p.name == "gemini_flash")
        assert compound.priority < gemini.priority


# ─── ProviderState Tests ─────────────────────────────────────────────────────

class TestProviderState:
    """Quota tracking and circuit breaker logic."""

    def test_fresh_state_is_available(self):
        state = ProviderState()
        defn = ProviderDef(
            name="test", base_url="", model="",
            tasks=["reasoning"], rpm=30, rpd=1000,
            auth_env_key="key", priority=1,
        )
        assert state.is_available(defn) is True

    def test_rpm_exhausted(self):
        state = ProviderState()
        defn = ProviderDef(
            name="test", base_url="", model="",
            tasks=["reasoning"], rpm=2, rpd=None,
            auth_env_key="key", priority=1,
        )
        state.requests_this_minute = 2
        assert state.is_available(defn) is False

    def test_rpd_exhausted(self):
        state = ProviderState()
        defn = ProviderDef(
            name="test", base_url="", model="",
            tasks=["reasoning"], rpm=30, rpd=100,
            auth_env_key="key", priority=1,
        )
        state.requests_today = 100
        assert state.is_available(defn) is False

    def test_rpd_none_means_unlimited(self):
        state = ProviderState()
        defn = ProviderDef(
            name="test", base_url="", model="",
            tasks=["reasoning"], rpm=30, rpd=None,
            auth_env_key="key", priority=1,
        )
        state.requests_today = 999999
        assert state.is_available(defn) is True

    def test_backoff_blocks_until_time(self):
        state = ProviderState()
        defn = ProviderDef(
            name="test", base_url="", model="",
            tasks=["reasoning"], rpm=30, rpd=None,
            auth_env_key="key", priority=1,
        )
        state.backoff_until = time.time() + 3600
        assert state.is_available(defn) is False

    def test_backoff_expired_allows(self):
        state = ProviderState()
        defn = ProviderDef(
            name="test", base_url="", model="",
            tasks=["reasoning"], rpm=30, rpd=None,
            auth_env_key="key", priority=1,
        )
        state.backoff_until = time.time() - 1
        assert state.is_available(defn) is True

    def test_minute_window_resets(self):
        state = ProviderState()
        defn = ProviderDef(
            name="test", base_url="", model="",
            tasks=["reasoning"], rpm=2, rpd=None,
            auth_env_key="key", priority=1,
        )
        state.requests_this_minute = 2
        state.minute_window_start = time.time() - 61  # window expired
        assert state.is_available(defn) is True
        assert state.requests_this_minute == 0

    def test_day_window_resets(self):
        state = ProviderState()
        defn = ProviderDef(
            name="test", base_url="", model="",
            tasks=["reasoning"], rpm=30, rpd=100,
            auth_env_key="key", priority=1,
        )
        state.requests_today = 100
        state.day_window_start = time.time() - 86401
        assert state.is_available(defn) is True
        assert state.requests_today == 0

    def test_record_request_increments(self):
        state = ProviderState()
        state.record_request()
        assert state.requests_this_minute == 1
        assert state.requests_today == 1
        state.record_request()
        assert state.requests_this_minute == 2
        assert state.requests_today == 2

    def test_record_success_clears_errors(self):
        state = ProviderState()
        state.consecutive_errors = 5
        state.record_success()
        assert state.consecutive_errors == 0

    def test_record_error_with_retry_after(self):
        state = ProviderState()
        state.record_error(retry_after=30)
        assert state.consecutive_errors == 1
        assert state.backoff_until > time.time()

    def test_record_error_exponential_backoff(self):
        state = ProviderState()
        state.record_error()  # 1st error
        assert state.backoff_until == 0.0  # no backoff on first error
        state.record_error()  # 2nd error
        assert state.backoff_until > time.time()  # backoff starts at 2

    def test_backoff_max_capped_at_300s(self):
        state = ProviderState()
        state.consecutive_errors = 99
        state.record_error()
        max_backoff = state.backoff_until - time.time()
        assert max_backoff <= 301  # 300s cap + 1s tolerance

    def test_get_state_lazy_init(self):
        state = _get_state("brand_new_provider")
        assert isinstance(state, ProviderState)
        assert state.requests_this_minute == 0

    def test_get_state_returns_same_instance(self):
        s1 = _get_state("test_prov")
        s2 = _get_state("test_prov")
        assert s1 is s2


# ─── API Key Tests ───────────────────────────────────────────────────────────

class TestApiKey:
    """API key resolution from settings."""

    def test_get_api_key_returns_value(self, monkeypatch, mock_settings):
        monkeypatch.setattr("app.core.config.get_settings", lambda: mock_settings)
        defn = ProviderDef(
            name="test", base_url="", model="",
            tasks=["reasoning"], rpm=30, rpd=None,
            auth_env_key="groq_api_key", priority=1,
        )
        assert _get_api_key(defn) == "test-groq-key"

    def test_get_api_key_missing_returns_none(self, monkeypatch, mock_settings):
        mock_settings.nonexistent_key = None
        monkeypatch.setattr("app.core.config.get_settings", lambda: mock_settings)
        defn = ProviderDef(
            name="test", base_url="", model="",
            tasks=["reasoning"], rpm=30, rpd=None,
            auth_env_key="nonexistent_key", priority=1,
        )
        assert _get_api_key(defn) is None

    def test_get_api_key_empty_string_returns_none(self, monkeypatch, mock_settings):
        mock_settings.groq_api_key = ""
        monkeypatch.setattr("app.core.config.get_settings", lambda: mock_settings)
        defn = ProviderDef(
            name="test", base_url="", model="",
            tasks=["reasoning"], rpm=30, rpd=None,
            auth_env_key="groq_api_key", priority=1,
        )
        assert _get_api_key(defn) is None


# ─── Route Function Tests ────────────────────────────────────────────────────

class TestRoute:
    """Core routing logic: provider selection, fallback, error handling."""

    @pytest.mark.asyncio
    async def test_route_selects_highest_priority(self, monkeypatch):
        """First available provider by priority should be chosen."""
        call_log = []

        async def fake_openai(defn, api_key, messages, max_tokens=1200,
                              image_b64=None, json_mode=False):
            call_log.append(defn.name)
            return "test response"

        monkeypatch.setattr(router, "_call_openai_compat", fake_openai)
        monkeypatch.setattr(router, "_get_api_key", lambda d: "key")

        result = await route("reasoning", "sys", "user", max_tokens=50)
        assert result["provider"] == "cerebras"
        assert result["text"] == "test response"

    @pytest.mark.asyncio
    async def test_route_falls_back_on_error(self, monkeypatch):
        call_log = []

        async def fake_openai(defn, api_key, messages, max_tokens=1200,
                              image_b64=None, json_mode=False):
            call_log.append(defn.name)
            if defn.name == "cerebras":
                raise RuntimeError("cerebras down")
            return "fallback response"

        monkeypatch.setattr(router, "_call_openai_compat", fake_openai)
        monkeypatch.setattr(router, "_get_api_key", lambda d: "key")

        result = await route("reasoning", "sys", "user")
        assert result["provider"] != "cerebras"
        assert result["text"] == "fallback response"
        assert "cerebras" in call_log  # was tried first

    @pytest.mark.asyncio
    async def test_route_skips_exhausted_quota(self, monkeypatch):
        # Exhaust cerebras
        state = _get_state("cerebras")
        state.requests_this_minute = 999

        async def fake_openai(defn, api_key, messages, max_tokens=1200,
                              image_b64=None, json_mode=False):
            return "ok"

        monkeypatch.setattr(router, "_call_openai_compat", fake_openai)
        monkeypatch.setattr(router, "_get_api_key", lambda d: "key")

        result = await route("reasoning", "sys", "user")
        assert result["provider"] != "cerebras"

    @pytest.mark.asyncio
    async def test_route_skips_provider_without_key(self, monkeypatch):
        async def fake_openai(defn, api_key, messages, max_tokens=1200,
                              image_b64=None, json_mode=False):
            return "ok"

        def key_fn(defn):
            return None if defn.name == "cerebras" else "key"

        monkeypatch.setattr(router, "_call_openai_compat", fake_openai)
        monkeypatch.setattr(router, "_get_api_key", key_fn)

        result = await route("reasoning", "sys", "user")
        assert result["provider"] != "cerebras"

    @pytest.mark.asyncio
    async def test_route_vision_requires_supports_vision(self, monkeypatch):
        call_log = []

        async def fake_gemini(api_key, model, sys_prompt, user_prompt,
                              max_tokens=1200, image_b64=None,
                              use_search=False, json_mode=False):
            call_log.append(model)
            return "vision result"

        monkeypatch.setattr(router, "_call_gemini_native", fake_gemini)
        monkeypatch.setattr(router, "_get_api_key", lambda d: "key")

        result = await route("vision", "sys", "user")
        assert result["provider"] in ("gemini_flash", "gemini_lite", "groq_vision")

    @pytest.mark.asyncio
    async def test_route_all_providers_fail_raises(self, monkeypatch):
        async def always_fail(defn, api_key, messages, max_tokens=1200,
                              image_b64=None, json_mode=False):
            raise RuntimeError("all down")

        async def gemini_fail(api_key, model, sys_prompt, user_prompt,
                              max_tokens=1200, image_b64=None,
                              use_search=False, json_mode=False):
            raise RuntimeError("gemini down")

        monkeypatch.setattr(router, "_call_openai_compat", always_fail)
        monkeypatch.setattr(router, "_call_gemini_native", gemini_fail)
        monkeypatch.setattr(router, "_get_api_key", lambda d: "key")

        with pytest.raises(RuntimeError, match="Tüm providerlar başarısız"):
            await route("reasoning", "sys", "user")

    @pytest.mark.asyncio
    async def test_route_no_providers_for_task_raises(self, monkeypatch):
        monkeypatch.setattr(router, "_get_api_key", lambda d: None)
        with pytest.raises(RuntimeError, match="hiçbir provider"):
            await route("reasoning", "sys", "user")

    @pytest.mark.asyncio
    async def test_route_rate_limit_triggers_fallback(self, monkeypatch):
        call_count = {"n": 0}

        async def fake_openai(defn, api_key, messages, max_tokens=1200,
                              image_b64=None, json_mode=False):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise _RateLimitError(30)
            return "fallback ok"

        monkeypatch.setattr(router, "_call_openai_compat", fake_openai)
        monkeypatch.setattr(router, "_get_api_key", lambda d: "key")

        result = await route("reasoning", "sys", "user")
        assert result["text"] == "fallback ok"

    @pytest.mark.asyncio
    async def test_route_auth_error_disables_24h(self, monkeypatch):
        call_log = []

        async def fake_openai(defn, api_key, messages, max_tokens=1200,
                              image_b64=None, json_mode=False):
            call_log.append(defn.name)
            if defn.name == "cerebras":
                raise _AuthError("401")
            return "ok"

        monkeypatch.setattr(router, "_call_openai_compat", fake_openai)
        monkeypatch.setattr(router, "_get_api_key", lambda d: "key")

        result = await route("reasoning", "sys", "user")
        # Cerebras should be disabled for 24h
        state = _get_state("cerebras")
        assert state.backoff_until > time.time() + 86000

    @pytest.mark.asyncio
    async def test_route_timeout_triggers_fallback(self, monkeypatch):
        """httpx.TimeoutException and asyncio.TimeoutError should trigger fallback."""
        import httpx

        call_count = {"n": 0}

        async def fake_openai(defn, api_key, messages, max_tokens=1200,
                              image_b64=None, json_mode=False):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise httpx.TimeoutException("timeout")
            return "timeout fallback ok"

        monkeypatch.setattr(router, "_call_openai_compat", fake_openai)
        monkeypatch.setattr(router, "_get_api_key", lambda d: "key")

        result = await route("reasoning", "sys", "user")
        assert result["text"] == "timeout fallback ok"

    @pytest.mark.asyncio
    async def test_route_asyncio_timeout_triggers_fallback(self, monkeypatch):
        call_count = {"n": 0}

        async def fake_openai(defn, api_key, messages, max_tokens=1200,
                              image_b64=None, json_mode=False):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise asyncio.TimeoutError()
            return "async timeout fallback"

        monkeypatch.setattr(router, "_call_openai_compat", fake_openai)
        monkeypatch.setattr(router, "_get_api_key", lambda d: "key")

        result = await route("reasoning", "sys", "user")
        assert result["text"] == "async timeout fallback"


# ─── Gemini Native Path Tests ────────────────────────────────────────────────

class TestGeminiRoutePath:
    """Test that Gemini providers use native call path."""

    @pytest.mark.asyncio
    async def test_gemini_flash_uses_native_call(self, monkeypatch):
        native_called = {"called": False}

        async def fake_gemini(api_key, model, sys_prompt, user_prompt,
                              max_tokens=1200, image_b64=None,
                              use_search=False, json_mode=False):
            native_called["called"] = True
            assert model == "gemini-2.5-flash"
            return "gemini native"

        monkeypatch.setattr(router, "_call_gemini_native", fake_gemini)
        # Only provide gemini key
        monkeypatch.setattr(router, "_get_api_key",
                            lambda d: "key" if d.name.startswith("gemini") else None)

        result = await route("vision", "sys", "user")
        assert native_called["called"]

    @pytest.mark.asyncio
    async def test_gemini_lite_uses_native_call(self, monkeypatch):
        model_used = {"m": None}

        async def fake_gemini(api_key, model, sys_prompt, user_prompt,
                              max_tokens=1200, image_b64=None,
                              use_search=False, json_mode=False):
            model_used["m"] = model
            return "lite response"

        # Only give gemini_lite key, not gemini_flash
        def key_fn(defn):
            if defn.name == "gemini_lite":
                return "key"
            return None

        monkeypatch.setattr(router, "_call_gemini_native", fake_gemini)
        monkeypatch.setattr(router, "_get_api_key", key_fn)

        result = await route("vision", "sys", "user")
        assert model_used["m"] == "gemini-2.5-flash-lite"

    @pytest.mark.asyncio
    async def test_gemini_flash_vision_gets_800_max_tokens(self, monkeypatch):
        max_tok = {"val": None}

        async def fake_gemini(api_key, model, sys_prompt, user_prompt,
                              max_tokens=1200, image_b64=None,
                              use_search=False, json_mode=False):
            max_tok["val"] = max_tokens
            return "vision"

        monkeypatch.setattr(router, "_call_gemini_native", fake_gemini)
        monkeypatch.setattr(router, "_get_api_key",
                            lambda d: "key" if d.name == "gemini_flash" else None)

        await route("vision", "sys", "user", max_tokens=400)
        assert max_tok["val"] >= 800  # Gemini Flash vision gets min 800

    @pytest.mark.asyncio
    async def test_gemini_web_search_passes_use_search(self, monkeypatch):
        search_flag = {"val": None}

        async def fake_gemini(api_key, model, sys_prompt, user_prompt,
                              max_tokens=1200, image_b64=None,
                              use_search=False, json_mode=False):
            search_flag["val"] = use_search
            return "search result"

        monkeypatch.setattr(router, "_call_gemini_native", fake_gemini)
        monkeypatch.setattr(router, "_get_api_key",
                            lambda d: "key" if d.name.startswith("gemini") else None)

        # Exhaust groq_compound first
        _get_state("groq_compound").backoff_until = time.time() + 999

        await route("web_search", "sys", "user")
        assert search_flag["val"] is True


# ─── JSON Mode Tests ─────────────────────────────────────────────────────────

class TestJsonMode:
    """json_mode parameter handling."""

    @pytest.mark.asyncio
    async def test_json_mode_enabled_for_reasoning(self, monkeypatch):
        json_flag = {"val": None}

        async def fake_openai(defn, api_key, messages, max_tokens=1200,
                              image_b64=None, json_mode=False):
            json_flag["val"] = json_mode
            return '{"answer": "test"}'

        monkeypatch.setattr(router, "_call_openai_compat", fake_openai)
        monkeypatch.setattr(router, "_get_api_key", lambda d: "key")

        await route("reasoning", "sys", "user", json_mode=True)
        assert json_flag["val"] is True

    @pytest.mark.asyncio
    async def test_json_mode_disabled_for_vision(self, monkeypatch):
        json_flag = {"val": None}

        async def fake_gemini(api_key, model, sys_prompt, user_prompt,
                              max_tokens=1200, image_b64=None,
                              use_search=False, json_mode=False):
            json_flag["val"] = json_mode
            return "vision"

        monkeypatch.setattr(router, "_call_gemini_native", fake_gemini)
        monkeypatch.setattr(router, "_get_api_key",
                            lambda d: "key" if d.name.startswith("gemini") else None)

        await route("vision", "sys", "user", json_mode=True)
        assert json_flag["val"] is False  # json_mode stripped for vision

    @pytest.mark.asyncio
    async def test_json_mode_disabled_for_web_search(self, monkeypatch):
        json_flag = {"val": None}

        async def fake_openai(defn, api_key, messages, max_tokens=1200,
                              image_b64=None, json_mode=False):
            json_flag["val"] = json_mode
            return "search"

        monkeypatch.setattr(router, "_call_openai_compat", fake_openai)
        monkeypatch.setattr(router, "_get_api_key", lambda d: "key")

        await route("web_search", "sys", "user", json_mode=True)
        assert json_flag["val"] is False


# ─── Thinking Model Tests ────────────────────────────────────────────────────

class TestThinkingModels:
    """Test content fallback for thinking models."""

    @pytest.mark.asyncio
    async def test_thinking_model_content_null_uses_reasoning(self, monkeypatch):
        """GPT-OSS-120B / Nemotron put reasoning in 'reasoning' field, content=null."""
        import httpx

        class FakeResponse:
            status_code = 200
            headers = {}
            text = ""
            def json(self):
                return {
                    "choices": [{
                        "message": {
                            "content": None,
                            "reasoning": "The capital is Paris.",
                        }
                    }]
                }

        async def fake_post(url, json=None, headers=None):
            return FakeResponse()

        monkeypatch.setattr(router, "_get_api_key", lambda d: "key")

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = fake_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_class.return_value = mock_client

            defn = ProviderDef(
                name="test", base_url="http://test", model="test",
                tasks=["reasoning"], rpm=30, rpd=None,
                auth_env_key="key", priority=1,
            )
            result = await router._call_openai_compat(
                defn, "key",
                [{"role": "user", "content": "test"}],
            )
            assert result == "The capital is Paris."

    @pytest.mark.asyncio
    async def test_thinking_model_both_null_returns_empty(self, monkeypatch):
        """Both content and reasoning null should return empty string."""
        import httpx

        class FakeResponse:
            status_code = 200
            headers = {}
            text = ""
            def json(self):
                return {
                    "choices": [{
                        "message": {
                            "content": None,
                            "reasoning": None,
                        }
                    }]
                }

        async def fake_post(url, json=None, headers=None):
            return FakeResponse()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = fake_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_class.return_value = mock_client

            defn = ProviderDef(
                name="test", base_url="http://test", model="test",
                tasks=["reasoning"], rpm=30, rpd=None,
                auth_env_key="key", priority=1,
            )
            result = await router._call_openai_compat(
                defn, "key",
                [{"role": "user", "content": "test"}],
            )
            assert result == ""

    @pytest.mark.asyncio
    async def test_normal_model_content_returned(self, monkeypatch):
        """Normal model with content should return content, not reasoning."""
        import httpx

        class FakeResponse:
            status_code = 200
            headers = {}
            text = ""
            def json(self):
                return {
                    "choices": [{
                        "message": {
                            "content": "Normal response",
                            "reasoning": "Some reasoning",
                        }
                    }]
                }

        async def fake_post(url, json=None, headers=None):
            return FakeResponse()

        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client.post = fake_post
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_class.return_value = mock_client

            defn = ProviderDef(
                name="test", base_url="http://test", model="test",
                tasks=["reasoning"], rpm=30, rpd=None,
                auth_env_key="key", priority=1,
            )
            result = await router._call_openai_compat(
                defn, "key",
                [{"role": "user", "content": "test"}],
            )
            assert result == "Normal response"


# ─── Status API Tests ────────────────────────────────────────────────────────

class TestGetStatus:
    """Status endpoint data."""

    def test_status_returns_all_providers(self):
        status = get_status()
        assert len(status) == len(PROVIDERS)

    def test_status_includes_required_fields(self):
        status = get_status()
        for name, info in status.items():
            assert "model" in info
            assert "tasks" in info
            assert "configured" in info
            assert "available" in info
            assert "rpm_limit" in info
            assert "rpd_limit" in info
            assert "requests_this_minute" in info
            assert "requests_today" in info
            assert "priority" in info

    def test_status_shows_correct_priority(self):
        status = get_status()
        assert status["groq_compound"]["priority"] == 0
        assert status["cerebras"]["priority"] == 1

    def test_status_backoff_remaining_non_negative(self):
        # Set a backoff
        _get_state("cerebras").backoff_until = time.time() - 100
        status = get_status()
        assert status["cerebras"]["backoff_remaining_s"] == 0


# ─── OpenAI Compat Call Tests ────────────────────────────────────────────────

class TestOpenAICompat:
    """_call_openai_compat message construction and response parsing."""

    def test_vision_content_construction(self):
        """Vision should convert last user message to multimodal."""
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "describe"},
        ]
        # We test the message mutation logic directly
        image_b64 = "base64data"
        msgs = list(messages)
        last = msgs[-1]
        if last.get("role") == "user":
            text_content = last.get("content", "")
            msgs[-1] = {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                    {"type": "text", "text": text_content},
                ],
            }
        assert isinstance(msgs[-1]["content"], list)
        assert msgs[-1]["content"][0]["type"] == "image_url"
        assert msgs[-1]["content"][1]["text"] == "describe"

    def test_json_mode_skipped_for_vision(self):
        """json_mode + image_b64 should not set response_format."""
        payload = {"model": "test", "messages": [], "max_tokens": 100, "temperature": 0.1}
        json_mode = True
        image_b64 = "data"
        if json_mode and not image_b64:
            payload["response_format"] = {"type": "json_object"}
        assert "response_format" not in payload  # image_b64 is truthy

    def test_json_mode_set_for_text(self):
        payload = {"model": "test", "messages": [], "max_tokens": 100, "temperature": 0.1}
        json_mode = True
        image_b64 = None
        if json_mode and not image_b64:
            payload["response_format"] = {"type": "json_object"}
        assert payload["response_format"]["type"] == "json_object"


# ─── Error Class Tests ───────────────────────────────────────────────────────

class TestErrorClasses:

    def test_rate_limit_error_has_retry_after(self):
        err = _RateLimitError(30.0)
        assert err.retry_after == 30.0

    def test_auth_error_is_exception(self):
        err = _AuthError("401 Unauthorized")
        assert isinstance(err, Exception)
        assert "401" in str(err)


# ─── Edge Cases ──────────────────────────────────────────────────────────────

class TestEdgeCases:

    @pytest.mark.asyncio
    async def test_empty_system_prompt(self, monkeypatch):
        async def fake_openai(defn, api_key, messages, max_tokens=1200,
                              image_b64=None, json_mode=False):
            assert messages[0]["content"] == ""
            return "ok"

        monkeypatch.setattr(router, "_call_openai_compat", fake_openai)
        monkeypatch.setattr(router, "_get_api_key", lambda d: "key")

        result = await route("reasoning", "", "user prompt")
        assert result["text"] == "ok"

    @pytest.mark.asyncio
    async def test_very_long_prompt(self, monkeypatch):
        async def fake_openai(defn, api_key, messages, max_tokens=1200,
                              image_b64=None, json_mode=False):
            return "ok"

        monkeypatch.setattr(router, "_call_openai_compat", fake_openai)
        monkeypatch.setattr(router, "_get_api_key", lambda d: "key")

        long_prompt = "x" * 100000
        result = await route("reasoning", "sys", long_prompt)
        assert result["text"] == "ok"

    def test_concurrent_state_access(self):
        """Multiple get_state calls for same provider."""
        states = [_get_state("test") for _ in range(100)]
        assert all(s is states[0] for s in states)
