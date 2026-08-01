"""המעגל הסגור: יצירה → גיבוי → מחיקה → שחזור → השוואה.

זו הבדיקה היחידה שמוכיחה שהגיבוי שווה משהו. ארכיון שנפתח יפה ואי אפשר
לשחזר ממנו הוא ארכיון, לא גיבוי.

הסקריפט עצמו מדבר HTTP עם שרת חי, ולכן כאן נבדקת הלוגיקה שלו מול אותו
API דרך ה-ASGI client — אותם מסלולים, בלי להרים תהליך.
"""

from __future__ import annotations

import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

from app.backup import archive_bytes
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

# הסקריפט אינו חבילה, ולכן נטען לפי נתיב.
_spec = importlib.util.spec_from_file_location(
    "restore_script", Path(__file__).resolve().parent.parent / "scripts" / "restore.py"
)
restore_script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(restore_script)


class AsgiClient:
    """מתרגם את ממשק ה-Client של הסקריפט ל-httpx האסינכרוני של הבדיקות.

    הסקריפט סינכרוני; הבדיקות אסינכרוניות. במקום להרים שרת, כל קריאה
    מנותבת לאותו client שכל שאר הבדיקות משתמשות בו — כלומר מה שנבדק
    הוא הלוגיקה של השחזור, ולא httpx.
    """

    def __init__(self, client) -> None:
        self.client = client
        self.base = ""
        self.calls: list[tuple[str, str]] = []

    async def get(self, path):
        self.calls.append(("GET", path))
        return await self.client.get(path)

    async def post(self, path, payload):
        self.calls.append(("POST", path))
        return await self.client.post(path, json=payload, headers=WRITE)

    async def put(self, path, payload):
        self.calls.append(("PUT", path))
        return await self.client.put(path, json=payload, headers=WRITE)

    async def patch(self, path, payload):
        self.calls.append(("PATCH", path))
        return await self.client.patch(path, json=payload, headers=WRITE)

    async def delete(self, path):
        self.calls.append(("DELETE", path))
        return await self.client.delete(path, headers=WRITE)


async def _restore(client, data, mode):
    """קורא לפונקציה מהסקריפט עצמה, בלי גרסה שנייה של הלוגיקה."""
    return await restore_script.restore(client, data, mode)


async def _snapshot(client) -> dict:
    """מצב המערכת, במבנה שאפשר להשוות."""
    projects = (await client.get("/api/projects")).json()
    out = {}
    for project in projects:
        body = (await client.get(f"/api/projects/{project['slug']}")).json()
        out[body["slug"]] = {
            "name": body["name"],
            "visibility": body["visibility"],
            "documents": {d["slug"]: d["title"] for d in body["documents"]},
            "links": sorted(item["url"] for item in body.get("links", [])),
        }
    return out


def _read(raw: bytes) -> dict:
    import io

    archive = zipfile.ZipFile(io.BytesIO(raw))
    names = archive.namelist()
    manifest = json.loads(archive.read("manifest.json"))
    projects: dict[str, dict] = {}
    for name in names:
        if "/" not in name:
            continue
        folder, _, leaf = name.partition("/")
        entry = projects.setdefault(folder, {"meta": None, "documents": []})
        if leaf == "links.json":
            entry["meta"] = json.loads(archive.read(name))
        elif leaf.endswith(".md"):
            entry["documents"].append(
                {"slug": leaf[:-3], "content": archive.read(name).decode("utf-8")}
            )
    for entry in projects.values():
        entry["documents"].sort(key=lambda d: d["slug"])
    return {"manifest": manifest, "projects": projects}


# ── המעגל הסגור ───────────────────────────────────────────────────────


async def test_full_round_trip(owner):
    """יוצרים, מגבים, מוחקים הכול, משחזרים — וההשוואה חייבת להיות זהה."""
    await make_project(owner, slug="alpha", name="אלפא", visibility="public")
    await make_project(owner, slug="beta", name="בטא")
    await make_document(owner, project="alpha", slug="install", title="התקנה", content="# התקנה\n\nמדריך.\n")
    await make_document(owner, project="alpha", slug="usage", title="שימוש", content="# שימוש\n\nהסבר.\n")
    await make_document(owner, project="beta", slug="notes", title="הערות", content="# הערות\n\nטקסט.\n")
    await owner.post(
        "/api/projects/alpha/links",
        json={"title": "אתר", "url": "https://example.com"},
        headers=WRITE,
    )

    before = await _snapshot(owner)
    raw = await archive_bytes()

    # מוחקים הכול — זה הרגע שהגיבוי קיים בשבילו
    for slug in list(before):
        assert (await owner.delete(f"/api/projects/{slug}", headers=WRITE)).status_code == 204
    assert (await owner.get("/api/projects")).json() == []

    data = _read(raw)
    projects, documents, warnings = await _restore(AsgiClient(owner), data, "skip")
    assert projects == 2, warnings
    assert documents == 3, warnings

    after = await _snapshot(owner)
    assert set(after) == set(before), f"פרויקטים שונים: {set(before)} מול {set(after)}"
    for slug in before:
        assert after[slug]["name"] == before[slug]["name"]
        assert after[slug]["visibility"] == before[slug]["visibility"], f"{slug}: visibility"
        assert after[slug]["documents"] == before[slug]["documents"], f"{slug}: מסמכים"
        assert after[slug]["links"] == before[slug]["links"], f"{slug}: קישורים"


