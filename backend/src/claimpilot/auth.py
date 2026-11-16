"""Authentication and role checks. PyJWT + pwdlib (argon2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from pwdlib import PasswordHash
from pydantic import BaseModel
from sqlalchemy import select

from claimpilot.config import get_settings
from claimpilot.db import get_sessionmaker
from claimpilot.models import User

ALGORITHM = "HS256"
hasher = PasswordHash.recommended()
oauth2 = OAuth2PasswordBearer(tokenUrl="/auth/login")


class Role(StrEnum):
    CLAIMANT = "claimant"
    ADJUSTER = "adjuster"
    AUDITOR = "auditor"


class CurrentUser(BaseModel):
    id: int
    email: str
    name: str
    role: Role
    policy_number: str | None = None


def issue_token(user: User) -> str:
    s = get_settings()
    now = datetime.now(UTC)
    claims = {
        "sub": str(user.id),
        "role": user.role,
        "iat": now,
        "exp": now + timedelta(minutes=s.jwt_ttl_minutes),
    }
    return jwt.encode(claims, s.jwt_secret, algorithm=ALGORITHM)


async def authenticate(email: str, password: str) -> User | None:
    async with get_sessionmaker()() as session:
        user = await session.scalar(select(User).where(User.email == email.lower()))
    if user is None:
        hasher.hash(password)  # spend the same time as a real check (no user enumeration)
        return None
    return user if hasher.verify(password, user.password_hash) else None


async def current_user(token: Annotated[str, Depends(oauth2)]) -> CurrentUser:
    unauthorized = HTTPException(
        status.HTTP_401_UNAUTHORIZED, "Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )  # fmt: skip
    try:
        payload = jwt.decode(token, get_settings().jwt_secret, algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        raise unauthorized from exc
    async with get_sessionmaker()() as session:
        user = await session.get(User, int(payload["sub"]))
    if user is None:
        raise unauthorized
    return CurrentUser(
        id=user.id, email=user.email, name=user.name, role=Role(user.role),
        policy_number=user.policy_number,
    )  # fmt: skip


def require(*roles: Role):
    """Dependency: allow only these roles (enforced server-side on every endpoint)."""

    async def check(user: Annotated[CurrentUser, Depends(current_user)]) -> CurrentUser:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Not allowed for your role")
        return user

    return check
