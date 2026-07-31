"""תלויות אימות."""

from __future__ import annotations

import uuid

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models import User
from app.security import COOKIE_NAME, read_token


async def optional_user(
    request: Request, session: AsyncSession = Depends(get_session)
) -> User | None:
    """מחזיר את המשתמש המחובר, או None. לא זורק — נתיבים פומביים משתמשים בזה."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None

    payload = read_token(token)  # חתימה + תפוגה
    if payload is None:
        return None

    try:
        user_id = uuid.UUID(payload["uid"])
    except (ValueError, TypeError):
        return None

    user = await session.get(User, user_id)
    if user is None:
        return None

    # שלושת התנאים חייבים להתקיים יחד. session_version הוא זה שמאפשר
    # לנתק את כל המכשירים בלי טבלת sessions.
    if user.session_version != payload["sv"]:
        return None
    return user


async def require_user(user: User | None = Depends(optional_user)) -> User:
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "נדרשת התחברות")
    return user
