"""מדדי הקבלה של שלב 3.

מה-ROADMAP: יצירת פרויקט, כתיבת מסמך, עריכה, וראייה שהגרסה הקודמת
נשמרה. פרויקט ציבורי נקרא בלי cookie; פרטי מחזיר 404.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.seed import seed_admin
from app.security import COOKIE_NAME

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


# ── מדד: המחזור המלא, כולל שמירת הגרסה הקודמת ─────────────────────────


async def test_full_cycle_keeps_the_previous_version(owner):
    await make_project(owner)
    await make_document(owner, content="גרסה ראשונה")

    updated = await owner.put(
        "/api/projects/docs/docs/installation",
        json={"content": "גרסה שנייה"},
        headers=WRITE,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["applied"] is True
    assert updated.json()["document"]["content"] == "גרסה שנייה"

    versions = await owner.get("/api/projects/docs/docs/installation/versions")
    assert versions.status_code == 200
    assert len(versions.json()) == 1, "הגרסה הקודמת לא נשמרה"
    assert versions.json()[0]["size_bytes"] == len("גרסה ראשונה".encode("utf-8"))


async def test_identical_content_does_not_create_a_version(owner):
    """שמירה אוטומטית שלא שינתה כלום לא צריכה לייצר היסטוריה."""
    await make_project(owner)
    await make_document(owner, content="אותו תוכן")
    for _ in range(3):
        await owner.put(
            "/api/projects/docs/docs/installation", json={"content": "אותו תוכן"}, headers=WRITE
        )
    versions = await owner.get("/api/projects/docs/docs/installation/versions")
    assert versions.json() == []


async def test_version_history_is_capped(owner):
    """היסטוריה לא גדלה בלי גבול."""
    await make_project(owner)
    await make_document(owner, content="0")
    keep = get_settings().document_versions_kept
    for i in range(1, keep + 12):
        await owner.put(
            "/api/projects/docs/docs/installation", json={"content": str(i)}, headers=WRITE
        )
    versions = await owner.get("/api/projects/docs/docs/installation/versions")
    assert len(versions.json()) == keep, f"נשמרו {len(versions.json())} גרסאות במקום {keep}"


# ── מדד: פומבי נקרא בלי cookie, פרטי מחזיר 404 ────────────────────────


async def test_private_project_is_404_for_anonymous(anon, owner):
    await make_project(owner, visibility="private")
    assert (await anon.get("/api/projects/docs")).status_code == 404


async def test_public_project_is_readable_without_a_cookie(anon, owner):
    await make_project(owner, visibility="public")
    await make_document(owner)
    response = await anon.get("/api/projects/docs")
    assert response.status_code == 200
    assert response.json()["name"] == "התיעוד"
    assert [d["slug"] for d in response.json()["documents"]] == ["installation"]


async def test_missing_and_private_are_indistinguishable(anon, owner):
    """אותה תשובה בדיוק — אחרת אפשר למפות אילו פרויקטים קיימים."""
    await make_project(owner, slug="secret", visibility="private")
    hidden = await anon.get("/api/projects/secret")
    absent = await anon.get("/api/projects/does-not-exist")
    assert hidden.status_code == absent.status_code == 404
    assert hidden.json() == absent.json()


async def test_listing_hides_private_projects(anon, owner):
    await make_project(owner, slug="open", name="פתוח", visibility="public")
    await make_project(owner, slug="closed", name="סגור", visibility="private")

    assert [p["slug"] for p in (await anon.get("/api/projects")).json()] == ["open"]
    assert {p["slug"] for p in (await owner.get("/api/projects")).json()} == {"open", "closed"}


async def test_public_response_does_not_leak_owner_fields(anon, owner):
    await make_project(owner, visibility="public")
    body = (await anon.get("/api/projects/docs")).json()
    for leaked in ("owner_id", "id", "visibility", "created_at", "updated_at"):
        assert leaked not in body, f"התשובה הציבורית חשפה {leaked}"


async def test_document_in_private_project_is_404_for_anonymous(anon, owner):
    await make_project(owner, visibility="private")
    await make_document(owner)
    assert (await anon.get("/api/projects/docs/docs/installation")).status_code == 404


async def test_anonymous_cannot_write(anon, owner):
    await make_project(owner, visibility="public")
    response = await anon.post(
        "/api/projects/docs/docs", json={"title": "חדש"}, headers=WRITE
    )
    assert response.status_code == 401


# ── slug ──────────────────────────────────────────────────────────────


async def test_hebrew_slug_is_derived_from_the_name(owner):
    response = await owner.post("/api/projects", json={"name": "ספריית התיעוד"}, headers=WRITE)
    assert response.status_code == 201
    assert response.json()["slug"] == "ספריית-התיעוד"
    assert (await owner.get("/api/projects/ספריית-התיעוד")).status_code == 200


async def test_duplicate_project_slug_is_409(owner):
    await make_project(owner)
    response = await owner.post(
        "/api/projects", json={"name": "אחר", "slug": "docs"}, headers=WRITE
    )
    assert response.status_code == 409


async def test_same_document_slug_in_two_projects_is_allowed(owner):
    await make_project(owner, slug="one", name="ראשון")
    await make_project(owner, slug="two", name="שני")
    await make_document(owner, project="one")
    await make_document(owner, project="two")


async def test_duplicate_document_slug_in_one_project_is_409(owner):
    await make_project(owner)
    await make_document(owner)
    response = await owner.post(
        "/api/projects/docs/docs", json={"title": "שוב", "slug": "installation"}, headers=WRITE
    )
    assert response.status_code == 409


async def test_unusable_slug_is_422(owner):
    response = await owner.post("/api/projects", json={"name": "!!!"}, headers=WRITE)
    assert response.status_code == 422


# ── סדר כתיבות ────────────────────────────────────────────────────────


async def test_stale_write_is_rejected_without_an_error(owner):
    """שמירה שנתקעה ברשת ונחתה מאוחר לא מחזירה את המסמך אחורה."""
    await make_project(owner)
    await make_document(owner, content="התחלה")

    fresh = await owner.put(
        "/api/projects/docs/docs/installation",
        json={"content": "חדש", "client_seq": 10, "editor_id": "tab-a"},
        headers=WRITE,
    )
    assert fresh.json()["applied"] is True

    stale = await owner.put(
        "/api/projects/docs/docs/installation",
        json={"content": "ישן שאיחר", "client_seq": 7, "editor_id": "tab-a"},
        headers=WRITE,
    )
    assert stale.status_code == 200, "בקשה ישנה החזירה שגיאה במקום להידחות בשקט"
    assert stale.json()["applied"] is False
    assert stale.json()["document"]["content"] == "חדש", "המסמך חזר אחורה בזמן"


async def test_equal_sequence_is_also_rejected(owner):
    await make_project(owner)
    await make_document(owner)
    await owner.put(
        "/api/projects/docs/docs/installation", json={"content": "א", "client_seq": 5, "editor_id": "t"}, headers=WRITE
    )
    repeat = await owner.put(
        "/api/projects/docs/docs/installation", json={"content": "ב", "client_seq": 5, "editor_id": "t"}, headers=WRITE
    )
    assert repeat.json()["applied"] is False


async def test_writes_without_a_sequence_still_apply(owner):
    """לקוח שלא שולח מונה חוזר לכלל 'האחרון לפי סדר ההגעה'."""
    await make_project(owner)
    await make_document(owner)
    await owner.put(
        "/api/projects/docs/docs/installation", json={"content": "א", "client_seq": 9, "editor_id": "t"}, headers=WRITE
    )
    plain = await owner.put(
        "/api/projects/docs/docs/installation", json={"content": "ב"}, headers=WRITE
    )
    assert plain.json()["applied"] is True
    assert plain.json()["document"]["content"] == "ב"


# ── מיון ──────────────────────────────────────────────────────────────


async def test_documents_are_ordered_by_position_then_id(owner):
    """כשכל ה-position שווים, ה-id הוא מה שקובע.

    הבדיקה הקודמת כאן השוותה את התשובה לעצמה ממוינת לפי position. מיון
    של פייתון יציב, ולכן מיון רשימה שכבר מסודרת מחזיר אותה רשימה —
    הטענה הייתה נכונה תמיד, גם אילו ה-tiebreaker היה מוסר. הסדר הצפוי
    נגזר כאן מה-id בפועל, שלא נחשף ב-API ולכן נשלף מבסיס הנתונים.
    """
    await make_project(owner)
    for slug in ("a", "b", "c"):
        await make_document(owner, slug=slug, title=slug.upper())

    for slug in ("a", "b", "c"):
        await owner.put(f"/api/projects/docs/docs/{slug}", json={"position": 0}, headers=WRITE)

    async with SessionLocal() as session:
        expected = [
            row[0]
            for row in (
                await session.execute(text("SELECT slug FROM documents ORDER BY position, id"))
            ).all()
        ]

    reads = [
        [d["slug"] for d in (await owner.get("/api/projects/docs")).json()["documents"]]
        for _ in range(4)
    ]
    assert sorted(reads[0]) == ["a", "b", "c"], "לא כל המסמכים חזרו"
    assert all(order == expected for order in reads), (
        f"הסדר לא תואם למיון לפי (position, id). צפוי {expected}, התקבל {reads}"
    )


async def test_new_documents_go_to_the_end(owner):
    await make_project(owner)
    for slug in ("first", "second", "third"):
        await make_document(owner, slug=slug, title=slug)
    order = [d["slug"] for d in (await owner.get("/api/projects/docs")).json()["documents"]]
    assert order == ["first", "second", "third"]


# ── ולידציה מספרית (כלל 4) ────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity"])
async def test_nan_and_infinity_are_rejected_for_position(owner, bad):
    """NaN עובר כל בדיקת טווח, כי כל השוואה איתו מחזירה False."""
    await make_project(owner)
    await make_document(owner)
    response = await owner.put(
        "/api/projects/docs/docs/installation",
        content=f'{{"position": {bad}}}'.encode(),
        headers={**WRITE, "Content-Type": "application/json"},
    )
    assert response.status_code == 422, f"{bad} התקבל כ-position"


async def test_negative_position_is_rejected(owner):
    await make_project(owner)
    await make_document(owner)
    response = await owner.put(
        "/api/projects/docs/docs/installation", json={"position": -1}, headers=WRITE
    )
    assert response.status_code == 422


# ── מחיקה ─────────────────────────────────────────────────────────────


async def test_deleting_a_project_removes_its_documents_and_versions(owner):
    await make_project(owner)
    await make_document(owner, content="א")
    await owner.put("/api/projects/docs/docs/installation", json={"content": "ב"}, headers=WRITE)

    assert (await owner.delete("/api/projects/docs", headers=WRITE)).status_code == 204

    async with SessionLocal() as session:
        docs = (await session.execute(text("SELECT count(*) FROM documents"))).scalar_one()
        versions = (await session.execute(text("SELECT count(*) FROM document_versions"))).scalar_one()
    assert (docs, versions) == (0, 0), "נשארו יתומים אחרי מחיקת הפרויקט"


async def test_slug_is_immutable(owner):
    """שינוי slug שובר כל קישור שנשלח — ולכן הוא פשוט לא נתמך."""
    await make_project(owner)
    patched = await owner.patch("/api/projects/docs", json={"slug": "renamed"}, headers=WRITE)
    assert patched.status_code == 200, patched.text
    assert (await owner.get("/api/projects/docs")).status_code == 200
    assert (await owner.get("/api/projects/renamed")).status_code == 404


async def test_patch_that_really_changes_something_returns_200(owner):
    """PATCH ששינה שדה — ולכן הריץ UPDATE אמיתי.

    test_slug_is_immutable שולח רק slug, שמתעלמים ממנו, ולכן אף פעם לא
    התבצע UPDATE ו-updated_at לא פג. רק כשמשנים שדה אמיתי מתגלה
    ש-onupdate מפקיע את העמודה, וקריאה שלה אחרי commit היא MissingGreenlet
    (כלל 5). הבדיקה הזאת נכשלת ב-500 אם חוזרים לגעת ב-project המקורי.
    """
    await make_project(owner)
    before = (await owner.get("/api/projects/docs")).json()

    patched = await owner.patch(
        "/api/projects/docs",
        json={"visibility": "public", "name": "שם מעודכן"},
        headers=WRITE,
    )
    assert patched.status_code == 200, patched.text

    body = patched.json()
    assert body["visibility"] == "public"
    assert body["name"] == "שם מעודכן"
    assert body["updated_at"] >= before["updated_at"]

    # ומה שחזר הוא מה שנשמר, לא רק מה שהוחזק בזיכרון.
    assert (await owner.get("/api/projects/docs")).json()["name"] == "שם מעודכן"


# ── סדר כתיבות משויך לעורך ────────────────────────────────────────────


async def test_a_second_editor_is_not_locked_out(owner):
    """הבאג שמונה גלובלי למסמך היה יוצר.

    טאב א' הגיע למונה 50. טאב ב' נטען מחדש והתחיל מ-1. עם מונה גלובלי
    למסמך, אף כתיבה של ב' לא הייתה מתקבלת לעולם — הוא היה נראה כאילו
    "נתקע" בלי שום הודעת שגיאה.
    """
    await make_project(owner)
    await make_document(owner)

    for seq in (10, 20, 50):
        applied = await owner.put(
            "/api/projects/docs/docs/installation",
            json={"content": f"א{seq}", "client_seq": seq, "editor_id": "tab-a"},
            headers=WRITE,
        )
        assert applied.json()["applied"] is True

    second = await owner.put(
        "/api/projects/docs/docs/installation",
        json={"content": "מטאב ב", "client_seq": 1, "editor_id": "tab-b"},
        headers=WRITE,
    )
    assert second.json()["applied"] is True, "הטאב השני ננעל החוצה"
    assert second.json()["document"]["content"] == "מטאב ב"


async def test_stale_write_from_the_same_editor_is_still_rejected(owner):
    await make_project(owner)
    await make_document(owner)
    await owner.put(
        "/api/projects/docs/docs/installation",
        json={"content": "חדש", "client_seq": 10, "editor_id": "tab-a"},
        headers=WRITE,
    )
    stale = await owner.put(
        "/api/projects/docs/docs/installation",
        json={"content": "ישן", "client_seq": 7, "editor_id": "tab-a"},
        headers=WRITE,
    )
    assert stale.json()["applied"] is False
    assert stale.json()["document"]["content"] == "חדש"


async def test_sequence_without_an_editor_id_is_422(owner):
    await make_project(owner)
    await make_document(owner)
    response = await owner.put(
        "/api/projects/docs/docs/installation", json={"client_seq": 3}, headers=WRITE
    )
    assert response.status_code == 422


# ── חשיפת קבצים ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    ["/app/config.py", "/app/security.py", "/alembic.ini", "/.env.example", "/requirements.txt", "/CLAUDE.md"],
)
async def test_repository_files_are_not_served(anon, path):
    """STATIC_ROOT='.' עם mount על '/' הפך כל קובץ בפרויקט לנגיש."""
    assert (await anon.get(path)).status_code == 404, f"{path} עדיין מוגש"


async def test_the_front_end_is_still_served(anon):
    assert (await anon.get("/")).status_code == 200
    assert (await anon.get("/assets/support.js")).status_code == 200
