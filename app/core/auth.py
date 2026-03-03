"""
API key authentication middleware.

If API_KEY is set in env/.env, all state-changing requests (POST/PUT/DELETE/PATCH)
require a valid X-API-Key header. GET/HEAD/OPTIONS are exempt.
"""
from __future__ import annotations

import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger("trackerbundle.auth")

# HTTP methods that are exempt from API key checks
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class APIKeyMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, api_key: str | None = None):
        super().__init__(app)
        self._api_key = api_key

    async def dispatch(self, request: Request, call_next):
        # Skip auth if no key configured or method is safe
        if not self._api_key or request.method in _SAFE_METHODS:
            return await call_next(request)

        provided = request.headers.get("x-api-key", "")
        if provided != self._api_key:
            logger.warning("Unauthorized %s %s from %s", request.method, request.url.path, request.client.host if request.client else "unknown")
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing API key"},
            )
        return await call_next(request)
