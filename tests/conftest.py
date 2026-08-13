"""ערכים ו-fixtures משותפים לכל הבדיקות.

**אל תייבאו מכאן fixtures.** pytest מוצא כל fixture שמוגדר ב-conftest
בעצמו, וייבוא מפורש רושם אותו **מחדש** במודול המייבא — עם cache נפרד
משלו. עבור fixture עם ``scope="session"`` זה אומר שהוא ירוץ פעם לכל
מודול שמייבא אותו, וה-scope פשוט לא מתקיים.

זה כבר נשך: ``mcp_lifespan`` מרים את מנהל הסשנים של ה-MCP, ול-
``StreamableHTTPSessionManager`` יש מגבלה מפורשת ש-``run()`` נקרא פעם
אחת למופע. הריצה השנייה נפלה על "can only be called once per instance",
והכשל נראה כאילו הוא בקוד השרת.

קבועים (``EMAIL``, ``WRITE``) ופונקציות עזר (``make_project``) כן
מיובאים — הם אינם fixtures ואין להם cache.
"""

from __future__ import annotations

import asyncio
import json
import os
import re

# חייב לקרות לפני הייבוא של app: ההגדרות נטענות פעם אחת בזמן ייבוא,
# ושרת ה-MCP נבנה ומורכב רק אם יש טוקן. בלי זה נתיב /mcp לא היה קיים
# בבדיקות כלל, וכל הכיסוי שלו היה נעלם בשקט.
#
# השמה ולא setdefault: משתנה סביבה קיים — למשל ריק ב-CI, או ערך אחר
# במכונת פיתוח — היה משתלט על הריצה. ריק היה מכבה את ההרכבה, וערך אחר
# היה מפיל את בדיקות האימות. הבדיקות חייבות להיות דטרמיניסטיות.
MCP_TOKEN = "m" * 40
os.environ["MCP_TOKEN"] = MCP_TOKEN

# זרימת ה-OAuth נדלקת רק כשיש כתובת חיצונית ידועה, כי ה-issuer נכתב
# לתוך מסמכי המטא-דאטה. בלי הערך הזה חצי משטח האימות לא היה נבדק כלל.
#
# הערך נבחר כך שיהיה זהה ל-Origin שהבדיקות שלחו קודם: origin_allowlist
# נגזר ממנו כשאין ALLOWED_ORIGINS מפורש, ולכן שינוי כאן היה משנה בשקט
# את ה-Origin של כל בקשה משנת מצב בכל הבדיקות.
MCP_ISSUER = "http://localhost:8000"
os.environ["RENDER_EXTERNAL_URL"] = MCP_ISSUER

import pytest  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed_admin  # noqa: E402

_settings = get_settings()
_allowlist = sorted(_settings.origin_allowlist)

# ה-Origin שהבדיקות שולחות בכל בקשה משנת מצב. בפיתוח אין allowlist מפורש
# והמקורות המקומיים מאושרים לפי הכלל, ולכן נבחר כזה.
if _allowlist:
    ORIGIN = _allowlist[0]
elif _settings.allow_loopback_origins:
    ORIGIN = "http://localhost:8000"
else:
    # לא assert: תחת python -O הוא נמחק, והכישלון היה חוזר בתור IndexError
    # מבלבל בשורה הבאה במקום ההודעה הזו.
    raise RuntimeError(
        "origin_allowlist ריק — הגדירו ALLOWED_ORIGINS או RENDER_EXTERNAL_URL, "
        "או הריצו עם ENVIRONMENT=development"
    )
WRITE = {"Origin": ORIGIN}

EMAIL = "admin@example.com"
PASSWORD = "correct-horse-battery"  # noqa: S105 — fixture לבדיקות, לא סוד אמיתי


@pytest.fixture(scope="session")
async def seeded_admin():
    """יוצר את המשתמש היחיד. httpx לא מריץ lifespan, ולכן זה נעשה כאן.

    ההרצה כפולה בכוונה — ה-seed חייב להיות idempotent.
    """
    async with SessionLocal() as session:
        await seed_admin(session)
        await seed_admin(session)
    async with SessionLocal() as session:
        count = (
            await session.execute(text("SELECT count(*) FROM users WHERE email = :e"), {"e": EMAIL})
        ).scalar_one()
    assert count == 1, f"ה-seed יצר {count} משתמשים במקום אחד"
    yield


