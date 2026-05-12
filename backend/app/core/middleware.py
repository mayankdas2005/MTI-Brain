"""FastAPI middleware for request tracing, timing, and security headers.

Provides :class:`RequestIDMiddleware` for attaching a unique request ID to
every request/response cycle, :class:`TimingMiddleware` for measuring and
logging request duration, and :class:`SecurityHeadersMiddleware` for adding
defensive HTTP response headers.

Uses pure ASGI middleware instead of ``BaseHTTPMiddleware`` to avoid
deadlocks on Windows (``BaseHTTPMiddleware`` wraps responses through a
thread-pool executor that can stall under the ``ProactorEventLoop``).
"""

import time
import uuid

from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import settings
from app.core.logger import logger


class RequestIDMiddleware:
    """Attach a unique request ID to every request/response for tracing.

    If the incoming request includes an ``X-Request-ID`` header its value is
    reused; otherwise a new UUID4 is generated.  The ID is stored on
    ``scope["state"]["request_id"]`` and echoed back in the response header.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        # Extract or generate request ID
        headers = dict(scope.get("headers", []))
        request_id = headers.get(b"x-request-id", b"").decode() or str(uuid.uuid4())

        # Store on scope state
        scope.setdefault("state", {})
        scope["state"]["request_id"] = request_id

        async def send_with_request_id(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_request_id)


class TimingMiddleware:
    """Log request duration and attach an ``X-Response-Time`` header.

    Measures wall-clock time from when the request is received to when the
    response is ready.  The duration is added as an ``X-Response-Time`` header
    and, for non-health-check endpoints, logged with the request ID.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        path = scope.get("path", "")
        method = scope.get("method", "")
        status_code = 0

        async def send_with_timing(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status", 0)
                duration_ms = (time.perf_counter() - start) * 1000
                headers = list(message.get("headers", []))
                headers.append((b"x-response-time", f"{duration_ms:.2f}ms".encode()))
                message = {**message, "headers": headers}

                if path != "/health":
                    request_id = scope.get("state", {}).get("request_id", "N/A")
                    logger.info(
                        f"{method} {path} → {status_code} ({duration_ms:.1f}ms) [rid={request_id}]"
                    )
            await send(message)

        await self.app(scope, receive, send_with_timing)


# Paths whose responses are HTML/JS (Swagger, ReDoc, OpenAPI schema host).
# A strict CSP would break the docs UI, so we exempt them.
_DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")

# Strict CSP for JSON API responses: nothing should be loadable from a
# response body that isn't a document. ``frame-ancestors 'none'`` mirrors
# ``X-Frame-Options: DENY`` for browsers that prefer CSP.
_API_CSP = b"default-src 'none'; frame-ancestors 'none'; base-uri 'none'"


class SecurityHeadersMiddleware:
    """Attach defensive HTTP response headers to every response.

    Headers set:
      * ``X-Content-Type-Options: nosniff`` — disables MIME sniffing.
      * ``X-Frame-Options: DENY`` — blocks framing (clickjacking defense).
      * ``Referrer-Policy: strict-origin-when-cross-origin`` — limits
        leakage of full URLs in the ``Referer`` header.
      * ``Content-Security-Policy`` — strict ``default-src 'none'`` for
        API responses. Skipped for the FastAPI docs paths so Swagger /
        ReDoc still load.
      * ``Strict-Transport-Security`` — production only, since dev runs
        over plain HTTP and HSTS would pin a broken policy in browsers.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        is_docs = any(path.startswith(p) for p in _DOCS_PATHS)

        async def send_with_security(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"x-content-type-options", b"nosniff"))
                headers.append((b"x-frame-options", b"DENY"))
                headers.append(
                    (b"referrer-policy", b"strict-origin-when-cross-origin")
                )
                headers.append((b"x-permitted-cross-domain-policies", b"none"))
                headers.append(
                    (b"permissions-policy", b"geolocation=(), microphone=(), camera=()")
                )
                if not is_docs:
                    headers.append((b"cache-control", b"no-store"))
                if not is_docs:
                    headers.append((b"content-security-policy", _API_CSP))
                if settings.is_production:
                    headers.append(
                        (
                            b"strict-transport-security",
                            b"max-age=31536000; includeSubDomains",
                        )
                    )
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_security)
