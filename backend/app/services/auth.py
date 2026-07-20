"""Authentication service - JWT token management and user credential validation."""

import asyncio
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from app.core.config import settings
from app.core.logger import logger
from app.models.refresh_token import RefreshToken
from app.models.user import MTIBrainUser
from sqlalchemy import delete, func, or_, select, update
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
    """Create a short-lived access token (default 15 minutes)."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": email,
        "user_id": user_id,
        "email": email,
        "name": name,
        "groups": groups,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.JWT_ACCESS_TOKEN_MINUTES),
    }
    return jwt.encode(payload, settings.jwt_signing_key, algorithm=settings.JWT_ALGORITHM)


def decode_jwt_token(token: str) -> dict | None:
    # Accept both RS256 and HS256 during migration so old tokens still work
    _algorithms = [settings.JWT_ALGORITHM]
    if settings.JWT_ALGORITHM == "RS256" and "HS256" not in _algorithms:
        _algorithms.append("HS256")
    try:
        return jwt.decode(token, settings.jwt_verify_key, algorithms=_algorithms)
    except jwt.InvalidAlgorithmError:
        # Token signed with HS256 but we're verifying with RSA public key — retry with shared secret
        if settings.JWT_ALGORITHM == "RS256":
            try:
                return jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
            except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
                return None
        return None
    except jwt.ExpiredSignatureError:
        logger.debug("JWT expired")
        return None
    except jwt.InvalidTokenError as e:
        logger.debug(f"JWT invalid: {e}")
        return None


# ─── Refresh token management ───


def _hash_token(token: str) -> str:
    """SHA-256 hash of the raw refresh token — only the hash is stored in DB."""
    return hashlib.sha256(token.encode()).hexdigest()


async def create_refresh_token(db: AsyncSession, user_id: str) -> str:
    """Generate a cryptographically random refresh token, store its hash in DB."""
    raw_token = secrets.token_urlsafe(48)
    token_hash = _hash_token(raw_token)
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.JWT_REFRESH_TOKEN_DAYS)

    rt = RefreshToken(
        user_id=user_id,
        token_hash=token_hash,
        expires_at=expires_at,
    )
    db.add(rt)
    await db.flush()
    return raw_token


async def validate_refresh_token(db: AsyncSession, raw_token: str) -> RefreshToken | None:
    """Validate a refresh token: exists, not revoked, not expired."""
    token_hash = _hash_token(raw_token)
    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.token_hash == token_hash,
            RefreshToken.revoked == False,  # noqa: E712
            RefreshToken.expires_at > datetime.now(timezone.utc),
        )
    )
    return result.scalar_one_or_none()


async def revoke_refresh_token(db: AsyncSession, raw_token: str) -> bool:
    """Revoke a single refresh token. Returns True if found and revoked."""
    token_hash = _hash_token(raw_token)
    result = await db.execute(
        update(RefreshToken)
        .where(RefreshToken.token_hash == token_hash)
        .values(revoked=True)
    )
    await db.flush()
    return result.rowcount > 0


async def revoke_all_user_tokens(db: AsyncSession, user_id: str) -> int:
    """Revoke all refresh tokens for a user (e.g. password change, security event)."""
    result = await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked == False)  # noqa: E712
        .values(revoked=True)
    )
    await db.flush()
    return result.rowcount


async def cleanup_expired_tokens(db: AsyncSession) -> int:
    """Delete expired refresh tokens. Call periodically to keep the table small."""
    result = await db.execute(
        delete(RefreshToken).where(RefreshToken.expires_at < datetime.now(timezone.utc))
    )
    await db.flush()
    return result.rowcount


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
