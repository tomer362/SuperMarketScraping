from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db import get_session
from models import ShoppingList, User, UserSession
from settings import get_settings


USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,32}$")


def normalize_username(username: str) -> str:
    return username.strip().casefold()


def validate_username(username: str) -> str:
    clean = username.strip()
    if not USERNAME_RE.fullmatch(clean):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Username must be 3-32 characters and use only letters, numbers, "
                "or underscores"
            ),
        )
    return clean


def validate_password(password: str) -> None:
    if len(password) < 6:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Password must be at least 6 characters",
        )


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    iterations = 200_000
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    salt_b64 = base64.b64encode(salt).decode("ascii")
    hash_b64 = base64.b64encode(derived).decode("ascii")
    return f"pbkdf2_sha256${iterations}${salt_b64}${hash_b64}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations_raw, salt_b64, expected_b64 = stored_hash.split("$", 3)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    iterations = int(iterations_raw)
    salt = base64.b64decode(salt_b64.encode("ascii"))
    expected = base64.b64decode(expected_b64.encode("ascii"))
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_session_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, hash_session_token(token)


async def create_session_cookie(
    response: Response,
    session: AsyncSession,
    user: User,
) -> UserSession:
    settings = get_settings()
    token, token_hash = generate_session_token()
    expires_at = datetime.now(timezone.utc) + timedelta(
        days=settings.session_max_age_days
    )
    user_session = UserSession(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=expires_at,
        last_seen_at=datetime.now(timezone.utc),
    )
    session.add(user_session)
    await session.flush()
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_max_age_days * 24 * 60 * 60,
        expires=int(expires_at.timestamp()),
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )
    return user_session


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=get_settings().session_cookie_name, path="/")


async def destroy_session_by_request(
    request: Request,
    session: AsyncSession,
    response: Response,
) -> None:
    token = request.cookies.get(get_settings().session_cookie_name)
    if token:
        await session.execute(
            delete(UserSession).where(UserSession.token_hash == hash_session_token(token))
        )
    clear_session_cookie(response)


async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> User:
    token = request.cookies.get(get_settings().session_cookie_name)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    session_row = (
        await session.execute(
            select(UserSession)
            .options(selectinload(UserSession.user))
            .where(UserSession.token_hash == hash_session_token(token))
        )
    ).scalar_one_or_none()
    if not session_row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    now = datetime.now(timezone.utc)
    expires_at = session_row.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= now:
        await session.delete(session_row)
        await session.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")

    session_row.last_seen_at = now
    await session.commit()
    return session_row.user


async def create_default_list_for_user(session: AsyncSession, user: User) -> ShoppingList:
    shopping_list = ShoppingList(user_id=user.id, name="הרשימה שלי")
    session.add(shopping_list)
    await session.flush()
    return shopping_list
