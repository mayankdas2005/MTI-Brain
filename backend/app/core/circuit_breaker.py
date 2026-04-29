"""Circuit breaker instances for external service calls.

Uses ``pybreaker`` to protect against cascading failures when calling
Postgres, the LLM provider, the embedding service, or other external APIs.
Each breaker is pre-configured with appropriate thresholds and a
:class:`LoggingListener` that logs state transitions.
"""

import pybreaker
from app.core.logger import logger
from app.core.config import settings


class LoggingListener(pybreaker.CircuitBreakerListener):
    """Log circuit breaker state transitions, failures, and successes.

    Attach an instance of this listener to any :class:`pybreaker.CircuitBreaker`
    to get structured log output whenever the breaker changes state or records
    a call outcome.
    """

    def state_change(self, cb, old_state, new_state):
        """Log a warning when the circuit breaker transitions between states.

        Args:
            cb: The :class:`pybreaker.CircuitBreaker` instance.
            old_state: The state the breaker is leaving.
            new_state: The state the breaker is entering.
        """
        logger.warning(
            f"Circuit breaker '{cb.name}': {old_state.name} → {new_state.name}"
        )

    def failure(self, cb, exc):
        """Log an error when a protected call fails.

        Args:
            cb: The :class:`pybreaker.CircuitBreaker` instance.
            exc: The exception raised by the failed call.
        """
        logger.error(f"Circuit breaker '{cb.name}' recorded failure: {exc}")

    def success(self, cb):
        """Log a debug message when a protected call succeeds.

        Args:
            cb: The :class:`pybreaker.CircuitBreaker` instance.
        """
        logger.debug(f"Circuit breaker '{cb.name}' call succeeded")


postgres_breaker = pybreaker.CircuitBreaker(
    fail_max=settings.CB_FAIL_MAX,
    reset_timeout=settings.CB_RESET_TIMEOUT,
    name="postgres",
    listeners=[LoggingListener()],
)

llm_breaker = pybreaker.CircuitBreaker(
    fail_max=3,
    reset_timeout=60,
    name="llm",
    listeners=[LoggingListener()],
)

embedding_breaker = pybreaker.CircuitBreaker(
    fail_max=3,
    reset_timeout=60,
    name="embedding",
    listeners=[LoggingListener()],
)

external_api_breaker = pybreaker.CircuitBreaker(
    fail_max=3,
    reset_timeout=60,
    name="external_api",
    listeners=[LoggingListener()],
)
