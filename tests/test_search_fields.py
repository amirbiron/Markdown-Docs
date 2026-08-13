"""השדות שנוספו ל-SearchHit עבור צרכן שאינו אנושי.

בלי rank, צרכן שאינו רואה את המסך אינו יכול להבחין בין התאמה חזקה
לחלשה, ולכן הוא נאלץ למשוך את תוכן כל התוצאות רק כדי להחליט.
"""

from __future__ import annotations

import uuid

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


async def test_hit_carries_rank_and_updated_at(owner):
    await make_project(owner)
    await make_document(owner, slug="install", title="התקנה", content="מדריך התקנה")

    hits = (await owner.get("/api/search", params={"q": "התקנה"})).json()
    assert len(hits) == 1
    hit = hits[0]

    assert hit["rank"] > 0, "ts_rank חייב להיות חיובי על התאמה אמיתית"
    assert hit["updated_at"], "בלי updated_at אי אפשר לדעת אם התוצאה עדכנית"


async def test_owner_gets_stable_id_anonymous_does_not(owner, anon):
    """המזהה נחשף לבעלים בלבד, כמו בשאר הסכמות."""
    await make_project(owner, visibility="public")
    created = await make_document(owner, slug="install", title="התקנה", content="מדריך התקנה")

    mine = (await owner.get("/api/search", params={"q": "התקנה"})).json()
    assert mine[0]["doc_id"] == created["id"]
    uuid.UUID(mine[0]["doc_id"])

    theirs = (await anon.get("/api/search", params={"q": "התקנה"})).json()
    assert theirs, "פרויקט פומבי חייב להיות ניתן לחיפוש אנונימי"
    assert theirs[0]["doc_id"] is None


async def test_rank_orders_results_and_is_comparable(owner):
    """התאמה בכותרת מדורגת גבוה מהתאמה בתוכן, וה-rank משקף את זה."""
    await make_project(owner)
    await make_document(owner, slug="a", title="התקנה", content="טקסט אחר לגמרי")
    await make_document(owner, slug="b", title="נושא אחר", content="מדריך התקנה ארוך")

    hits = (await owner.get("/api/search", params={"q": "התקנה"})).json()
    assert len(hits) == 2
    assert hits[0]["doc_slug"] == "a"
    assert hits[0]["rank"] >= hits[1]["rank"], "הסדר חייב להתאים ל-rank שמוחזר"


async def test_fuzzy_hit_also_carries_the_new_fields(owner):
    """מסלול הדמיון בונה תוצאה דרך אותה פונקציה, ולכן אסור שיחסיר שדות."""
    await make_project(owner)
    created = await make_document(owner, slug="install", title="התקנה", content="גוף")

    hits = (await owner.get("/api/search", params={"q": "התקנא"})).json()
    assert hits, "שגיאת כתיב הייתה אמורה להיתפס בדמיון"
    assert hits[0]["match"] == "fuzzy"
    assert hits[0]["rank"] > 0
    assert hits[0]["doc_id"] == created["id"]
    assert hits[0]["updated_at"]
