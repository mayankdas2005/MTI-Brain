"""Authentication endpoints."""

from app.api.v1.deps import CurrentUser, get_current_user
from app.core.config import settings
from app.core.logger import logger
from app.core.rate_limit import limiter
from app.db import get_async_session
from app.services import auth as auth_service
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter()


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    username: str = Field(..., min_length=1, max_length=128)
    password: str = Field(..., min_length=1, max_length=128)


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
    body: LoginRequest,
    db: AsyncSession = Depends(get_async_session),
):
    user_data = await auth_service.authenticate_user(body.username, body.password)
    if not user_data:
        logger.warning(
            f"Failed login for username={body.username!r} from {request.client.host if request.client else 'unknown'}"
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


@router.get("/me", response_model=MeResponse)
async def get_me(current_user: CurrentUser = Depends(get_current_user)):
    return MeResponse(
        user_id=str(current_user.id),
        email=current_user.email,
        name=current_user.name,
        groups=current_user.groups,
    )
