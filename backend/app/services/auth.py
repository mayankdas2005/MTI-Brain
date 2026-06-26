"""Authentication service - JWT token management and user credential validation."""

import asyncio
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from app.core.config import settings
from app.core.logger import logger
from app.models.user import MTIBrainUser
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession


async def _check_password(password: str, password_hash: str) -> bool:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, bcrypt.checkpw, password.encode(), password_hash.encode()
    )


def _role_allowed(groups: list | None, role: str) -> bool:
    return role in set(groups or [])


async def authenticate_user(db: AsyncSession, username: str, password: str, role: str) -> dict | None:
    """Return the user dict if credentials are valid, else None.

    bcrypt.checkpw is CPU-bound and synchronous — offloaded to a thread
    pool so it does not block the async event loop.
    """
    key = (username or "").strip().lower()
    selected_role = role if role in {"admin", "user"} else "user"
    identity_keys = {key}

    result = await db.execute(
        select(MTIBrainUser).where(
            or_(
                func.lower(MTIBrainUser.email).in_(identity_keys),
                func.lower(MTIBrainUser.keycloak_sub).in_(identity_keys),
                func.lower(func.split_part(MTIBrainUser.email, "@", 1)).in_(identity_keys),
                func.lower(func.split_part(MTIBrainUser.keycloak_sub, "@", 1)).in_(identity_keys),
            )
        )
    )
    db_user = result.scalar_one_or_none()

    if db_user and db_user.password_hash:
        if not _role_allowed(db_user.groups, selected_role):
            return None
        if await _check_password(password, db_user.password_hash):
            return {
                "email": db_user.email,
                "name": db_user.name,
                "groups": db_user.groups or [selected_role],
            }
        return None
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
    password_hash: str | None = None,
) -> MTIBrainUser:
    """Create or update a user record on login (keyed by email)."""
    result = await db.execute(select(MTIBrainUser).where(MTIBrainUser.email == email))
    user = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)

    if user:
        values: dict[str, object] = {
            "name": name,
            "groups": groups,
            "last_login": now,
        }
        if password_hash is not None:
            values["password_hash"] = password_hash
        await db.execute(
            update(MTIBrainUser)
            .where(MTIBrainUser.id == user.id)
            .values(**values)
        )
        await db.flush()
        user.name = name
        user.groups = groups
        if password_hash is not None:
            user.password_hash = password_hash
        user.last_login = now
    else:
        user = MTIBrainUser(
            keycloak_sub=email,
            email=email,
            name=name,
            password_hash=password_hash,
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
