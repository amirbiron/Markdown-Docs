"""ערכים ו-fixtures משותפים לכל הבדיקות."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.seed import seed_admin

_settings = get_settings()
_allowlist = sorted(_settings.origin_allowlist)

# ה-Origin שהבדיקות שולחות בכל בקשה משנת מצב. בפיתוח אין allowlist מפורש
# והמקורות המקומיים מאושרים לפי הכלל, ולכן נבחר כזה.
if _allowlist:
    ORIGIN = _allowlist[0]
elif _settings.allow_loopback_origins:
    ORIGIN = "http://localhost:8000"
else:
    # לא assert: תחת python -O הוא נמחק, והכישלון היה חוזר בתור IndexError
    # מבלבל בשורה הבאה במקום ההודעה הזו.
    raise RuntimeError(
        "origin_allowlist ריק — הגדירו ALLOWED_ORIGINS או RENDER_EXTERNAL_URL, "
        "או הריצו עם ENVIRONMENT=development"
    )
WRITE = {"Origin": ORIGIN}

EMAIL = "admin@example.com"
PASSWORD = "correct-horse-battery"  # noqa: S105 — fixture לבדיקות, לא סוד אמיתי


@pytest.fixture(scope="session")
async def seeded_admin():
    """יוצר את המשתמש היחיד. httpx לא מריץ lifespan, ולכן זה נעשה כאן.

    ההרצה כפולה בכוונה — ה-seed חייב להיות idempotent.
    """
    async with SessionLocal() as session:
        await seed_admin(session)
        await seed_admin(session)
    async with SessionLocal() as session:
        count = (
            await session.execute(text("SELECT count(*) FROM users WHERE email = :e"), {"e": EMAIL})
        ).scalar_one()
    assert count == 1, f"ה-seed יצר {count} משתמשים במקום אחד"
    yield


@pytest.fixture
async def clean_projects(seeded_admin):
    """כל טסט מתחיל מספרייה ריקה."""
    async with SessionLocal() as session:
        await session.execute(text("DELETE FROM projects"))
        await session.commit()
    yield


@pytest.fixture
async def owner(clean_projects):
    """לקוח מחובר."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/auth/login", json={"email": EMAIL, "password": PASSWORD}, headers=WRITE
        )
        assert response.status_code == 200, response.text
        yield client


@pytest.fixture
async def anon(clean_projects):
    """לקוח לא מחובר."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def make_project(owner, slug="docs", name="התיעוד", visibility="private"):
    response = await owner.post(
        "/api/projects",
        json={"name": name, "slug": slug, "visibility": visibility},
        headers=WRITE,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def make_document(owner, project="docs", slug="installation", title="התקנה", content="# התקנה"):
    response = await owner.post(
        f"/api/projects/{project}/docs",
        json={"title": title, "slug": slug, "content": content},
        headers=WRITE,
    )
    assert response.status_code == 201, response.text
    return response.json()
