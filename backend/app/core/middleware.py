"""FastAPI middleware for request tracing and timing.

Provides :class:`RequestIDMiddleware` for attaching a unique request ID to
every request/response cycle, and :class:`TimingMiddleware` for measuring and
logging request duration.

Uses pure ASGI middleware instead of ``BaseHTTPMiddleware`` to avoid
deadlocks on Windows (``BaseHTTPMiddleware`` wraps responses through a
thread-pool executor that can stall under the ``ProactorEventLoop``).
"""

import time
import uuid

from starlette.types import ASGIApp, Receive, Scope, Send

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
