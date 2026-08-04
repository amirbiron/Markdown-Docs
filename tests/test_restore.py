"""המעגל הסגור: יצירה → גיבוי → מחיקה → שחזור → השוואה.

זו הבדיקה היחידה שמוכיחה שהגיבוי שווה משהו. ארכיון שנפתח יפה ואי אפשר
לשחזר ממנו הוא ארכיון, לא גיבוי.

הסקריפט עצמו מדבר HTTP עם שרת חי, ולכן כאן נבדקת הלוגיקה שלו מול אותו
API דרך ה-ASGI client — אותם מסלולים, בלי להרים תהליך.
"""

from __future__ import annotations

import importlib.util
import io
import itertools
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
    """מנתב את קריאות הסקריפט ל-ASGI client של הבדיקות.

    אותה חתימה שיש ל-Client האמיתי, בתוספת כותרת ה-Origin שה-OriginGuard
    דורש. כך restore() עצמה נבדקת — לא עותק שלה — בלי להרים שרת.
    calls נאסף כדי שאפשר יהיה לטעון על מה *לא* נשלח, למשל ש-skip אינו
    מבצע PUT.
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


async def _restore(client, data, mode, editor_id=None):
    """קורא לפונקציה מהסקריפט עצמה, בלי גרסה שנייה של הלוגיקה."""
    return await restore_script.restore(client, data, mode, editor_id=editor_id)


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


@pytest.fixture
def read(tmp_path):
    """קורא ארכיון דרך הפונקציה של הסקריפט עצמו.

    היה כאן העתק של לוגיקת הפרסור, וזה בדיוק סוג הכפילות שמתפצלת
    בשקט — הבדיקה הייתה ממשיכה לעבור גם אם הפרסור האמיתי נשבר.
    """
    counter = itertools.count()

    def _read(raw: bytes) -> dict:
        path = tmp_path / f"archive-{next(counter)}.zip"
        path.write_bytes(raw)
        return restore_script.read_archive(path, decrypt=False)

    return _read


# ── המעגל הסגור ───────────────────────────────────────────────────────


async def test_full_round_trip(owner, read):
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

    data = read(raw)
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


async def test_document_content_is_identical_after_restore(owner, read):
    """לא רק הכותרות — הבתים עצמם."""
    pointed = "# הַתְקָנָה\n\nטקסט מנוקד, עם ״גרשיים״ ו־מקף עברי.\n\n```\nבלוק קוד\n```\n"
    await make_project(owner, slug="docs")
    await make_document(owner, slug="install", title="התקנה", content=pointed)

    raw = await archive_bytes()
    await owner.delete("/api/projects/docs", headers=WRITE)

    await _restore(AsgiClient(owner), read(raw), "skip")

    body = (await owner.get("/api/projects/docs/docs/install")).json()
    assert body["content"] == pointed


# ── שלושת המצבים ──────────────────────────────────────────────────────


async def test_skip_does_not_touch_existing_content(owner, read):
    """skip הוא המצב שלא יכול להזיק."""
    await make_project(owner, slug="docs", name="תיעוד")
    await make_document(owner, slug="install", title="התקנה", content="גרסה ישנה")
    raw = await archive_bytes()

    await owner.put(
        "/api/projects/docs/docs/install",
        json={"content": "גרסה חדשה שנכתבה אחרי הגיבוי", "client_seq": 5, "editor_id": "later"},
        headers=WRITE,
    )

    await _restore(AsgiClient(owner), read(raw), "skip")

    body = (await owner.get("/api/projects/docs/docs/install")).json()
    assert body["content"] == "גרסה חדשה שנכתבה אחרי הגיבוי", "skip דרס תוכן קיים"


async def test_upsert_overwrites_but_keeps_newer_documents(owner, read):
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

    await _restore(AsgiClient(owner), read(raw), "upsert")

    restored = (await owner.get("/api/projects/docs/docs/install")).json()
    assert restored["content"] == "מקורי", "upsert לא דרס את מה שבגיבוי"

    kept = await owner.get("/api/projects/docs/docs/new-one")
    assert kept.status_code == 200, "upsert מחק מסמך שנוצר אחרי הגיבוי"


async def test_replace_rebuilds_the_project_exactly(owner, read):
    """replace מוחק את מה שנוצר אחרי הגיבוי. זו המטרה, וזה גם הסיכון."""
    await make_project(owner, slug="docs", name="תיעוד")
    await make_document(owner, slug="install", title="התקנה", content="מקורי")
    raw = await archive_bytes()

    await make_document(owner, slug="new-one", title="חדש", content="נוצר אחרי הגיבוי")

    await _restore(AsgiClient(owner), read(raw), "replace")

    body = (await owner.get("/api/projects/docs")).json()
    slugs = {d["slug"] for d in body["documents"]}
    assert slugs == {"install"}, f"replace לא החזיר את המצב מהגיבוי: {slugs}"


# ── פרטים שקל לפספס ───────────────────────────────────────────────────


async def test_visibility_is_restored(owner, read):
    """פרויקט שהיה ציבורי חייב לחזור ציבורי.

    זה לא נשמר ביצירת הפרויקט אלא ב-PATCH נפרד, ולכן קל לשכוח — ואז
    השחזור מצליח לכאורה, אבל קישור שנשלח למישהו מפסיק לעבוד.
    """
    await make_project(owner, slug="open", name="פתוח", visibility="public")
    await make_document(owner, project="open", slug="a", content="תוכן")
    raw = await archive_bytes()
    await owner.delete("/api/projects/open", headers=WRITE)

    await _restore(AsgiClient(owner), read(raw), "skip")

    body = (await owner.get("/api/projects/open")).json()
    assert body["visibility"] == "public"


async def test_links_are_restored(owner, read):
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

    await _restore(AsgiClient(owner), read(raw), "skip")

    links = (await owner.get("/api/projects/docs/links")).json()
    assert sorted(item["url"] for item in links) == [f"https://x{i}.example" for i in range(3)]


async def test_restoring_twice_does_not_duplicate(owner, read):
    """הרצה חוזרת אחרי שחזור שנקטע לא מייצרת כפילויות."""
    await make_project(owner, slug="docs")
    await make_document(owner, slug="a", content="תוכן")
    raw = await archive_bytes()
    await owner.delete("/api/projects/docs", headers=WRITE)

    data = read(raw)
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


# ── מה שהתגלה בביקורת ─────────────────────────────────────────────────


async def test_restoring_the_same_document_twice_is_not_reported_as_success(owner, read):
    """200 אינו "נכתב".

    השרת דוחה כתיבה שאינה מתקדמת *מאותו עורך* ומחזיר 200 עם
    applied=false. עם editor_id קבוע, שחזור שני של אותו מסמך היה נדחה
    בשקט ונספר כהצלחה — כלומר דיווח על שחזור שלא קרה.
    """
    await make_project(owner, slug="docs")
    await make_document(owner, slug="a", title="מסמך", content="מקורי")
    raw = await archive_bytes()

    await owner.put(
        "/api/projects/docs/docs/a",
        json={"content": "השתנה", "client_seq": 3, "editor_id": "someone"},
        headers=WRITE,
    )

    data = read(raw)
    _, first_docs, first_warnings = await _restore(AsgiClient(owner), data, "upsert")
    assert first_docs == 1, first_warnings
    assert (await owner.get("/api/projects/docs/docs/a")).json()["content"] == "מקורי"

    # שוב, ומזהה העורך חייב להיות אחר — אחרת הכתיבה נדחית
    await owner.put(
        "/api/projects/docs/docs/a",
        json={"content": "שוב השתנה", "client_seq": 4, "editor_id": "someone"},
        headers=WRITE,
    )
    _, second_docs, second_warnings = await _restore(AsgiClient(owner), data, "upsert")
    assert second_docs == 1, f"השחזור השני לא נכתב: {second_warnings}"
    assert (await owner.get("/api/projects/docs/docs/a")).json()["content"] == "מקורי"


async def test_a_rejected_write_is_counted_as_a_warning_not_a_success(owner, read):
    """כשהכתיבה כן נדחית, זה חייב להופיע — לא להיספר כהצלחה."""
    await make_project(owner, slug="docs")
    await make_document(owner, slug="a", title="מסמך", content="מקורי")
    data = read(await archive_bytes())

    # אותו מזהה עורך שהשחזור ישתמש בו, עם המונה כבר בתקרה
    await owner.put(
        "/api/projects/docs/docs/a",
        json={"content": "תפס את התקרה", "client_seq": restore_script.MAX_CLIENT_SEQ,
              "editor_id": "restore-fixed"},
        headers=WRITE,
    )

    _, written, warnings = await _restore(
        AsgiClient(owner), data, "upsert", editor_id="restore-fixed"
    )
    assert written == 0
    assert any("נדחה" in w for w in warnings), warnings


async def test_upsert_does_not_duplicate_links(owner, read):
    """אין אילוץ ייחודיות על קישורים — הדה-דופליקציה היא באחריות השחזור."""
    await make_project(owner, slug="docs")
    await make_document(owner)
    await owner.post(
        "/api/projects/docs/links",
        json={"title": "אתר", "url": "https://example.com"},
        headers=WRITE,
    )
    data = read(await archive_bytes())

    await _restore(AsgiClient(owner), data, "upsert")
    await _restore(AsgiClient(owner), data, "upsert")

    links = (await owner.get("/api/projects/docs/links")).json()
    assert len(links) == 1, f"הקישור שוכפל: {[item['url'] for item in links]}"


async def test_upsert_realigns_the_project_name(owner, read):
    """שם ששונה אחרי הגיבוי חוזר למה שגובה."""
    await make_project(owner, slug="docs", name="השם המקורי")
    await make_document(owner)
    data = read(await archive_bytes())

    await owner.patch("/api/projects/docs", json={"name": "שם אחר לגמרי"}, headers=WRITE)
    await _restore(AsgiClient(owner), data, "upsert")

    assert (await owner.get("/api/projects/docs")).json()["name"] == "השם המקורי"


async def test_skip_mode_never_writes_to_an_existing_project(owner, read):
    """הטענה על מה ש*לא* נשלח — לא רק על התוצאה."""
    await make_project(owner, slug="docs")
    await make_document(owner, slug="a", content="קיים")
    data = read(await archive_bytes())

    client = AsgiClient(owner)
    await _restore(client, data, "skip")

    writes = [(verb, path) for verb, path in client.calls if verb in ("PUT", "POST", "PATCH", "DELETE")]
    assert writes == [], f"skip ביצע כתיבות: {writes}"


# ── קריאת ארכיון פגום ─────────────────────────────────────────────────


def test_reading_something_that_is_not_a_zip_fails_clearly(tmp_path):
    path = tmp_path / "not-a-zip.zip"
    path.write_bytes("בכלל לא ארכיון".encode("utf-8"))
    with pytest.raises(SystemExit) as exc:
        restore_script.read_archive(path, decrypt=False)
    assert "ZIP" in str(exc.value)
    assert "--decrypt" in str(exc.value), "ההודעה לא מכוונת למקרה הנפוץ — קובץ מוצפן"


def test_wrong_passphrase_fails_clearly(tmp_path, monkeypatch):
    from app.crypto import encrypt

    path = tmp_path / "sealed.zip.enc"
    path.write_bytes(encrypt(b"PK\x03\x04payload", "הנכונה"))
    monkeypatch.setenv("BACKUP_PASSPHRASE", "השגויה")

    with pytest.raises(SystemExit) as exc:
        restore_script.read_archive(path, decrypt=True)
    assert "הפענוח נכשל" in str(exc.value)


def test_a_corrupt_member_fails_clearly(tmp_path):
    """CRC שבור — הארכיון נראה תקין ברשימה ונשבר בקריאה."""
    path = tmp_path / "corrupt.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("docs/a.md", "תוכן תקין")

    raw = bytearray(path.read_bytes())
    # פוגעים בבתים של התוכן הדחוס, אחרי הכותרת המקומית
    for i in range(40, min(60, len(raw))):
        raw[i] ^= 0xFF
    path.write_bytes(bytes(raw))

    with pytest.raises(SystemExit):
        restore_script.read_archive(path, decrypt=False)


@pytest.mark.parametrize(
    "member",
    ["alpha/draft/install.md", "__MACOSX/alpha/._install.md", "alpha/sub/links.json"],
)
def test_nested_and_helper_paths_are_ignored(tmp_path, member):
    """הארכיון שלנו שטוח, אבל הקובץ מגיע מבחוץ."""
    path = tmp_path / "nested.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", '{"created_at": "x"}')
        archive.writestr("alpha/install.md", "# תקין\n")
        archive.writestr("alpha/links.json", '{"name":"אלפא","slug":"alpha","links":[]}')
        archive.writestr(member, "לא אמור להיכנס")

    data = restore_script.read_archive(path, decrypt=False)
    assert set(data["projects"]) == {"alpha"}
    assert [d["slug"] for d in data["projects"]["alpha"]["documents"]] == ["install"]
