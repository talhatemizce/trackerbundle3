"""
TrackerBundle3 — Multi-LLM Router v3
=====================================
Görev tipine göre en iyi ücretsiz LLM provider'ı seç, kota dolunca geç.

GÖREV → MODEL ATAMALARI (Mart 2026 — canlı API doğrulamalı):
  reasoning   → Cerebras Qwen3-235B           (∞ kota, 30 RPM, ~2000 t/s)
               → Groq Kimi K2 0905             (14.400/gün, 30 RPM, 262K ctx)
               → Groq GPT-OSS-120B             (14.400/gün, 30 RPM, thinking)
               → Groq Llama3.3-70B             (14.400/gün, 30 RPM)
               → OR StepFun Step-3.5-Flash     (∞, 20 RPM, en güvenilir OR)
               → OR Nemotron-3 Super 120B      (∞, 20 RPM, thinking model)
               → Gemini Flash-Lite fallback

  vision      → Gemini 2.5 Flash              (10 RPM, 500 RPD, %97 doğruluk)
               → Gemini Flash-Lite             (15 RPM, 1500 RPD, hızlı)
               → Groq Llama 4 Scout            (30 RPM, ⚠️ anti-contamination)

  web_search  → Groq Compound                 (30 RPM, Groq hızı)
               → Gemini 2.5 Flash              (Google Search grounding)
               → Gemini Flash-Lite fallback

/llm/status endpoint → kota durumu, devre dışı providerlar
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("trackerbundle.llm_router")


# ─── Provider tanımları ───────────────────────────────────────────────────────

@dataclass
class ProviderDef:
    name: str
    base_url: str
    model: str
    tasks: List[str]          # desteklediği task tipleri
    rpm: int                  # rate limit (per minute)
    rpd: Optional[int]        # daily limit (None = unlimited)
    auth_env_key: str         # env değişkeni adı (config'de)
    priority: int             # düşük = yüksek öncelik
    supports_vision: bool = False
    extra_headers: Dict[str, str] = field(default_factory=dict)
    timeout_s: int = 60       # provider-specific timeout (OpenRouter: 12s)


# ── OpenRouter ortak headerlar ─────────────────────────────────────────────
_OR_HEADERS = {"HTTP-Referer": "https://trackerbundle3.app", "X-Title": "TrackerBundle3"}

PROVIDERS: List[ProviderDef] = [
    # ══════════════════════════════════════════════════════════════════════════
    # Strateji: GÜVENİLİRLİK × ZEKA (Mart 2026, canlı test sonuçları)
    #
    # Reasoning:  Cerebras 235B (∞, hızlı, en akıllı) → Groq K2 0905 (262K)
    #             → Groq GPT-OSS 120B → Groq 70B → OR StepFun → OR Nemotron
    #             → Gemini Lite fallback
    # Vision:     Gemini 2.5 Flash (%97) → Gemini Lite (1500 RPD)
    #             → Groq Scout (⚠️ training contamination)
    # Web search: Groq Compound (30 RPM) → Gemini Flash (Google grounding)
    #
    # Önemli kararlar:
    #   • Cerebras P1: Qwen3-235B ONLY model left (llama-3.3-70b removed!)
    #   • OpenRouter DEMOTED: 30-120s kuyruk + deprioritize @ peak
    #   • OpenRouter timeout: 12s (60s yerine) — kuyruk tuzağını önle
    #   • Gemini 2.5 Flash: thinking model, vision=800 max_tokens
    #   • Groq Scout: anti-contamination prompt gerekli (listing_verifier)
    #   • GPT-OSS-120B & Nemotron: thinking models, reasoning token overhead
    #
    # Para: $0.  Tüm providerlar ücretsiz tier.
    # ══════════════════════════════════════════════════════════════════════════

    # ── 1. REASONING ──────────────────────────────────────────────────────

    # Cerebras Qwen3-235B — EN AKILLI + EN GÜVENİLİR (235B MoE, ∞ kota)
    # NOT: Cerebras'tan llama-3.3-70b kaldırıldı (404). Sadece bu + 8B kaldı.
    # Thinking model DEĞİL: reasoning_tokens=0, doğrudan content döner.
    ProviderDef(
        name="cerebras",
        base_url="https://api.cerebras.ai/v1",
        model="qwen-3-235b-a22b-instruct-2507",
        tasks=["reasoning"],
        rpm=30, rpd=None,
        auth_env_key="cerebras_api_key",
        priority=1,
    ),
    # Groq Kimi K2 0905 — hızlı + güçlü (8/10, 262K ctx, yeni versiyon)
    ProviderDef(
        name="groq_kimi",
        base_url="https://api.groq.com/openai/v1",
        model="moonshotai/kimi-k2-instruct-0905",
        tasks=["reasoning"],
        rpm=30, rpd=14400,
        auth_env_key="groq_api_key",
        priority=2,
    ),
    # Groq GPT-OSS-120B — 120B on Groq hardware (thinking model: reasoning tokens)
    ProviderDef(
        name="groq_gpt_oss",
        base_url="https://api.groq.com/openai/v1",
        model="openai/gpt-oss-120b",
        tasks=["reasoning"],
        rpm=30, rpd=14400,
        auth_env_key="groq_api_key",
        priority=3,
    ),
    # Groq Llama 3.3 70B — battle-tested hızlı fallback (7/10)
    ProviderDef(
        name="groq",
        base_url="https://api.groq.com/openai/v1",
        model="llama-3.3-70b-versatile",
        tasks=["reasoning"],
        rpm=30, rpd=14400,
        auth_env_key="groq_api_key",
        priority=4,
    ),
    # OpenRouter StepFun Step-3.5-Flash — en güvenilir OR :free model (256K ctx)
    # 12s timeout: OR kuyruk gecikmeleri 30-120s olabilir, tuzağa düşme
    ProviderDef(
        name="openrouter_stepfun",
        base_url="https://openrouter.ai/api/v1",
        model="stepfun/step-3.5-flash:free",
        tasks=["reasoning"],
        rpm=20, rpd=None,
        auth_env_key="openrouter_api_key",
        priority=5,
        extra_headers=_OR_HEADERS,
        timeout_s=12,
    ),
    # OpenRouter Nemotron-3 Super 120B — thinking model, token overhead
    ProviderDef(
        name="openrouter_nemotron",
        base_url="https://openrouter.ai/api/v1",
        model="nvidia/nemotron-3-super-120b-a12b:free",
        tasks=["reasoning"],
        rpm=20, rpd=None,
        auth_env_key="openrouter_api_key",
        priority=6,
        extra_headers=_OR_HEADERS,
        timeout_s=12,
    ),

    # ── 2. VISION ─────────────────────────────────────────────────────────

    # Gemini 2.5 Flash — EN İYİ vision (%97 title doğruluk, 0.75 mAP)
    # Thinking model: vision için max_tokens=800 kullanılmalı
    # 10 RPM, 500 RPD — kitap doğrulama için yeterli
    ProviderDef(
        name="gemini_flash",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        model="gemini-2.5-flash",
        tasks=["vision", "web_search"],
        rpm=10, rpd=500,
        auth_env_key="gemini_api_key",
        priority=1,
        supports_vision=True,
    ),
    # Gemini Flash-Lite — yüksek hacim fallback (hızlı, düz çıktı)
    # ⚠️ sources_checked field'ı fabrike edebilir — strip edilmeli
    ProviderDef(
        name="gemini_lite",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        model="gemini-2.5-flash-lite",
        tasks=["vision", "web_search", "reasoning"],
        rpm=15, rpd=1500,
        auth_env_key="gemini_api_key",
        priority=2,
        supports_vision=True,
    ),
    # Groq Llama 4 Scout — hızlı ama ⚠️ training contamination
    # Kitap kapağını eğitim verisinden tanımlayabilir (gerçek görselden değil)
    # listing_verifier.py'de anti-contamination prompt eklenmeli
    ProviderDef(
        name="groq_vision",
        base_url="https://api.groq.com/openai/v1",
        model="meta-llama/llama-4-scout-17b-16e-instruct",
        tasks=["vision"],
        rpm=30, rpd=14400,
        auth_env_key="groq_api_key",
        priority=3,
        supports_vision=True,
    ),

    # ── 3. WEB SEARCH ─────────────────────────────────────────────────────

    # Groq Compound — web search özellikli model (Groq hızı, 30 RPM)
    # ~25% Perplexity Sonar'dan daha doğru (Groq iddiası)
    # P0 çünkü web_search'te Gemini Flash'tan (P1) önce gelmeli
    ProviderDef(
        name="groq_compound",
        base_url="https://api.groq.com/openai/v1",
        model="groq/compound",
        tasks=["web_search"],
        rpm=30, rpd=14400,
        auth_env_key="groq_api_key",
        priority=0,
    ),
    # Gemini Flash + Lite zaten web_search task'ını da destekliyor (yukarıda tanımlı)
]


# ─── Quota tracker (in-memory) ────────────────────────────────────────────────

@dataclass
class ProviderState:
    requests_this_minute: int = 0
    requests_today: int = 0
    minute_window_start: float = field(default_factory=time.time)
    day_window_start: float = field(default_factory=time.time)
    consecutive_errors: int = 0
    backoff_until: float = 0.0
    last_used: float = 0.0

    def reset_if_needed(self):
        now = time.time()
        if now - self.minute_window_start >= 60:
            self.requests_this_minute = 0
            self.minute_window_start = now
        if now - self.day_window_start >= 86400:
            self.requests_today = 0
            self.day_window_start = now

    def is_available(self, defn: ProviderDef) -> bool:
        self.reset_if_needed()
        if time.time() < self.backoff_until:
            return False
        if self.requests_this_minute >= defn.rpm:
            return False
        if defn.rpd is not None and self.requests_today >= defn.rpd:
            return False
        return True

    def record_request(self):
        self.requests_this_minute += 1
        self.requests_today += 1
        self.last_used = time.time()

    def record_success(self):
        self.consecutive_errors = 0

    def record_error(self, retry_after: float = 0):
        self.consecutive_errors += 1
        if retry_after > 0:
            self.backoff_until = time.time() + retry_after
        elif self.consecutive_errors >= 2:
            self.backoff_until = time.time() + min(30 * self.consecutive_errors, 300)


_states: Dict[str, ProviderState] = {}
_state_lock = asyncio.Lock()


def _get_state(name: str) -> ProviderState:
    """Lazy state init — provider eklense bile çalışır."""
    if name not in _states:
        _states[name] = ProviderState()
    return _states[name]


# ─── API key erişimi ──────────────────────────────────────────────────────────

def _get_api_key(defn: ProviderDef) -> Optional[str]:
    try:
        from app.core.config import get_settings
        s = get_settings()
        val = getattr(s, defn.auth_env_key, None)
        return val if val else None
    except Exception:
        return None


# ─── OpenAI-uyumlu tek çağrı ─────────────────────────────────────────────────

async def _call_openai_compat(
    defn: ProviderDef,
    api_key: str,
    messages: List[Dict[str, Any]],
    max_tokens: int = 1200,
    temperature: float = 0.1,
    image_b64: Optional[str] = None,
) -> str:
    """OpenAI-uyumlu /chat/completions endpoint'i çağır, ham text döndür.
    image_b64 varsa son user mesajına vision content ekler.
    Provider-specific timeout kullanır (OpenRouter: 12s, diğerleri: 60s).
    """
    # Vision: son user mesajını multimodal content'e çevir
    if image_b64 and messages:
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
        messages = msgs

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        **defn.extra_headers,
    }
    payload = {
        "model": defn.model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    timeout = defn.timeout_s
    async with httpx.AsyncClient(timeout=timeout) as client:
        r = await client.post(
            f"{defn.base_url}/chat/completions",
            json=payload,
            headers=headers,
        )
        if r.status_code == 429:
            retry_after = float(r.headers.get("Retry-After", 30))
            raise _RateLimitError(retry_after)
        if r.status_code in (401, 403):
            raise _AuthError(f"{defn.name}: {r.status_code}")
        if r.status_code != 200:
            raise RuntimeError(f"{defn.name} HTTP {r.status_code}: {r.text[:200]}")
    data = r.json()
    msg = data["choices"][0]["message"]
    # Thinking models (GPT-OSS-120B, Nemotron, Gemini Flash) may put
    # chain-of-thought in "reasoning" and leave "content" null.
    return msg.get("content") or msg.get("reasoning") or ""


class _RateLimitError(Exception):
    def __init__(self, retry_after: float):
        self.retry_after = retry_after

class _AuthError(Exception):
    pass


# ─── Gemini native çağrı (vision + Google Search destekli) ───────────────────

async def _call_gemini_native(
    api_key: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int = 1200,
    image_b64: Optional[str] = None,
    use_search: bool = False,
) -> str:
    """Gemini native API — vision ve Google Search grounding destekli.
    Model dinamik: gemini-2.5-flash (vision-primary) veya gemini-2.5-flash-lite (fallback).
    """
    GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
    url = f"{GEMINI_BASE}/{model}:generateContent?key={api_key}"

    parts: List[Dict] = []
    if image_b64:
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": image_b64}})
    parts.append({"text": user_prompt})

    payload: Dict[str, Any] = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": max_tokens},
        "system_instruction": {"parts": [{"text": system_prompt}]},
    }
    if use_search:
        payload["tools"] = [{"google_search": {}}]

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(url, json=payload, headers={"Content-Type": "application/json"})
        if r.status_code == 429:
            retry_after = float(r.headers.get("Retry-After", 60))
            raise _RateLimitError(retry_after)
        if r.status_code != 200:
            raise RuntimeError(f"Gemini({model}) {r.status_code}: {r.text[:200]}")
    # Extract text from Gemini response format
    data = r.json()
    try:
        parts_out = data["candidates"][0]["content"]["parts"]
        return "\n".join(p.get("text", "") for p in parts_out if "text" in p)
    except (KeyError, IndexError):
        return str(data)


# ─── Ana router fonksiyonu ────────────────────────────────────────────────────

async def route(
    task: str,                        # "vision" | "web_search" | "reasoning"
    system_prompt: str,
    user_prompt: str,
    image_b64: Optional[str] = None,  # sadece vision task'ı için
    max_tokens: int = 1200,
) -> Dict[str, Any]:
    """
    En uygun provider'a isteği gönder, başarısız olursa sıradakine geç.
    Döner: {"text": str, "provider": str, "model": str}
    """
    candidates = [
        p for p in sorted(PROVIDERS, key=lambda x: x.priority)
        if task in p.tasks
        and (task != "vision" or p.supports_vision)
        and _get_api_key(p) is not None
    ]

    if not candidates:
        raise RuntimeError(f"task={task} için hiçbir provider yapılandırılmamış")

    last_error: Optional[Exception] = None

    for defn in candidates:
        state = _get_state(defn.name)
        if not state.is_available(defn):
            logger.info("router: %s skip (kota/backoff)", defn.name)
            continue

        api_key = _get_api_key(defn)
        if not api_key:
            continue

        try:
            state.record_request()
            logger.info("router: %s (%s) — task=%s", defn.name, defn.model, task)

            if defn.name.startswith("gemini"):
                # Gemini 2.5 Flash = thinking model → vision için daha fazla token
                _max = max(max_tokens, 800) if (task == "vision" and defn.model == "gemini-2.5-flash") else max_tokens
                text = await _call_gemini_native(
                    api_key, defn.model, system_prompt, user_prompt,
                    max_tokens=_max,
                    image_b64=image_b64,
                    use_search=(task == "web_search"),
                )
            else:
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_prompt},
                ]
                # Vision-capable non-Gemini providers (Llama 4 Scout on Groq)
                img = image_b64 if (task == "vision" and defn.supports_vision) else None
                text = await _call_openai_compat(defn, api_key, messages, max_tokens=max_tokens, image_b64=img)

            state.record_success()
            return {"text": text, "provider": defn.name, "model": defn.model}

        except _RateLimitError as e:
            state.record_error(retry_after=e.retry_after)
            last_error = e
            logger.warning("router: %s 429 — retry_after=%.0fs, next provider", defn.name, e.retry_after)

        except _AuthError as e:
            state.backoff_until = time.time() + 86400  # auth hatası → 24 saat devre dışı
            last_error = e
            logger.error("router: %s auth error — devre dışı: %s", defn.name, e)

        except (httpx.TimeoutException, asyncio.TimeoutError) as e:
            state.record_error()
            last_error = e
            logger.warning("router: %s timeout (%ds) — %s, next provider", defn.name, defn.timeout_s, e)

        except Exception as e:
            state.record_error()
            last_error = e
            logger.warning("router: %s error — %s, next provider", defn.name, e)

    raise RuntimeError(f"Tüm providerlar başarısız — task={task}: {last_error}")


# ─── Status API ───────────────────────────────────────────────────────────────

def get_status() -> Dict[str, Any]:
    """Tüm providerların kota durumunu döndür (/llm/status endpoint için)."""
    result = {}
    for defn in PROVIDERS:
        state = _get_state(defn.name)
        state.reset_if_needed()
        api_key = _get_api_key(defn)
        result[defn.name] = {
            "model": defn.model,
            "tasks": defn.tasks,
            "configured": bool(api_key),
            "available": state.is_available(defn) and bool(api_key),
            "rpm_limit": defn.rpm,
            "rpd_limit": defn.rpd,
            "requests_this_minute": state.requests_this_minute,
            "requests_today": state.requests_today,
            "backoff_until": state.backoff_until,
            "backoff_remaining_s": max(0, round(state.backoff_until - time.time())),
            "consecutive_errors": state.consecutive_errors,
            "priority": defn.priority,
        }
    return result
