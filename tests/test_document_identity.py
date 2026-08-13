"""המזהה היציב של מסמך.

הבדיקות כאן מגנות על ההחלטה לחשוף `id`. הן מתארות תרחיש שקורה
בפועל: slug שמתפנה נתפס מחדש, וצרכן שמחזיק את ה-slug הישן מגיע
בשקט למסמך אחר.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import User
from app.services import documents as document_service
from app.services.errors import NotFound
from tests.conftest import EMAIL, WRITE, make_document, make_project


async def _admin() -> User:
    async with SessionLocal() as session:
        return (
            await session.execute(select(User).where(User.email == EMAIL))
        ).scalar_one()


async def test_private_document_exposes_id(owner):
    """הבעלים מקבל את המזהה; אנונימי לא."""
    await make_project(owner, visibility="public")
    created = await make_document(owner)
    assert "id" in created, "לבעלים חייב להיות מזהה יציב"
    uuid.UUID(created["id"])


async def test_public_view_has_no_id(owner, anon):
    """התצוגה הציבורית נשארת בלי מזהים, כמו LinkPublic."""
    await make_project(owner, visibility="public")
    await make_document(owner)

    response = await anon.get("/api/projects/docs/docs/installation")
    assert response.status_code == 200, response.text
    assert "id" not in response.json()


async def test_stale_slug_resolves_to_a_different_document(owner):
    """זו הבעיה עצמה, מודגמת: slug ישן מצביע למסמך אחר.

    מסמך א' משחרר את ה-slug, מסמך ב' תופס אותו, ומי שהחזיק את ה-slug
    המקורי מקבל עכשיו את ב' — בלי שום שגיאה. זה מה שהמזהה בא לפתור.
    """
    await make_project(owner)
    first = await make_document(owner, slug="roadmap", title="מפת דרכים", content="ראשון")

    # א' עובר ל-slug אחר ומפנה את "roadmap".
    moved = await owner.put(
        "/api/projects/docs/docs/roadmap",
        json={"slug": "roadmap-2024"},
        headers=WRITE,
    )
    assert moved.status_code == 200, moved.text

    # ב' תופס את ה-slug שהתפנה.
    second = await make_document(owner, slug="roadmap", title="מפת דרכים חדשה", content="שני")
    assert second["id"] != first["id"]

    # מי שהחזיק את ה-slug המקורי מקבל עכשיו מסמך אחר לגמרי.
    by_stale_slug = await owner.get("/api/projects/docs/docs/roadmap")
    assert by_stale_slug.status_code == 200
    assert by_stale_slug.json()["content"] == "שני"

    # אבל המזהה המקורי עדיין מגיע למסמך הנכון.
    async with SessionLocal() as session:
        document = await document_service.load_document_by_id(
            session, uuid.UUID(first["id"]), await _admin()
        )
        assert document.content == "ראשון"
        assert document.slug == "roadmap-2024"


async def test_load_by_id_rejects_other_users_private_project(owner):
    """IDOR: מזהה תקין אינו הרשאה.

    הפרויקט פרטי ואין צופה כלל, ולכן השליפה חייבת להיכשל — ובשגיאת
    "לא נמצא", לא "אין הרשאה", כדי לא לאשר שהמשאב קיים.
    """
    await make_project(owner, visibility="private")
    created = await make_document(owner)

    async with SessionLocal() as session:
        with pytest.raises(NotFound):
            await document_service.load_document_by_id(
                session, uuid.UUID(created["id"]), None
            )


async def test_load_by_id_allows_anonymous_on_public_project(owner):
    """פרויקט פומבי גלוי גם בלי צופה מזוהה."""
    await make_project(owner, visibility="public")
    created = await make_document(owner)

    async with SessionLocal() as session:
        document = await document_service.load_document_by_id(
            session, uuid.UUID(created["id"]), None
        )
        assert document.slug == "installation"


async def test_require_owner_blocks_public_project_of_another(owner):
    """נראות אינה בעלות: פרויקט פומבי נקרא, אבל לא נערך."""
    await make_project(owner, visibility="public")
    created = await make_document(owner)

    async with SessionLocal() as session:
        with pytest.raises(NotFound):
            await document_service.load_document_by_id(
                session, uuid.UUID(created["id"]), None, require_owner=True
            )


async def test_unknown_id_is_not_found(owner):
    """מזהה תקין שאינו קיים אינו קורס."""
    await make_project(owner)
    async with SessionLocal() as session:
        with pytest.raises(NotFound):
            await document_service.load_document_by_id(
                session, uuid.uuid4(), await _admin()
            )
