"""Authentication service - JWT token management and user credential validation."""

import asyncio
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from app.core.config import settings
from app.core.logger import logger
from app.models.user import MTIBrainUser
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

# ─── Hardcoded users (dev only — replace with OIDC before production deploy) ───

_USERS: dict[str, dict] = {
    "admin": {
        "password": "$2b$12$cIf.CmlZ0pO2sAWQy4Yzr.TRNpeL/Tx9r8omOPdzbpgQiKKIsXGgq",
        "name": "Admin User",
        "email": "admin@milestone.tech",
    },
}


async def authenticate_user(username: str, password: str) -> dict | None:
    """Return the user dict if credentials are valid, else None.

    bcrypt.checkpw is CPU-bound and synchronous — offloaded to a thread
    pool so it does not block the async event loop.
    """
    key = (username or "").strip().lower()
    user = _USERS.get(key)
    if not user:
        return None
    loop = asyncio.get_event_loop()
    match = await loop.run_in_executor(
        None, bcrypt.checkpw, password.encode(), user["password"].encode()
    )
    return user if match else None


# ─── JWT management ───


def create_jwt_token(
    user_id: str,
    email: str,
    name: str,
    groups: list[str],
) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": email,
        "user_id": user_id,
        "email": email,
        "name": name,
        "groups": groups,
        "iat": now,
        "exp": now + timedelta(hours=settings.JWT_EXPIRY_HOURS),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_jwt_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        logger.debug("JWT expired")
        return None
    except jwt.InvalidTokenError as e:
        logger.debug(f"JWT invalid: {e}")
        return None


# ─── User upsert ───


async def upsert_user(
    db: AsyncSession,
    email: str,
    name: str,
    groups: list[str] | None = None,
) -> MTIBrainUser:
    """Create or update a user record on login (keyed by email)."""
    result = await db.execute(select(MTIBrainUser).where(MTIBrainUser.email == email))
    user = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)

    if user:
        await db.execute(
            update(MTIBrainUser)
            .where(MTIBrainUser.id == user.id)
            .values(name=name, groups=groups, last_login=now)
        )
        await db.flush()
        user.name = name
        user.groups = groups
        user.last_login = now
    else:
        user = MTIBrainUser(
            keycloak_sub=email,
            email=email,
            name=name,
            groups=groups,
            last_login=now,
            created_at=now,
        )
        db.add(user)
        await db.flush()
        logger.info(f"New user created: {email}")
        try:
            from app.services.chat.instructions import seed_default_instructions
            await seed_default_instructions(db, user.id)
        except Exception as e:
            logger.warning(f"Failed to seed default instructions for {email}: {e}")

    return user
