"""Authentication service - JWT token management and user credential validation."""

from datetime import datetime, timedelta, timezone

import jwt
from app.core.config import settings
from app.core.logger import logger
from app.models.user import QuestUser
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

# ─── Hardcoded users ───

_USERS: dict[str, dict] = {
    "admin": {
        "password": "admin123",
        "name": "Admin User",
        "email": "admin@milestone.tech",
    },
}


def authenticate_user(username: str, password: str) -> dict | None:
    """Return the user dict if credentials are valid, else None."""
    key = (username or "").strip().lower()
    user = _USERS.get(key)
    logger.debug(f"authenticate_user: key={key!r} found={user is not None} pw_match={user['password'] == password if user else False}")
    if user and user["password"] == password:
        return user
    return None


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
        "exp": now + timedelta(hours=8),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")


def decode_jwt_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
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
) -> QuestUser:
    """Create or update a user record on login (keyed by email)."""
    result = await db.execute(select(QuestUser).where(QuestUser.email == email))
    user = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)

    if user:
        await db.execute(
            update(QuestUser)
            .where(QuestUser.id == user.id)
            .values(name=name, groups=groups, last_login=now)
        )
        await db.flush()
        user.name = name
        user.groups = groups
        user.last_login = now
    else:
        user = QuestUser(
            okta_id=email,
            email=email,
            name=name,
            groups=groups,
            last_login=now,
            created_at=now,
        )
        db.add(user)
        await db.flush()
        logger.info(f"New user created: {email}")

    return user
