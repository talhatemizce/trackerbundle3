"""
TrackerBundle3 — Multi-LLM Router
==================================
Görev tipine göre en iyi ücretsiz LLM provider'ı seç, kota dolunca geç.

GÖREV → MODEL ATAMALARI:
  vision      → Gemini 2.5 Flash-Lite  (tek vision destekleyen)
  web_search  → Gemini 2.5 Flash-Lite  (Google Search grounding)
               → Perplexity Sonar      (web araması en iyi, $5 kredi ~5k istek)
  reasoning   → OpenRouter Nemotron-3 Super 120B:free (∞ kota, 20 RPM)
               → OpenRouter Hermes-3 405B:free       (∞ kota, 20 RPM)
               → Groq Kimi K2               (14.400/gün, 30 RPM)
               → Groq Llama3.3-70B           (14.400/gün, 30 RPM)
               → Cerebras Llama3.3-70B       (∞, 30 RPM)
               → Gemini fallback

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


PROVIDERS: List[ProviderDef] = [
    # ══════════════════════════════════════════════════════════════════════════
    # Öncelik stratejisi: ZEKA-FIRST
    #
    # Reasoning:  OpenRouter Nemotron 120B → Hermes 405B → Groq Kimi K2 (8/10)
    #             → Groq 70B (7/10) → Cerebras (7/10 ∞) → Gemini (7/10)
    # Vision:     Groq Llama 4 Scout (8/10 hızlı) → Gemini (Google grounding)
    # Web search: Gemini (Google Search grounding) → OpenRouter Hermes 405B
    #
    # Para: $0.  Tüm providerlar ücretsiz tier.
    # ══════════════════════════════════════════════════════════════════════════

    # ── 1. REASONING: En akıllıdan başla ────────────────────────────────────

    # OpenRouter Nemotron-3 Super 120B MoE — güçlü ücretsiz (∞ kota, 262k ctx)
    ProviderDef(
        name="openrouter_nemotron",
        base_url="https://openrouter.ai/api/v1",
        model="nvidia/nemotron-3-super-120b-a12b:free",
        tasks=["reasoning"],
        rpm=20, rpd=None,
        auth_env_key="openrouter_api_key",
        priority=1,
        extra_headers={"HTTP-Referer": "https://trackerbundle3.app", "X-Title": "TrackerBundle3"},
    ),
    # OpenRouter Hermes-3 Llama 405B — en büyük ücretsiz model (∞ kota, 131k ctx)
    ProviderDef(
        name="openrouter_hermes",
        base_url="https://openrouter.ai/api/v1",
        model="nousresearch/hermes-3-llama-3.1-405b:free",
        tasks=["reasoning", "web_search"],
        rpm=20, rpd=None,
        auth_env_key="openrouter_api_key",
        priority=2,
        extra_headers={"HTTP-Referer": "https://trackerbundle3.app", "X-Title": "TrackerBundle3"},
    ),
    # Groq Kimi K2 — hızlı + güçlü (8/10, 14.4k/gün paylaşımlı kota)
    ProviderDef(
        name="groq_kimi",
        base_url="https://api.groq.com/openai/v1",
        model="moonshotai/kimi-k2-instruct",
        tasks=["reasoning"],
        rpm=30, rpd=14400,
        auth_env_key="groq_api_key",
        priority=3,
    ),
    # Groq Llama 3.3 70B — hızlı fallback (7/10, 14.4k/gün paylaşımlı kota)
    ProviderDef(
        name="groq",
        base_url="https://api.groq.com/openai/v1",
        model="llama-3.3-70b-versatile",
        tasks=["reasoning"],
        rpm=30, rpd=14400,
        auth_env_key="groq_api_key",
        priority=4,
    ),
    # Cerebras Llama 3.3 70B — sınırsız kota, güvenilir yedek (7/10, ∞)
    ProviderDef(
        name="cerebras",
        base_url="https://api.cerebras.ai/v1",
        model="llama-3.3-70b",
        tasks=["reasoning"],
        rpm=30, rpd=None,
        auth_env_key="cerebras_api_key",
        priority=5,
    ),

    # ── 2. VISION: Görsel doğrulama ─────────────────────────────────────────

    # Groq Llama 4 Scout — hızlı vision (8/10, multimodal, 14.4k/gün)
    ProviderDef(
        name="groq_vision",
        base_url="https://api.groq.com/openai/v1",
        model="meta-llama/llama-4-scout-17b-16e-instruct",
        tasks=["vision"],
        rpm=30, rpd=14400,
        auth_env_key="groq_api_key",
        priority=1,
        supports_vision=True,
    ),

    # ── 3. GEMINI: Son çare + Google Search grounding ───────────────────────

    # Gemini Flash-Lite — vision fallback + web_search (Google grounding), kota 1.5k/gün
    ProviderDef(
        name="gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        model="gemini-2.5-flash-lite",
        tasks=["vision", "web_search", "reasoning"],
        rpm=15, rpd=1500,
        auth_env_key="gemini_api_key",
        priority=9,
        supports_vision=True,
    ),
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
    image_b64 varsa son user mesajına vision content ekler (Llama 4 Scout destekler).
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
    async with httpx.AsyncClient(timeout=60) as client:
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
    # Thinking models (e.g. Nemotron) may put chain-of-thought in "reasoning"
    # and leave "content" null when max_tokens is exhausted during reasoning.
    return msg.get("content") or msg.get("reasoning") or ""


class _RateLimitError(Exception):
    def __init__(self, retry_after: float):
        self.retry_after = retry_after

class _AuthError(Exception):
    pass


# ─── Gemini native çağrı (vision + Google Search destekli) ───────────────────

async def _call_gemini_native(
    api_key: str,
    system_prompt: str,
    user_prompt: str,
    image_b64: Optional[str] = None,
    use_search: bool = False,
) -> str:
    """Gemini native API — vision ve Google Search grounding destekli."""
    GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
    MODEL = "gemini-2.5-flash-lite"
    url = f"{GEMINI_BASE}/{MODEL}:generateContent?key={api_key}"

    parts: List[Dict] = []
    if image_b64:
        parts.append({"inline_data": {"mime_type": "image/jpeg", "data": image_b64}})
    parts.append({"text": user_prompt})

    payload: Dict[str, Any] = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 1200},
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
            raise RuntimeError(f"Gemini {r.status_code}: {r.text[:200]}")
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

            if defn.name == "gemini":
                text = await _call_gemini_native(
                    api_key, system_prompt, user_prompt,
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
