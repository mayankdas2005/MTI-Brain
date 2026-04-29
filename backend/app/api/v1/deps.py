"""Shared FastAPI dependencies for the v1 API."""

import uuid

from app.core.logger import logger
from app.services.auth import decode_jwt_token
from fastapi import HTTPException, Request
from pydantic import BaseModel


class CurrentUser(BaseModel):
    """Authenticated user context extracted from the JWT."""

    id: uuid.UUID
    email: str
    name: str
    groups: list[str] = []


async def get_current_user(request: Request) -> CurrentUser:
    auth_header = request.headers.get("Authorization")
    if not auth_header or not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Authorization token is missing or malformed.",
        )

    token = auth_header.split(" ", 1)[1]
    payload = decode_jwt_token(token)
    if payload is None:
        raise HTTPException(
            status_code=401,
            detail="Token is expired or invalid. Please log in again.",
        )

    try:
        return CurrentUser(
            id=uuid.UUID(payload["user_id"]),
            email=payload["email"],
            name=payload["name"],
            groups=payload.get("groups", []),
        )
    except (KeyError, ValueError) as e:
        logger.warning(f"Malformed JWT payload: {e}")
        raise HTTPException(status_code=401, detail="Invalid token payload.")
