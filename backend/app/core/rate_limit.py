"""Shared rate limiter instance.

Lives in its own module so route files can import the limiter without
pulling in :mod:`app.main` (which would create a circular import). Keys
limits by client IP via :func:`slowapi.util.get_remote_address`.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
