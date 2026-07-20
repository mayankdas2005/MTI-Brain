"""Authentication endpoints."""

from app.api.v1.deps import CurrentUser, get_current_user
from app.core.config import settings
from app.core.logger import logger
from app.core.rate_limit import limiter
from app.db import get_async_session
from app.services import auth as auth_service
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()

_REFRESH_COOKIE = "mti_brain_refresh"


def _set_refresh_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        key=_REFRESH_COOKIE,
        value=raw_token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=settings.JWT_REFRESH_TOKEN_DAYS * 86400,
        path="/api/v1/auth",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=_REFRESH_COOKIE,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/api/v1/auth",
    )


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    username: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=1, max_length=128)
    role: str = Field("user", pattern="^(admin|user)$")


class AuthResponse(BaseModel):
    token: str
    user: dict


class MeResponse(BaseModel):
    user_id: str
    email: str
    name: str
    groups: list[str] = []


@router.post("/login", response_model=AuthResponse)
@limiter.limit(f"{settings.RATE_LIMIT_LOGIN_PER_MINUTE}/minute")
async def login(
    request: Request,
    response: Response,
    body: LoginRequest,
    db: AsyncSession = Depends(get_async_session),
):
    user_data = await auth_service.authenticate_user(db, body.username, body.password, body.role)
    if not user_data:
        logger.warning(
            f"Failed login for username={body.username!r}, role={body.role!r} from {request.client.host if request.client else 'unknown'}"
        )
        raise HTTPException(status_code=401, detail="Invalid username or password.")

    user_groups = user_data.get("groups", [])

    user = await auth_service.upsert_user(
        db,
        email=user_data["email"],
        name=user_data["name"],
        groups=user_groups,
    )

    token = auth_service.create_jwt_token(
        user_id=str(user.id),
        email=user_data["email"],
        name=user_data["name"],
        groups=user_groups,
    )

    refresh_token = await auth_service.create_refresh_token(db, str(user.id))
    await db.commit()
    _set_refresh_cookie(response, refresh_token)

    logger.info(f"User authenticated: {user_data['email']}")

    return AuthResponse(
        token=token,
        user={
            "user_id": str(user.id),
            "email": user_data["email"],
            "name": user_data["name"],
            "groups": user_groups,
        },
    )


@router.post("/refresh", response_model=AuthResponse)
@limiter.limit("30/minute")
async def refresh(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_async_session),
    mti_brain_refresh: str | None = Cookie(None),
):
    """Exchange a valid refresh token for a new access token + rotated refresh token."""
    if not mti_brain_refresh:
        raise HTTPException(status_code=401, detail="No refresh token provided.")

    rt = await auth_service.validate_refresh_token(db, mti_brain_refresh)
    if not rt:
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=401, detail="Refresh token expired or revoked.")

    # Revoke the used refresh token (rotation — each token is single-use)
    await auth_service.revoke_refresh_token(db, mti_brain_refresh)

    # Load user data
    from app.models.user import MTIBrainUser
    from sqlalchemy import select
    result = await db.execute(select(MTIBrainUser).where(MTIBrainUser.id == rt.user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found.")

    # Issue new token pair
    access_token = auth_service.create_jwt_token(
        user_id=str(user.id),
        email=user.email,
        name=user.name,
        groups=user.groups or [],
    )
    new_refresh = await auth_service.create_refresh_token(db, str(user.id))
    await db.commit()
    _set_refresh_cookie(response, new_refresh)

    return AuthResponse(
        token=access_token,
        user={
            "user_id": str(user.id),
            "email": user.email,
            "name": user.name,
            "groups": user.groups or [],
        },
    )


@router.post("/logout")
async def logout(
    response: Response,
    db: AsyncSession = Depends(get_async_session),
    mti_brain_refresh: str | None = Cookie(None),
):
    """Revoke the refresh token and clear the cookie."""
    if mti_brain_refresh:
        await auth_service.revoke_refresh_token(db, mti_brain_refresh)
        await db.commit()
    _clear_refresh_cookie(response)
    return {"status": "ok"}


@router.get("/me", response_model=MeResponse)
async def get_me(current_user: CurrentUser = Depends(get_current_user)):
    return MeResponse(
        user_id=str(current_user.id),
        email=current_user.email,
        name=current_user.name,
        groups=current_user.groups,
    )
