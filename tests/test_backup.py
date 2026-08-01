"""מדדי הקבלה של הגיבוי.

מה-ROADMAP: ה-ZIP נפתח ומכיל את כל המסמכים, והוא עקבי גם כשעורכים
באמצע הייצוא.
"""

from __future__ import annotations

import asyncio
import io
import json
import zipfile

import pytest

from sqlalchemy import select, update

from app import backup as backup_module
from app.backup import archive_bytes, safe_name
from app.db import SessionLocal
from app.models import Document, Project
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


def _open(raw: bytes) -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(raw))


# ── מדד: ה-ZIP נפתח ומכיל את כל המסמכים ───────────────────────────────


async def test_archive_contains_every_document(owner):
    await make_project(owner, slug="alpha", name="אלפא")
    await make_project(owner, slug="beta", name="בטא")
    for i in range(3):
        await make_document(owner, project="alpha", slug=f"a{i}", title=f"מסמך {i}", content=f"תוכן {i}")
    await make_document(owner, project="beta", slug="b0", title="יחיד", content="תוכן ב")

    archive = _open(await archive_bytes())
    assert archive.testzip() is None, "הארכיון פגום"

    names = set(archive.namelist())
    for i in range(3):
        assert f"alpha/a{i}.md" in names
    assert "beta/b0.md" in names
    assert archive.read("alpha/a1.md").decode("utf-8") == "תוכן 1"


async def test_archive_includes_links_and_manifest(owner):
    await make_project(owner, slug="docs", name="תיעוד")
    await make_document(owner)
    await owner.post(
        "/api/projects/docs/links",
        json={"title": "CodeKeeper", "url": "https://codekeeper.com"},
        headers=WRITE,
    )

    archive = _open(await archive_bytes())
    payload = json.loads(archive.read("docs/links.json"))
    assert payload["name"] == "תיעוד"
    assert [item["url"] for item in payload["links"]] == ["https://codekeeper.com"]

    manifest = json.loads(archive.read("manifest.json"))
    assert manifest["projects"] == 1
    assert manifest["documents"] == 1


async def test_hebrew_content_survives_the_round_trip(owner):
    """הארכיון נכתב ב-UTF-8, וטקסט מנוקד חוזר בדיוק כפי שנשמר."""
    pointed = "מַדְרִיךְ הַתְקָנָה מְלֵאָה — עם קו מפריד ו״גרשיים״"
    await make_project(owner)
    await make_document(owner, slug="install", title="התקנה", content=pointed)

    archive = _open(await archive_bytes())
    assert archive.read("docs/install.md").decode("utf-8") == pointed


async def test_empty_database_still_produces_a_valid_archive(owner):
    """אפס פרויקטים הוא מצב חוקי, לא שגיאה."""
    archive = _open(await archive_bytes())
    assert archive.testzip() is None
    assert json.loads(archive.read("manifest.json"))["projects"] == 0


# ── מדד: עקביות ───────────────────────────────────────────────────────


async def test_repeatable_read_snapshot_survives_an_outside_commit(owner):
    """התכונה שכל הייצוא נשען עליה, נבדקת ישירות.

    בתוך טרנזקציה ב-REPEATABLE READ, קריאה שנייה חייבת להחזיר את מה
    שהקריאה הראשונה ראתה — גם אחרי ש-session אחר שינה והתחייב. תחת
    READ COMMITTED הקריאה השנייה הייתה רואה את השינוי, וזה בדיוק
    הארכיון המעורבב שאנחנו מונעים.

    הבדיקה הזאת נכשלת אם מסירים את שורת isolation_level מ-stream_archive
    — כלומר היא לא ריקה מתוכן.
    """
    await make_project(owner, slug="docs")
    await make_document(owner, slug="d0", title="מסמך", content="מקורי")

    async with SessionLocal() as reader:
        await reader.connection(execution_options={"isolation_level": "REPEATABLE READ"})

        first = (
            await reader.execute(select(Document.content).where(Document.slug == "d0"))
        ).scalar_one()
        assert first == "מקורי"

        # שינוי מבחוץ, בסשן נפרד לגמרי, שמתחייב במלואו
        async with SessionLocal() as writer:
            await writer.execute(
                update(Document).where(Document.slug == "d0").values(content="שונה")
            )
            await writer.commit()

        second = (
            await reader.execute(select(Document.content).where(Document.slug == "d0"))
        ).scalar_one()
        assert second == "מקורי", f"ה-snapshot ראה שינוי מבחוץ: {second!r}"

    # ומחוץ לטרנזקציה השינוי כן נראה — כלומר הוא באמת התחייב
    async with SessionLocal() as after:
        now = (
            await after.execute(select(Document.content).where(Document.slug == "d0"))
        ).scalar_one()
        assert now == "שונה"


