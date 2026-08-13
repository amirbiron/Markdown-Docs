"""כלי הכתיבה של ה-MCP.

נבדקים ישירות מול ה-DB, כי מה שחשוב כאן אינו העיצוב אלא מה באמת קרה
בבסיס הנתונים: האם נוצרה גרסה, האם ה-slug לא זז, ומה קורה למסמך של
מישהו אחר.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import func, select

from app.db import SessionLocal
from app.mcp import handlers
from app.mcp.auth import Identity
from app.models import Document, DocumentVersion, User
from tests.conftest import (  # noqa: F401
    EMAIL,
    ORIGIN,
    PASSWORD,
    WRITE,
    clean_projects,
    make_document,
    make_project,
    owner,
    seeded_admin,
)


@pytest.fixture
async def identity(seeded_admin):
    async with SessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.email == EMAIL))
        ).scalar_one()
    return Identity(user=user, scopes=frozenset({"read", "write"}))


async def _version_count(document_id) -> int:
    async with SessionLocal() as session:
        return (
            await session.execute(
                select(func.count())
                .select_from(DocumentVersion)
                .where(DocumentVersion.document_id == document_id)
            )
        ).scalar_one()


async def _reload(document_id) -> Document:
    async with SessionLocal() as session:
        return (
            await session.execute(select(Document).where(Document.id == document_id))
        ).scalar_one()


# ── יצירה ─────────────────────────────────────────────────────────────


async def test_create_writes_a_real_document(owner, identity):
    await make_project(owner, slug="docs")

    async with SessionLocal() as session:
        result = await handlers.create_document(
            session, identity, "docs", "מדריך התקנה", content="# שלב ראשון"
        )

    assert result["ok"] is True
    assert result["created"] is True

    stored = await _reload(uuid.UUID(result["document"]["id"]))
    assert stored.title == "מדריך התקנה"
    assert stored.content == "# שלב ראשון"


async def test_create_derives_a_slug_from_the_title(owner, identity):
    await make_project(owner, slug="docs")

    async with SessionLocal() as session:
        result = await handlers.create_document(session, identity, "docs", "Getting Started")

    assert result["document"]["doc_slug"] == "getting-started"


async def test_create_rejects_an_empty_title(owner, identity):
    await make_project(owner, slug="docs")

    async with SessionLocal() as session:
        result = await handlers.create_document(session, identity, "docs", "   ")

    assert result["error"] == "missing_title"


async def test_create_on_a_taken_slug_says_so_and_does_not_overwrite(owner, identity):
    """התנגשות slug מחזירה שגיאה מובנת, ולא דורסת את הקיים בשקט."""
    await make_project(owner, slug="docs")
    await make_document(owner, project="docs", slug="installation", content="המקורי")

    async with SessionLocal() as session:
        result = await handlers.create_document(
            session, identity, "docs", "אחר", content="חדש", slug="installation"
        )

    assert result["error"] == "slug_taken"
    assert "message" in result

    async with SessionLocal() as session:
        existing = (
            await session.execute(select(Document).where(Document.slug == "installation"))
        ).scalar_one()
        assert existing.content == "המקורי"


async def test_create_in_an_unknown_project_lists_what_exists(owner, identity):
    """שגיאת "לא נמצא" חייבת להיות ניתנת להמשך."""
    await make_project(owner, slug="docs", name="תיעוד")

    async with SessionLocal() as session:
        result = await handlers.create_document(session, identity, "dosc", "כותרת")

    assert result["ok"] is False
    assert "docs" in str(result), "צריך להחזיר את מה שכן קיים"


# ── עדכון ─────────────────────────────────────────────────────────────


async def test_update_keeps_the_old_content_as_a_version(owner, identity):
    await make_project(owner)
    created = await make_document(owner, content="הישן")
    document_id = uuid.UUID(created["id"])

    async with SessionLocal() as session:
        result = await handlers.update_document(
            session, identity, str(document_id), content="החדש"
        )

    assert result["ok"] is True
    assert (await _reload(document_id)).content == "החדש"
    assert await _version_count(document_id) == 1


async def test_the_same_content_twice_does_not_pile_up_versions(owner, identity):
    """ההצהרה idempotentHint=True חייבת להיות נכונה."""
    await make_project(owner)
    created = await make_document(owner, content="הישן")
    document_id = uuid.UUID(created["id"])

    for _ in range(3):
        async with SessionLocal() as session:
            await handlers.update_document(session, identity, str(document_id), content="החדש")

    assert await _version_count(document_id) == 1


async def test_changing_the_title_does_not_move_the_slug(owner, identity):
    """זו ההתנהגות שמגינה על כל קישור קיים למסמך.

    ב-API יש slug_from_title; דרך ה-MCP הוא לא נחשף בכוונה, כי סוכן
    ששינה כותרת ובלי לשים לב שבר את הכתובת אינו התנהגות סבירה.
    """
    await make_project(owner)
    created = await make_document(owner, slug="installation", title="התקנה")
    document_id = uuid.UUID(created["id"])

    async with SessionLocal() as session:
        result = await handlers.update_document(
            session, identity, str(document_id), title="התקנה מתקדמת"
        )

    assert result["ok"] is True
    stored = await _reload(document_id)
    assert stored.title == "התקנה מתקדמת"
    assert stored.slug == "installation", "ה-slug זז בלי שביקשו"


async def test_new_slug_moves_it_when_asked_explicitly(owner, identity):
    await make_project(owner)
    created = await make_document(owner, slug="installation")
    document_id = uuid.UUID(created["id"])

    async with SessionLocal() as session:
        result = await handlers.update_document(
            session, identity, str(document_id), new_slug="setup"
        )

    assert result["ok"] is True
    assert (await _reload(document_id)).slug == "setup"


async def test_update_with_nothing_to_change_is_an_error(owner, identity):
    await make_project(owner)
    created = await make_document(owner)

    async with SessionLocal() as session:
        result = await handlers.update_document(session, identity, created["id"])

    assert result["error"] == "nothing_to_update"


async def test_update_with_a_malformed_id_is_clear(owner, identity):
    async with SessionLocal() as session:
        result = await handlers.update_document(session, identity, "not-a-uuid", content="x")

    assert result["error"] == "invalid_id"


async def test_update_of_an_unknown_id_is_not_found(owner, identity):
    async with SessionLocal() as session:
        result = await handlers.update_document(
            session, identity, str(uuid.uuid4()), content="x"
        )

    assert result["error"] == "not_found"


# ── הוספה בסוף ────────────────────────────────────────────────────────


async def test_append_adds_to_the_end_with_a_blank_line(owner, identity):
    """שורה ריקה מפרידה — בלעדיה ההוספה נדבקת לפסקה האחרונה."""
    await make_project(owner)
    created = await make_document(owner, content="## פסקה")
    document_id = uuid.UUID(created["id"])

    async with SessionLocal() as session:
        result = await handlers.append_document(session, identity, str(document_id), "שורה חדשה")

    assert result["ok"] is True
    assert (await _reload(document_id)).content == "## פסקה\n\nשורה חדשה"


async def test_append_to_an_empty_document_does_not_lead_with_blank_lines(owner, identity):
    await make_project(owner)
    created = await make_document(owner, content="")
    document_id = uuid.UUID(created["id"])

    async with SessionLocal() as session:
        await handlers.append_document(session, identity, str(document_id), "ראשון")

    assert (await _reload(document_id)).content == "ראשון"


async def test_append_does_not_double_the_separator(owner, identity):
    """תוכן שכבר מסתיים בשורה ריקה לא מקבל עוד אחת."""
    await make_project(owner)
    created = await make_document(owner, content="פסקה\n\n")
    document_id = uuid.UUID(created["id"])

    async with SessionLocal() as session:
        await handlers.append_document(session, identity, str(document_id), "נוסף")

    assert (await _reload(document_id)).content == "פסקה\n\nנוסף"


async def test_append_is_not_idempotent_and_that_is_declared(owner, identity):
    """כל קריאה מאריכה — ולכן idempotentHint=False בהצהרת הכלי."""
    await make_project(owner)
    created = await make_document(owner, content="בסיס")
    document_id = uuid.UUID(created["id"])

    for _ in range(2):
        async with SessionLocal() as session:
            await handlers.append_document(session, identity, str(document_id), "שורה")

    assert (await _reload(document_id)).content == "בסיס\n\nשורה\n\nשורה"


async def test_append_rejects_empty_text(owner, identity):
    await make_project(owner)
    created = await make_document(owner)

    async with SessionLocal() as session:
        result = await handlers.append_document(session, identity, created["id"], "")

    assert result["error"] == "empty_text"


# ── הזהות שמבצעת את הכתיבה ────────────────────────────────────────────


async def test_writing_without_an_identity_finds_nothing(owner, identity):
    """כתיבה דורשת בעלות, ואי-בעלות מוחזרת כ-not_found ולא כ-forbidden.

    forbidden היה מאשר שהמסמך קיים — דליפה קטנה אבל אמיתית. גם מסמך
    בפרויקט פומבי אינו ניתן לעריכה על ידי מי שאינו הבעלים.
    """
    await make_project(owner, visibility="public")
    created = await make_document(owner)
    anonymous = Identity(user=None, scopes=frozenset({"read", "write"}))

    async with SessionLocal() as session:
        result = await handlers.update_document(
            session, anonymous, created["id"], content="חטיפה"
        )

    assert result["error"] == "not_found"
    assert (await _reload(uuid.UUID(created["id"]))).content == "# התקנה"
