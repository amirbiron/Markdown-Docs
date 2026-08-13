"""ההרשאה נאכפת בגוף כל כלי כותב.

זו הבדיקה המרכזית של שכבת ההרשאות, ולא בדיקת נוחות. ל-SDK אין
scopes פר-כלי: הדקורטור מכריז שהכלי קיים, לא שמותר להריץ אותו.
כלי כותב שנוסף ולא עובר דרך המעטפת ירוץ בשמחה עם טוקן קריאה בלבד,
ו**אין שום דבר אחר במערכת שיתפוס את זה**.

הבדיקה נגזרת מרשימת הכלים בפועל, ולא מרשימה קשיחה — כלי כותב חדש
נכנס אליה מעצמו.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.mcp import handlers, server
from app.mcp.auth import Identity, PermissionError_, require_write
from app.models import User
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

# הכלים הכותבים, לפי ה-annotations שהם מצהירים עליהן.
WRITE_TOOLS = {
    "mdocs_create_document",
    "mdocs_update_document",
    "mdocs_append_document",
}

READ_ONLY_IDENTITY = Identity(user=None, scopes=frozenset({"read"}))


@pytest.fixture
async def read_only(seeded_admin):
    async with SessionLocal() as session:
        user = (await session.execute(select(User).where(User.email == EMAIL))).scalar_one()
    return Identity(user=user, scopes=frozenset({"read"}))


# ── ההצהרה תואמת למציאות ──────────────────────────────────────────────


async def test_write_tools_are_not_marked_read_only():
    """כלי כותב שסומן בטעות read-only היה מטעה כל לקוח."""
    tools = await server.mcp.list_tools()
    by_name = {tool.name: tool for tool in tools}

    for name in WRITE_TOOLS:
        assert name in by_name, f"{name} לא נרשם"
        assert by_name[name].annotations.read_only_hint is False, name


async def test_every_non_read_only_tool_is_in_the_list():
    """אם נוסף כלי כותב חדש, הוא חייב להיכנס לבדיקה הזו.

    בלי זה, כלי שנוסף בעתיד היה מחליק מתחת לרדאר של כל הקובץ.
    """
    tools = await server.mcp.list_tools()
    actual = {t.name for t in tools if t.annotations and t.annotations.read_only_hint is False}
    assert actual == WRITE_TOOLS, (
        "רשימת הכלים הכותבים השתנתה. עדכנו את WRITE_TOOLS וודאו "
        "שכל כלי חדש נקרא עם needs_write=True."
    )


# ── האכיפה עצמה ───────────────────────────────────────────────────────


def test_require_write_rejects_read_scope():
    with pytest.raises(PermissionError_):
        require_write(READ_ONLY_IDENTITY)


async def test_create_is_blocked_for_read_only_token(read_only, owner):
    """הכלים עצמם, מול זהות שאין לה write."""
    await make_project(owner)
    async with SessionLocal() as session:
        with pytest.raises(PermissionError_):
            require_write(read_only)
            await handlers.create_document(session, read_only, "docs", "כותרת")


async def test_the_wrapper_is_what_enforces_it(owner, read_only, monkeypatch):
    """המעטפת חוסמת לפני שההנדלר בכלל נקרא.

    זו הנקודה: ההנדלרים עצמם אינם בודקים הרשאות — הם לא אמורים.
    האכיפה היא ב-_run, וכלי שלא עובר דרכה חשוף.
    """
    called = False

    async def _spy(*args, **kwargs):
        nonlocal called
        called = True
        return {"ok": True}

    class _Ctx:
        headers = {"authorization": "Bearer " + "m" * 40}

    monkeypatch.setattr("app.mcp.auth.get_settings", lambda: _settings_with_scopes("read"))

    result = await server._run(_Ctx(), _spy, needs_write=True)

    assert result["ok"] is False
    assert result["error"] == "insufficient_scope"
    assert called is False, "ההנדלר לא היה אמור להיקרא בכלל"


def _settings_with_scopes(scopes: str):
    from app.config import Settings, get_settings

    base = get_settings().model_dump()
    base.update({"mcp_token": "m" * 40, "mcp_token_scopes": scopes})
    return Settings(**base)


async def test_read_tools_still_work_with_read_only_token(owner, read_only):
    """הצד השני: טוקן קריאה חייב להמשיך לקרוא."""
    await make_project(owner)
    await make_document(owner)

    async with SessionLocal() as session:
        result = await handlers.map_documents(session, read_only)

    assert result["ok"] is True
    assert result["document_count"] == 1


async def test_read_tools_are_blocked_for_a_token_with_no_scopes(monkeypatch):
    """גם כלי קריאה עוברים דרך אכיפה, לא רק כלי כתיבה.

    בלי זה ה-scopes נאכפים לכיוון אחד: טוקן שהוגדר בלי read בכלל
    היה נחסם מכתיבה וקורא הכול.
    """
    called = False

    async def _spy(*args, **kwargs):
        nonlocal called
        called = True
        return {"ok": True}

    class _Ctx:
        headers = {"authorization": "Bearer " + "m" * 40}

    monkeypatch.setattr("app.mcp.auth.get_settings", lambda: _settings_with_scopes(""))

    result = await server._run(_Ctx(), _spy)

    assert result["ok"] is False
    assert result["error"] == "insufficient_scope"
    assert called is False
