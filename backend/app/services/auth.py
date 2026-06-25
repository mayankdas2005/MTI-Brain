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

# ─── Bootstrap users (dev only — replace with OIDC before production deploy) ───

_BOOTSTRAP_USERS: dict[str, dict] = {
    "admin": {
        "username": "admin",
        "password": "$2b$12$cIf.CmlZ0pO2sAWQy4Yzr.TRNpeL/Tx9r8omOPdzbpgQiKKIsXGgq",
        "name": "Admin User",
        "email": "admin@milestone.tech",
        "groups": ["admin"],
    },
    "user": {
        "username": "user",
        "password": "$2b$12$s.DOjMeoCePS33QVqJ3sOuaro/fP8FHeXwtWYfsrhbWR8fkcNN6xO",
        "name": "Standard User",
        "email": "user@milestone.tech",
        "groups": ["user"],
    },
}


async def _check_password(password: str, password_hash: str) -> bool:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, bcrypt.checkpw, password.encode(), password_hash.encode()
    )


def _role_allowed(groups: list | None, role: str) -> bool:
    return role in set(groups or [])


def _bootstrap_for(username: str, role: str) -> dict | None:
    user = _BOOTSTRAP_USERS.get(role)
    if not user:
        return None
    key = (username or "").strip().lower()
    if key in {user["username"], user["email"]}:
        return user
    return None


async def authenticate_user(db: AsyncSession, username: str, password: str, role: str) -> dict | None:
    """Return the user dict if credentials are valid, else None.

    bcrypt.checkpw is CPU-bound and synchronous — offloaded to a thread
    pool so it does not block the async event loop.
    """
    key = (username or "").strip().lower()
    selected_role = role if role in {"admin", "user"} else "user"

    bootstrap = _bootstrap_for(key, selected_role)
    identity_keys = {key}
    if bootstrap:
        identity_keys.add(bootstrap["email"])

    result = await db.execute(
        select(MTIBrainUser).where(
            or_(
                func.lower(MTIBrainUser.email).in_(identity_keys),
                func.lower(MTIBrainUser.keycloak_sub).in_(identity_keys),
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
                "password": db_user.password_hash,
            }
        return None

    if bootstrap and await _check_password(password, bootstrap["password"]):
        return bootstrap
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
        await db.execute(
            update(MTIBrainUser)
            .where(MTIBrainUser.id == user.id)
            .values(name=name, groups=groups, password_hash=password_hash, last_login=now)
        )
        await db.flush()
        user.name = name
        user.groups = groups
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