@pytest.fixture
async def clean_projects(seeded_admin):
    """כל טסט מתחיל מספרייה ריקה."""
    async with SessionLocal() as session:
        await session.execute(text("DELETE FROM projects"))
        await session.commit()
    yield


@pytest.fixture
async def owner(clean_projects):
    """לקוח מחובר."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/auth/login", json={"email": EMAIL, "password": PASSWORD}, headers=WRITE
        )
        assert response.status_code == 200, response.text
        yield client


@pytest.fixture
async def anon(clean_projects):
    """לקוח לא מחובר."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


async def make_project(owner, slug="docs", name="התיעוד", visibility="private"):
    response = await owner.post(
        "/api/projects",
        json={"name": name, "slug": slug, "visibility": visibility},
        headers=WRITE,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def make_document(owner, project="docs", slug="installation", title="התקנה", content="# התקנה"):
    response = await owner.post(
        f"/api/projects/{project}/docs",
        json={"title": title, "slug": slug, "content": content},
        headers=WRITE,
    )
    assert response.status_code == 201, response.text
    return response.json()


# תקרה להמתנה על עליית ה-lifespan. בלעדיה, lifespan שנתקע — למשל חיבור
# DB שאינו חוזר — היה תולה את כל הריצה עד ה-timeout של ה-CI, וזו בדיוק
# התוצאה שההמתנה הכפולה למטה נועדה למנוע.
LIFESPAN_TIMEOUT = 30


@pytest.fixture(scope="session")
async def mcp_lifespan():
    """מרים את השרת פעם אחת לכל הריצה.

    לא פעם לכל בדיקה: ל-StreamableHTTPSessionManager יש מגבלה מפורשת
    ש-run() נקרא פעם אחת למופע, והכניסה השנייה ל-lifespan נופלת על
    "can only be called once per instance". זו גם התנהגות הפרודקשן —
    התהליך עולה פעם אחת.

    ה-lifespan רץ בתוך משימה ייעודית ולא ישירות ב-fixture, כי anyio
    קושר cancel scope למשימה שפתחה אותו — ו-pytest מריץ setup ו-teardown
    של fixture ברמת סשן משתי משימות שונות. בלי זה ה-teardown נופל על
    "Attempted to exit cancel scope in a different task".
    """
    started = asyncio.Event()
    stopping = asyncio.Event()

    async def _run_lifespan() -> None:
        async with app.router.lifespan_context(app):
            started.set()
            await stopping.wait()

    task = asyncio.create_task(_run_lifespan())

    # לא await started.wait() לבדו: אם ה-lifespan נופל לפני set — למשל
    # ה-DB לא זמין — האירוע לעולם לא נדלק והריצה נתלית בלי הודעה עד
    # ה-timeout של ה-CI. ההמתנה על שניהם הופכת את זה לשגיאה מיידית
    # שמראה את הסיבה האמיתית.
    waiter = asyncio.create_task(started.wait())
    done, _ = await asyncio.wait(
        {waiter, task}, timeout=LIFESPAN_TIMEOUT, return_when=asyncio.FIRST_COMPLETED
    )
    if waiter not in done:
        waiter.cancel()
        if task.done():
            await task  # מרים את החריגה המקורית
            raise RuntimeError("ה-lifespan הסתיים בלי לאותת על עלייה")
        task.cancel()
        raise RuntimeError(
            f"ה-lifespan לא עלה תוך {LIFESPAN_TIMEOUT} שניות — ככל הנראה תקוע"
        )

    yield
    stopping.set()
    await task


# ── עזרי JSON-RPC מול נתיב ה-MCP ──────────────────────────────────────
#
# כאן ולא באחד ממודולי הבדיקות: יותר ממודול אחד משתמש בהם, וייבוא בין
# מודולי בדיקות יוצר תלות בשמות פרטיים ובסדר האיסוף.

GOOD = {
    "Authorization": f"Bearer {MCP_TOKEN}",
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


def _rpc(request_id: int, method: str, params: dict | None = None) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}


def _payload(response) -> dict:
    """התשובה מגיעה כ-SSE; מוציאים ממנה את ה-JSON."""
    match = re.search(r"^data: (.+)$", response.text, re.M)
    assert match, f"לא נמצא גוף JSON בתשובה: {response.text[:200]}"
    return json.loads(match.group(1))


def _tool_output(response) -> dict:
    result = _payload(response)["result"]
    if "structuredContent" in result:
        return result["structuredContent"]
    return json.loads(result["content"][0]["text"])
