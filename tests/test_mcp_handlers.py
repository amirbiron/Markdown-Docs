"""כלי הקריאה של ה-MCP.

נבדקים ישירות, בלי להרים שרת MCP — זו כל הסיבה ש-handlers.py אינו
מייבא דבר מ-MCP.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.mcp import handlers
from app.mcp.auth import Identity
from app.models import User
from tests.conftest import (  # noqa: F401
    EMAIL,
    ORIGIN,
    PASSWORD,
    WRITE,
    anon,
    clean_projects,
    make_document,
    make_project,
    owner,
    seeded_admin,
)


@pytest.fixture
async def identity(seeded_admin):
    """הזהות שהכלים מקבלים.

    ה-User יוצא מהסשן מנותק, וזה בסדר כאן רק משום ששכבת השירות ניגשת
    ל-user.id בלבד והוא נטען לפני הסגירה. הגישה המפורשת ל-id אינה
    קישוט: היא מוודאת שהשדה באמת טעון, כך שאם מישהו יוסיף commit
    ל-fixture בעתיד הכשל יופיע כאן ולא בבדיקה אקראית כלשהי.
    """
    async with SessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.email == EMAIL))
        ).scalar_one()
        assert user.id is not None
    return Identity(user=user, scopes=frozenset({"read", "write"}))


# ── מפה ───────────────────────────────────────────────────────────────


async def test_map_returns_everything_in_one_call(owner, identity):
    """זה המדד: מפה מלאה בקריאה אחת, בלי סיבוב ביניים."""
    await make_project(owner, slug="docs", name="תיעוד")
    await make_document(owner, project="docs", slug="a", title="ראשון")
    await make_document(owner, project="docs", slug="b", title="שני")

    async with SessionLocal() as session:
        result = await handlers.map_documents(session, identity)

    assert result["ok"] is True
    assert result["project_count"] == 1
    assert result["document_count"] == 2
    titles = [d["title"] for d in result["projects"][0]["documents"]]
    assert titles == ["ראשון", "שני"]
    assert all(uuid.UUID(d["id"]) for d in result["projects"][0]["documents"])


async def test_map_does_not_carry_content(owner, identity):
    """המפה נועדה להיות זולה. תוכן נמשך רק במפורש."""
    await make_project(owner)
    await make_document(owner, content="גוף ארוך מאוד")

    async with SessionLocal() as session:
        result = await handlers.map_documents(session, identity)

    document = result["projects"][0]["documents"][0]
    assert "content" not in document
    assert document["size_bytes"] > 0


# ── חיפוש ─────────────────────────────────────────────────────────────


async def test_search_returns_rank_and_ids(owner, identity):
    await make_project(owner)
    await make_document(owner, title="התקנה", content="מדריך התקנה")

    async with SessionLocal() as session:
        result = await handlers.search(session, identity, "התקנה")

    assert result["ok"] is True
    hit = result["results"][0]
    assert hit["rank"] > 0
    assert uuid.UUID(hit["id"])
    assert "content" not in hit, "בלי include_content אין תוכן"


async def test_search_with_content_answers_in_one_call(owner, identity):
    """המדד המרכזי: משאלה לתוכן בקריאה אחת."""
    await make_project(owner)
    await make_document(owner, title="התקנה", content="השלב הראשון הוא התקנה")

    async with SessionLocal() as session:
        result = await handlers.search(session, identity, "התקנה", include_content=True)

    assert result["results"][0]["content"] == "השלב הראשון הוא התקנה"


async def test_search_content_limit_is_clamped(owner, identity):
    """ערך מוגזם נחתך ולא נדחה.

    יותר מסמכים מהתקרה, ובכוונה: עם שלושה מסמכים ותקרה של עשרה
    הבדיקה הייתה עוברת גם אם החיתוך לא היה קיים בכלל. הוכחת חיתוך
    דורשת שיהיה מה לחתוך.
    """
    await make_project(owner)
    total = handlers.CONTENT_LIMIT_MAX + 2
    for index in range(total):
        await make_document(owner, slug=f"d{index}", title=f"התקנה {index}", content="גוף")

    async with SessionLocal() as session:
        result = await handlers.search(
            session, identity, "התקנה", include_content=True, content_limit=9999
        )

    assert result["ok"] is True
    assert len(result["results"]) == total, "כל המסמכים אמורים לחזור כתוצאות"
    with_content = [h for h in result["results"] if "content" in h]
    assert len(with_content) == handlers.CONTENT_LIMIT_MAX


async def test_empty_search_is_a_clear_error(owner, identity):
    async with SessionLocal() as session:
        result = await handlers.search(session, identity, "   ")
    assert result["ok"] is False
    assert result["error"] == "empty_query"


async def test_no_results_still_gives_the_model_something(owner, identity):
    """לעולם לא רק "לא נמצא"."""
    await make_project(owner)
    await make_document(owner, title="התקנה", content="גוף")

    async with SessionLocal() as session:
        result = await handlers.search(session, identity, "זנזיבר")

    assert result["ok"] is False
    assert result["error"] == "no_results"
    assert "התקנה" in result["available_titles"]


# ── שליפת מסמך ────────────────────────────────────────────────────────


async def test_get_by_id_is_the_stable_path(owner, identity):
    await make_project(owner)
    created = await make_document(owner, content="גוף")

    async with SessionLocal() as session:
        result = await handlers.get_document(session, identity, document_id=created["id"])

    assert result["ok"] is True
    assert result["document"]["content"] == "גוף"


async def test_get_by_slug_pair_works(owner, identity):
    await make_project(owner)
    await make_document(owner, content="גוף")

    async with SessionLocal() as session:
        result = await handlers.get_document(
            session, identity, project_slug="docs", doc_slug="installation"
        )

    assert result["ok"] is True


async def test_get_by_title_works(owner, identity):
    await make_project(owner)
    await make_document(owner, title="מפת דרכים", content="גוף")

    async with SessionLocal() as session:
        result = await handlers.get_document(session, identity, title="מפת דרכים")

    assert result["ok"] is True
    assert result["document"]["title"] == "מפת דרכים"


async def test_ambiguous_title_returns_candidates_not_a_guess(owner, identity):
    """שתי כותרות זהות בשני פרויקטים — לא בוחרים עבור המודל."""
    await make_project(owner, slug="one", name="ראשון")
    await make_project(owner, slug="two", name="שני")
    await make_document(owner, project="one", slug="r", title="מפת דרכים")
    await make_document(owner, project="two", slug="r", title="מפת דרכים")

    async with SessionLocal() as session:
        result = await handlers.get_document(session, identity, title="מפת דרכים")

    assert result["error"] == "ambiguous_title"
    assert len(result["candidates"]) == 2
    assert all("id" in c for c in result["candidates"])


async def test_unknown_slug_lists_what_exists(owner, identity):
    await make_project(owner)
    await make_document(owner, slug="installation")

    async with SessionLocal() as session:
        result = await handlers.get_document(
            session, identity, project_slug="docs", doc_slug="instalation"
        )

    assert result["error"] == "not_found"
    assert "installation" in result["suggestions"], "שגיאת כתיב חייבת לקבל הצעה"
    assert result["available_documents"]


async def test_unknown_project_lists_what_exists(owner, identity):
    await make_project(owner, slug="docs")

    async with SessionLocal() as session:
        result = await handlers.get_document(
            session, identity, project_slug="dcs", doc_slug="x"
        )

    assert result["error"] == "project_not_found"
    assert "docs" in result["available_projects"]


async def test_malformed_id_is_rejected_clearly(owner, identity):
    async with SessionLocal() as session:
        result = await handlers.get_document(session, identity, document_id="not-a-uuid")
    assert result["error"] == "invalid_id"


async def test_no_identifier_at_all(owner, identity):
    async with SessionLocal() as session:
        result = await handlers.get_document(session, identity)
    assert result["error"] == "missing_identifier"


# ── גרסאות ────────────────────────────────────────────────────────────


async def test_versions_empty_explains_why(owner, identity):
    await make_project(owner)
    created = await make_document(owner)

    async with SessionLocal() as session:
        result = await handlers.list_versions(session, identity, created["id"])

    assert result["ok"] is True
    assert result["count"] == 0
    assert "נוצרת" in result["message"], "גם ריק צריך להסביר את עצמו"


async def test_version_content_is_reachable(owner, identity):
    """היכולת החדשה: התוכן היה בטבלה ולא היה נגיש בשום נתיב."""
    await make_project(owner)
    created = await make_document(owner, content="ראשון")
    await owner.put(
        "/api/projects/docs/docs/installation",
        json={"content": "שני"},
        headers=WRITE,
    )

    async with SessionLocal() as session:
        listing = await handlers.list_versions(session, identity, created["id"])
        assert listing["count"] == 1
        version_id = listing["versions"][0]["id"]
        fetched = await handlers.get_version(session, identity, version_id)

    assert fetched["ok"] is True
    assert fetched["version"]["content"] == "ראשון", "הגרסה שומרת את התוכן הקודם"


async def test_unknown_version_is_not_found(owner, identity):
    async with SessionLocal() as session:
        result = await handlers.get_version(session, identity, str(uuid.uuid4()))
    assert result["error"] == "not_found"
