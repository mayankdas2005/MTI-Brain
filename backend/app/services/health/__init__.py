"""Health check package for downstream service monitoring."""

from app.services.health.service import check_postgres

__all__ = ["check_postgres"]
