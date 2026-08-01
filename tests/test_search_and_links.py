"""מדדי הקבלה של שלב 4.

מה-ROADMAP: חיפוש "התקנה" מוצא את המסמך גם אם נכתב מנוקד, וכותרת מנצחת
תוכן בדירוג.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.db import SessionLocal
from app.main import app
from app.seed import seed_admin
from tests.conftest import EMAIL, PASSWORD, WRITE


@pytest.fixture(scope="session", autouse=True)
async def seeded():
    async with SessionLocal() as session:
        await seed_admin(session)
    yield


@pytest.fixture(autouse=True)
async def clean_projects():
    async with SessionLocal() as session:
        await session.execute(text("DELETE FROM projects"))
        await session.commit()
    yield


@pytest.fixture
async def owner():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        response = await c.post(
            "/api/auth/login", json={"email": EMAIL, "password": PASSWORD}, headers=WRITE
        )
        assert response.status_code == 200, response.text
        yield c


@pytest.fixture
async def anon():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


async def _project(owner, slug="docs", name="התיעוד", visibility="private"):
    response = await owner.post(
        "/api/projects", json={"name": name, "slug": slug, "visibility": visibility}, headers=WRITE
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _document(owner, project="docs", slug="a", title="כותרת", content="תוכן"):
    response = await owner.post(
        f"/api/projects/{project}/docs",
        json={"title": title, "slug": slug, "content": content},
        headers=WRITE,
    )
    assert response.status_code == 201, response.text
    return response.json()


# ── מדד: ניקוד ─────────────────────────────────────────────────────────


async def test_search_finds_pointed_text_without_nikud(owner):
    """unaccent לא מסיר ניקוד עברי — בלי strip_nikud זה לא היה נמצא."""
    await _project(owner)
    await _document(owner, slug="install", title="הַתְקָנָה", content="מַדְרִיךְ הַתְקָנָה מְלֵאָה")

    hits = (await owner.get("/api/search", params={"q": "התקנה"})).json()
    assert len(hits) == 1, f"מסמך מנוקד לא נמצא בחיפוש לא מנוקד: {hits}"
    assert hits[0]["doc_slug"] == "install"


async def test_search_also_works_the_other_way(owner):
    """טקסט לא מנוקד נמצא גם בחיפוש מנוקד."""
    await _project(owner)
    await _document(owner, slug="install", title="התקנה", content="מדריך התקנה")
    hits = (await owner.get("/api/search", params={"q": "הַתְקָנָה"})).json()
    assert len(hits) == 1


# ── מדד: כותרת מנצחת תוכן ─────────────────────────────────────────────


async def test_title_outranks_content(owner):
    await _project(owner)
    await _document(
        owner, slug="in-content", title="נושא אחר", content="פסקה ארוכה שמזכירה התקנה באמצע."
    )
    await _document(owner, slug="in-title", title="התקנה", content="משהו לא קשור.")

    hits = (await owner.get("/api/search", params={"q": "התקנה"})).json()
    assert len(hits) == 2
    assert hits[0]["doc_slug"] == "in-title", f"הכותרת לא ניצחה: {[h['doc_slug'] for h in hits]}"


# ── הרשאות ────────────────────────────────────────────────────────────


async def test_search_hides_private_projects(anon, owner):
    await _project(owner, slug="open", name="פתוח", visibility="public")
    await _project(owner, slug="closed", name="סגור", visibility="private")
    await _document(owner, project="open", slug="a", title="סודות", content="תוכן פומבי")
    await _document(owner, project="closed", slug="b", title="סודות", content="תוכן פרטי")

    public_hits = (await anon.get("/api/search", params={"q": "סודות"})).json()
    assert [h["project_slug"] for h in public_hits] == ["open"]

    owner_hits = (await owner.get("/api/search", params={"q": "סודות"})).json()
    assert {h["project_slug"] for h in owner_hits} == {"open", "closed"}


# ── קלט חופשי ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("weird", ["?", "&&", "'", '"', "a & b", "!!!", "*", "x:y", "( ["])
async def test_odd_queries_do_not_crash(owner, weird):
    """to_tsquery היה זורק שגיאת תחביר על חלק מאלה."""
    await _project(owner)
    await _document(owner)
    response = await owner.get("/api/search", params={"q": weird})
    assert response.status_code == 200, f"{weird!r} הפיל את החיפוש: {response.text}"


async def test_percent_is_not_a_wildcard(owner):
    """בלי escape נכון, אחוז אחד היה מחזיר את כל המסמכים (כלל 9)."""
    await _project(owner)
    await _document(owner, slug="a", title="ראשון", content="תוכן")
    await _document(owner, slug="b", title="שני", content="תוכן")
    hits = (await owner.get("/api/search", params={"q": "%"})).json()
    assert hits == [], f"'%' התנהג כ-wildcard והחזיר {len(hits)} תוצאות"


async def test_search_results_are_stable(owner):
    """דירוגים שווים חייבים לצאת באותו סדר בכל קריאה (כלל 8)."""
    await _project(owner)
    for i in range(6):
        await _document(owner, slug=f"d{i}", title="זהה", content="אותו תוכן בדיוק")
    reads = [
        [h["doc_slug"] for h in (await owner.get("/api/search", params={"q": "זהה"})).json()]
        for _ in range(4)
    ]
    assert all(r == reads[0] for r in reads), f"הסדר השתנה: {reads}"


async def test_snippet_marks_the_match(owner):
    await _project(owner)
    await _document(
        owner, slug="a", title="מדריך", content="פסקה ראשונה. המילה חשובה נמצאת כאן. פסקה אחרונה."
    )
    hits = (await owner.get("/api/search", params={"q": "חשובה"})).json()
    assert "«חשובה»" in hits[0]["snippet"], hits[0]["snippet"]


async def test_fuzzy_fallback_catches_typos(owner):
    """כשהחיפוש הרגיל לא מצא כלום, נופלים להשוואת דמיון."""
    await _project(owner)
    await _document(owner, slug="a", title="התקנה", content="תוכן")
    hits = (await owner.get("/api/search", params={"q": "התקנת"})).json()
    assert hits, "שגיאת כתיב לא נתפסה"
    assert hits[0]["match"] == "fuzzy"


# ── קישורים ───────────────────────────────────────────────────────────


async def _link(owner, title="CodeKeeper", url="https://codekeeper.com", project="docs"):
    return await owner.post(
        f"/api/projects/{project}/links", json={"title": title, "url": url}, headers=WRITE
    )


async def test_link_crud(owner):
    await _project(owner)
    created = await _link(owner)
    assert created.status_code == 201, created.text
    link_id = created.json()["id"]

    listed = await owner.get("/api/projects/docs/links")
    assert [item["url"] for item in listed.json()] == ["https://codekeeper.com"]

    patched = await owner.patch(
        f"/api/projects/docs/links/{link_id}", json={"title": "שם חדש"}, headers=WRITE
    )
    assert patched.json()["title"] == "שם חדש"

    assert (
        await owner.delete(f"/api/projects/docs/links/{link_id}", headers=WRITE)
    ).status_code == 204
    assert (await owner.get("/api/projects/docs/links")).json() == []


@pytest.mark.parametrize(
    "bad",
    [
        "javascript:alert(1)",
        "JavaScript:alert(1)",
        "java\tscript:alert(1)",
        "data:text/html,<script>x</script>",
        "vbscript:msgbox",
        "//evil.example",
        "/relative",
        "ftp://x.co",
        "https://",
    ],
)
async def test_dangerous_urls_are_rejected(owner, bad):
    await _project(owner)
    response = await _link(owner, url=bad)
    assert response.status_code == 422, f"{bad!r} התקבל"


async def test_patching_to_a_dangerous_url_is_rejected(owner):
    """הוולידציה חלה גם בעדכון, לא רק ביצירה."""
    await _project(owner)
    link_id = (await _link(owner)).json()["id"]
    response = await owner.patch(
        f"/api/projects/docs/links/{link_id}",
        json={"url": "javascript:alert(1)"},
        headers=WRITE,
    )
    assert response.status_code == 422


async def test_links_appear_in_the_public_project_response(anon, owner):
    await _project(owner, visibility="public")
    await _link(owner)
    body = (await anon.get("/api/projects/docs")).json()
    assert [item["url"] for item in body["links"]] == ["https://codekeeper.com"]


async def test_links_of_a_private_project_are_404_for_anonymous(anon, owner):
    await _project(owner, visibility="private")
    await _link(owner)
    assert (await anon.get("/api/projects/docs/links")).status_code == 404


async def test_anonymous_cannot_create_links(anon, owner):
    await _project(owner, visibility="public")
    response = await anon.post(
        "/api/projects/docs/links",
        json={"title": "x", "url": "https://x.co"},
        headers=WRITE,
    )
    assert response.status_code == 401


async def test_links_keep_a_stable_order(owner):
    await _project(owner)
    for i in range(5):
        await _link(owner, title=f"קישור {i}", url=f"https://x{i}.co")
    reads = [
        [item["url"] for item in (await owner.get("/api/projects/docs/links")).json()]
        for _ in range(3)
    ]
    assert all(r == reads[0] for r in reads)
    assert reads[0] == [f"https://x{i}.co" for i in range(5)]
