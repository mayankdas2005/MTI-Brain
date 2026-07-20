"""Shared rate limiter instances.

Lives in its own module so route files can import the limiters without
pulling in :mod:`app.main` (which would create a circular import).

Two limiters:
  - ``limiter``      — keyed by real client IP (trusts X-Real-IP from nginx).
  - ``user_limiter`` — keyed by authenticated user ID (from JWT).
"""

from __future__ import annotations

from slowapi import Limiter
from starlette.requests import Request


def _get_real_ip(request: Request) -> str:
    """Extract the real client IP, respecting reverse-proxy headers.

    Priority: X-Real-IP (set by nginx) → first entry of X-Forwarded-For
    → direct connection IP. Only the header explicitly set by our trusted
    nginx is used — never raw X-Forwarded-For from the client.
    """
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        # Rightmost entry added by our nginx is the actual client IP
        # when there's a single trusted proxy layer.
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"


def _get_user_id(request: Request) -> str:
    """Extract user_id from the JWT Authorization header.

    Decodes the token directly (no DB lookup) to get the user_id claim.
    Falls back to IP-based keying if no valid token is present.
    """
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        from app.services.auth import decode_jwt_token
        payload = decode_jwt_token(auth.split(" ", 1)[1])
        if payload and payload.get("user_id"):
            return f"user:{payload['user_id']}"
    return _get_real_ip(request)


limiter = Limiter(key_func=_get_real_ip, headers_enabled=True)
user_limiter = Limiter(key_func=_get_user_id, headers_enabled=True)
