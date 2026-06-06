"""Transient-error retry utilities for all external service calls.

Wraps tenacity so all services get consistent behaviour: exponential backoff,
transient-only retries, and structured logs on each sleep and final give-up.

Usage — sync:
    result = retry_sync(lambda: do_something(), service="neo4j")

Usage — async:
    result = await retry_async(lambda: do_something_async(), service="redis")
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.core.logger import logger

T = TypeVar("T")

_TRANSIENT_PHRASES = (
    "timeout",
    "timed out",
    "connection reset",
    "connection refused",
    "broken pipe",
    "defunct",
    "socket",
    "eof occurred",
    "forcibly closed",
    "connectionreset",
    "connectionrefused",
    "serviceunavailable",
    "sessionexpired",
    "pool exhausted",
    "failed to read",
    "failed to write",
    "network",
    "ssl",
    "10054",
    "10061",
    "server closed the connection",
    "server terminated",
    "connection closed",
)


def is_transient(exc: BaseException) -> bool:
    """True if the exception looks like a transient connection / network error."""
    msg = str(exc).lower()
    return any(phrase in msg for phrase in _TRANSIENT_PHRASES)


def _before_sleep_cb(service: str, max_attempts: int):
    def _cb(retry_state) -> None:
        exc = retry_state.outcome.exception()
        sleep = getattr(retry_state.next_action, "sleep", 0)
        logger.warning(
            "{} | transient error attempt {}/{} — retrying in {:.1f}s | {}",
            service, retry_state.attempt_number, max_attempts, sleep, exc,
        )
    return _cb


def _after_cb(service: str, max_attempts: int):
    def _cb(retry_state) -> None:
        if (
            retry_state.outcome.failed
            and retry_state.attempt_number >= max_attempts
            and is_transient(retry_state.outcome.exception())
        ):
            logger.error(
                "{} | transient error attempt {}/{} — giving up | {}",
                service, retry_state.attempt_number, max_attempts, retry_state.outcome.exception(),
            )
    return _cb


def retry_sync(
    fn: Callable[[], T],
    *,
    max_attempts: int = 3,
    backoff_base: float = 0.5,
    service: str = "service",
) -> T:
    """Call fn() up to max_attempts times, retrying only on transient errors.

    Exponential backoff: attempt 1→backoff_base, attempt 2→backoff_base*2.
    Non-transient errors are re-raised immediately on the first occurrence.
    """
    from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_exponential

    for attempt in Retrying(
        retry=retry_if_exception(is_transient),
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=backoff_base, min=backoff_base, max=backoff_base * 4),
        before_sleep=_before_sleep_cb(service, max_attempts),
        after=_after_cb(service, max_attempts),
        reraise=True,
    ):
        with attempt:
            return fn()


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 3,
    backoff_base: float = 0.5,
    service: str = "service",
) -> T:
    """Async version of retry_sync."""
    from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential

    async for attempt in AsyncRetrying(
        retry=retry_if_exception(is_transient),
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=backoff_base, min=backoff_base, max=backoff_base * 4),
        before_sleep=_before_sleep_cb(service, max_attempts),
        after=_after_cb(service, max_attempts),
        reraise=True,
    ):
        with attempt:
            return await fn()
