"""
Simple in-memory token bucket rate limiter for discover endpoints.

Prevents eBay/Amazon API quota exhaustion from bulk scan spam.
Each client IP gets a token bucket. Tokens refill over time.
"""
from __future__ import annotations

import time
import logging
from typing import Dict

from fastapi import Request, HTTPException

logger = logging.getLogger("trackerbundle.rate_limiter")


class TokenBucket:
    """In-memory per-IP token bucket."""

    def __init__(self, capacity: int = 10, refill_per_min: float = 3.0):
        self.capacity = capacity
        self.refill_per_min = refill_per_min
        self._buckets: Dict[str, dict] = {}  # ip -> {tokens, last_refill}

    def _get_bucket(self, ip: str) -> dict:
        now = time.time()
        if ip not in self._buckets:
            self._buckets[ip] = {"tokens": self.capacity, "last_refill": now}
        b = self._buckets[ip]
        # Refill tokens based on elapsed time
        elapsed_min = (now - b["last_refill"]) / 60.0
        new_tokens = elapsed_min * self.refill_per_min
        b["tokens"] = min(self.capacity, b["tokens"] + new_tokens)
        b["last_refill"] = now
        return b

    def consume(self, ip: str, cost: int = 1) -> bool:
        """Try to consume `cost` tokens. Returns True if allowed, False if rate limited."""
        b = self._get_bucket(ip)
        if b["tokens"] >= cost:
            b["tokens"] -= cost
            return True
        return False

    def remaining(self, ip: str) -> float:
        return self._get_bucket(ip)["tokens"]

    def cleanup(self, max_age_min: float = 30.0):
        """Remove stale entries to prevent memory growth."""
        now = time.time()
        cutoff = now - (max_age_min * 60)
        stale = [ip for ip, b in self._buckets.items() if b["last_refill"] < cutoff]
        for ip in stale:
            del self._buckets[ip]


# ── Global limiter instances ─────────────────────────────────────────────────

# Discover endpoints: 10 requests burst, 3/min refill
discover_limiter = TokenBucket(capacity=10, refill_per_min=3.0)

# Suggest-limit: more generous, 20 burst, 10/min
suggest_limiter = TokenBucket(capacity=20, refill_per_min=10.0)


import os as _os

# Comma-separated list of trusted reverse-proxy IPs (e.g. "127.0.0.1,10.0.0.1")
_TRUSTED_PROXIES: set[str] = set(
    p.strip() for p in _os.environ.get("TRUSTED_PROXIES", "127.0.0.1").split(",") if p.strip()
)


def get_client_ip(request: Request) -> str:
    """
    Extract client IP.  Only trusts X-Forwarded-For when the direct
    connection comes from a known reverse-proxy address.
    """
    direct_ip = request.client.host if request.client else "unknown"
    if direct_ip in _TRUSTED_PROXIES:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
    return direct_ip


def check_discover_rate(request: Request, cost: int = 1):
    """Call at the start of discover endpoints. Raises HTTP 429 if rate limited."""
    ip = get_client_ip(request)
    if not discover_limiter.consume(ip, cost):
        remaining = discover_limiter.remaining(ip)
        logger.warning("Rate limited discover: ip=%s remaining=%.1f", ip, remaining)
        raise HTTPException(
            status_code=429,
            detail=f"Rate limit aşıldı. Lütfen {int(60 / discover_limiter.refill_per_min)}s bekleyin. Kalan: {remaining:.1f} token"
        )


def check_suggest_rate(request: Request, cost: int = 1):
    """Call at the start of suggest-limit endpoints. Raises HTTP 429 if rate limited."""
    ip = get_client_ip(request)
    if not suggest_limiter.consume(ip, cost):
        raise HTTPException(
            status_code=429,
            detail="Rate limit aşıldı. Lütfen birkaç saniye bekleyin."
        )