async def test_document_content_is_identical_after_restore(owner):
    """לא רק הכותרות — הבתים עצמם."""
    pointed = "# הַתְקָנָה\n\nטקסט מנוקד, עם ״גרשיים״ ו־מקף עברי.\n\n```\nבלוק קוד\n```\n"
    await make_project(owner, slug="docs")
    await make_document(owner, slug="install", title="התקנה", content=pointed)

    raw = await archive_bytes()
    await owner.delete("/api/projects/docs", headers=WRITE)

    await _restore(AsgiClient(owner), _read(raw), "skip")

    body = (await owner.get("/api/projects/docs/docs/install")).json()
    assert body["content"] == pointed


# ── שלושת המצבים ──────────────────────────────────────────────────────


async def test_skip_does_not_touch_existing_content(owner):
    """skip הוא המצב שלא יכול להזיק."""
    await make_project(owner, slug="docs", name="תיעוד")
    await make_document(owner, slug="install", title="התקנה", content="גרסה ישנה")
    raw = await archive_bytes()

    await owner.put(
        "/api/projects/docs/docs/install",
        json={"content": "גרסה חדשה שנכתבה אחרי הגיבוי", "client_seq": 5, "editor_id": "later"},
        headers=WRITE,
    )

    await _restore(AsgiClient(owner), _read(raw), "skip")

    body = (await owner.get("/api/projects/docs/docs/install")).json()
    assert body["content"] == "גרסה חדשה שנכתבה אחרי הגיבוי", "skip דרס תוכן קיים"


async def test_upsert_overwrites_but_keeps_newer_documents(owner):
    """upsert לשחזור נקודתי: דורס מה שבגיבוי, משאיר מה שנוסף אחריו."""
    await make_project(owner, slug="docs", name="תיעוד")
    await make_document(owner, slug="install", title="התקנה", content="מקורי")
    raw = await archive_bytes()

    await owner.put(
        "/api/projects/docs/docs/install",
        json={"content": "השתנה", "client_seq": 5, "editor_id": "later"},
        headers=WRITE,
    )
    await make_document(owner, slug="new-one", title="חדש", content="נוצר אחרי הגיבוי")

    await _restore(AsgiClient(owner), _read(raw), "upsert")

    restored = (await owner.get("/api/projects/docs/docs/install")).json()
    assert restored["content"] == "מקורי", "upsert לא דרס את מה שבגיבוי"

    kept = await owner.get("/api/projects/docs/docs/new-one")
    assert kept.status_code == 200, "upsert מחק מסמך שנוצר אחרי הגיבוי"


async def test_replace_rebuilds_the_project_exactly(owner):
    """replace מוחק את מה שנוצר אחרי הגיבוי. זו המטרה, וזה גם הסיכון."""
    await make_project(owner, slug="docs", name="תיעוד")
    await make_document(owner, slug="install", title="התקנה", content="מקורי")
    raw = await archive_bytes()

    await make_document(owner, slug="new-one", title="חדש", content="נוצר אחרי הגיבוי")

    await _restore(AsgiClient(owner), _read(raw), "replace")

    body = (await owner.get("/api/projects/docs")).json()
    slugs = {d["slug"] for d in body["documents"]}
    assert slugs == {"install"}, f"replace לא החזיר את המצב מהגיבוי: {slugs}"


# ── פרטים שקל לפספס ───────────────────────────────────────────────────


async def test_visibility_is_restored(owner):
    """פרויקט שהיה ציבורי חייב לחזור ציבורי.

    זה לא נשמר ביצירת הפרויקט אלא ב-PATCH נפרד, ולכן קל לשכוח — ואז
    השחזור מצליח לכאורה, אבל קישור שנשלח למישהו מפסיק לעבוד.
    """
    await make_project(owner, slug="open", name="פתוח", visibility="public")
    await make_document(owner, project="open", slug="a", content="תוכן")
    raw = await archive_bytes()
    await owner.delete("/api/projects/open", headers=WRITE)

    await _restore(AsgiClient(owner), _read(raw), "skip")

    body = (await owner.get("/api/projects/open")).json()
    assert body["visibility"] == "public"


async def test_links_are_restored(owner):
    await make_project(owner, slug="docs")
    await make_document(owner)
    for i in range(3):
        await owner.post(
            "/api/projects/docs/links",
            json={"title": f"קישור {i}", "url": f"https://x{i}.example"},
            headers=WRITE,
        )
    raw = await archive_bytes()
    await owner.delete("/api/projects/docs", headers=WRITE)

    await _restore(AsgiClient(owner), _read(raw), "skip")

    links = (await owner.get("/api/projects/docs/links")).json()
    assert sorted(item["url"] for item in links) == [f"https://x{i}.example" for i in range(3)]


async def test_restoring_twice_does_not_duplicate(owner):
    """הרצה חוזרת אחרי שחזור שנקטע לא מייצרת כפילויות."""
    await make_project(owner, slug="docs")
    await make_document(owner, slug="a", content="תוכן")
    raw = await archive_bytes()
    await owner.delete("/api/projects/docs", headers=WRITE)

    data = _read(raw)
    await _restore(AsgiClient(owner), data, "skip")
    await _restore(AsgiClient(owner), data, "skip")

    projects = (await owner.get("/api/projects")).json()
    assert len(projects) == 1
    body = (await owner.get("/api/projects/docs")).json()
    assert len(body["documents"]) == 1


async def test_title_is_taken_from_the_heading():
    doc = {"slug": "install", "content": "# מדריך התקנה\n\nתוכן.\n"}
    assert restore_script.title_of(doc) == "מדריך התקנה"


async def test_title_falls_back_to_the_slug():
    doc = {"slug": "install", "content": "בלי כותרת ראשית\n"}
    assert restore_script.title_of(doc) == "install"


def test_modes_are_ordered_from_safe_to_destructive():
    """ברירת המחדל היא הבטוחה ביותר."""
    assert restore_script.MODES[0] == "skip"
    assert restore_script.MODES[-1] == "replace"