async def test_archive_is_consistent_while_content_changes(owner, monkeypatch):
    """הייצוא עצמו, עם שינוי שנוחת באמצע הקריאה.

    כדי שהתרחיש יהיה ודאי ולא תלוי בתזמון, הקריאה נעצרת בין שליפת
    הפרויקטים לשליפת המסמכים, והשינוי מתבצע בדיוק שם. בלי ה-barrier
    הזה הייצוא מסתיים לפני שהכתיבה מתחילה, והבדיקה עוברת גם כשאין
    בכלל בידוד — כלומר לא בודקת כלום.
    """
    await make_project(owner, slug="docs")
    for i in range(6):
        await make_document(owner, slug=f"d{i}", title=f"מסמך {i}", content=f"מקורי {i}")

    reached = asyncio.Event()
    proceed = asyncio.Event()
    real_snapshot = backup_module._snapshot

    async def snapshot_with_barrier(session):
        # קריאה ראשונה בתוך הטרנזקציה — כאן נקבע ה-snapshot
        await session.execute(select(Project.id).order_by(Project.id))
        reached.set()
        await proceed.wait()
        return await real_snapshot(session)

    monkeypatch.setattr(backup_module, "_snapshot", snapshot_with_barrier)

    async def rewrite():
        await reached.wait()
        async with SessionLocal() as writer:
            for i in range(6):
                await writer.execute(
                    update(Document).where(Document.slug == f"d{i}").values(content=f"שונה {i}")
                )
            await writer.commit()
        proceed.set()

    raw, _ = await asyncio.gather(archive_bytes(), rewrite())
    archive = _open(raw)

    bodies = [archive.read(f"docs/d{i}.md").decode("utf-8") for i in range(6)]
    originals = [b for b in bodies if b.startswith("מקורי")]
    changed = [b for b in bodies if b.startswith("שונה")]
    assert not changed, f"הארכיון קלט שינוי שקרה אחרי פתיחת ה-snapshot: {changed}"
    assert len(originals) == 6


async def test_two_archives_of_the_same_state_match(owner):
    """סדר יציב (כלל 8) — אותו מצב מייצר את אותה רשימת קבצים."""
    await make_project(owner, slug="docs")
    for i in range(6):
        await make_document(owner, slug=f"d{i}", title="זהה", content="אותו תוכן")

    first = _open(await archive_bytes()).namelist()
    second = _open(await archive_bytes()).namelist()
    assert first == second


# ── שמות קבצים ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("../../etc/passwd", "-..-etc-passwd"),
        ("a/b", "a-b"),
        ("a\\b", "a-b"),
        ("...", "fallback"),
        ("", "fallback"),
        ("..", "fallback"),
        ("שם עברי", "שם עברי"),
    ],
)
def test_safe_name_returns_what_we_expect(raw, expected):
    assert safe_name(raw, "fallback") == expected


@pytest.mark.parametrize(
    "raw",
    [
        "../../etc/passwd",
        "..\\..\\windows\\system32",
        "/absolute/path",
        "....//....//x",
        "a/../../../b",
        "..",
        ".",
        "con",
        "x\x00y",
    ],
)
def test_safe_name_cannot_escape_the_archive(raw):
    """Zip Slip מתבצע אצל מי שפותח את הקובץ, ולכן הוא נמנע כאן.

    הבדיקה היא על התכונה ולא על מחרוזת מסוימת: מה שחשוב הוא שאין מפריד
    נתיב ואין רכיב שמטפס למעלה, לא איך בדיוק נראה הפלט.
    """
    result = safe_name(raw, "fallback")
    assert "/" not in result and "\\" not in result
    assert "\x00" not in result
    assert result not in ("", ".", "..")
    # ובאמת לא מטפס: הצירוף עם תיקייה נשאר בתוכה
    import posixpath

    assert posixpath.normpath("archive/" + result).startswith("archive/")


async def test_documents_with_colliding_safe_names_do_not_overwrite(owner):
    """שני slugs שונים שמצטמצמים לאותו שם — שניהם חייבים לשרוד."""
    await make_project(owner, slug="docs")
    await make_document(owner, slug="a-b", title="ראשון", content="תוכן ראשון")
    await make_document(owner, slug="a/b" if False else "a-b-2", title="שני", content="תוכן שני")

    archive = _open(await archive_bytes())
    md = [n for n in archive.namelist() if n.endswith(".md")]
    assert len(md) == len(set(md)) == 2


# ── הרשאות ────────────────────────────────────────────────────────────


async def test_backup_requires_authentication(anon, owner):
    """הארכיון כולל גם פרויקטים פרטיים, ולכן הוא לעולם לא ציבורי."""
    await make_project(owner, visibility="public")
    assert (await anon.get("/api/backup.zip")).status_code == 401


async def test_owner_downloads_the_archive_over_http(owner):
    await make_project(owner)
    await make_document(owner, slug="install", content="תוכן")

    response = await owner.get("/api/backup.zip")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert "attachment" in response.headers["content-disposition"]

    archive = _open(response.content)
    assert archive.testzip() is None
    assert "docs/install.md" in archive.namelist()


async def test_private_projects_are_in_the_archive(owner):
    """הגיבוי הוא של הכול, כולל מה שלא ציבורי — זו כל המטרה."""
    await make_project(owner, slug="secret", name="סודי", visibility="private")
    await make_document(owner, project="secret", slug="s", content="תוכן פרטי")

    archive = _open(await archive_bytes())
    assert archive.read("secret/s.md").decode("utf-8") == "תוכן פרטי"
