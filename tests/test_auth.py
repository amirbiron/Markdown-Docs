"""מדדי הקבלה של שלב 2, אחד לאחד מה-ROADMAP.

מריצים מול Postgres אמיתי:
    DATABASE_URL=... python3 -m pytest tests/test_auth.py -v
"""

from __future__ import annotations

import time

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.seed import seed_admin
from app.security import COOKIE_NAME, issue_token, login_limiter, read_token

from tests.conftest import EMAIL, ORIGIN, PASSWORD, WRITE  # noqa: F401


@pytest.fixture(scope="session", autouse=True)
async def seeded_admin():
    """httpx לא מריץ lifespan, ולכן ה-seed נקרא כאן במפורש.

    זו גם בדיקה בפני עצמה: הרצה כפולה חייבת להיות בטוחה.
    """
    async with SessionLocal() as session:
        await seed_admin(session)
        await seed_admin(session)  # idempotent — הרצה שנייה לא משנה כלום
    async with SessionLocal() as session:
        count = (
            await session.execute(text("SELECT count(*) FROM users WHERE email = :e"), {"e": EMAIL})
        ).scalar_one()
    assert count == 1, f"ה-seed יצר {count} משתמשים במקום אחד"
    yield


@pytest.fixture(autouse=True)
async def reset_session_version():
    """טסט אחד מעלה את session_version — מחזירים כדי לא להשפיע על השאר."""
    yield
    async with SessionLocal() as session:
        await session.execute(text("UPDATE users SET session_version = 1 WHERE email = :e"), {"e": EMAIL})
        await session.commit()


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
async def clean_limiter():
    login_limiter._buckets.clear()
    yield
    login_limiter._buckets.clear()


async def _user_row():
    async with SessionLocal() as session:
        row = (
            await session.execute(text("SELECT id, session_version FROM users WHERE email = :e"), {"e": EMAIL})
        ).one()
        return str(row[0]), row[1]


async def _login(client) -> str:
    response = await client.post(
        "/api/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 200, response.text
    return response.cookies[COOKIE_NAME]


# ── מדד 1: בקשה בלי cookie מקבלת 401 ──────────────────────────────────


async def test_no_cookie_is_401_on_protected_routes(client):
    """נתיב שדורש אימות דוחה בקשה בלי cookie."""
    response = await client.post(
        "/api/projects", json={"name": "בלי כניסה"}, headers={"Origin": ORIGIN}
    )
    assert response.status_code == 401


async def test_me_is_200_for_anonymous(client):
    """"אף אחד" הוא תשובה, לא שגיאה — אחרת כל טעינת דף מייצרת רעש."""
    response = await client.get("/api/auth/me")
    assert response.status_code == 200
    assert response.json() == {"authenticated": False}


async def test_login_then_me_succeeds(client):
    await _login(client)
    response = await client.get("/api/auth/me")
    assert response.status_code == 200
    assert response.json()["email"] == EMAIL


# ── מדד 2: exp שעבר נדחה גם כשהחתימה תקפה ─────────────────────────────


async def test_expired_token_rejected_even_with_valid_signature(client):
    user_id, session_version = await _user_row()

    # טוקן שנחתם כך שכבר פג. החתימה תקינה לחלוטין — רק ה-exp בעבר.
    stale = issue_token(user_id, session_version, now=time.time() - 40 * 86400)
    assert read_token(stale) is None, "read_token קיבל טוקן שפג"

    client.cookies.set(COOKIE_NAME, stale)
    assert (await client.get("/api/auth/me")).json()["authenticated"] is False

    # ואותו טוקן, נחתם עכשיו, כן עובד — כלומר הדחייה הייתה בגלל exp
    # ולא בגלל שהחתימה נשברה.
    client.cookies.set(COOKIE_NAME, issue_token(user_id, session_version))
    assert (await client.get("/api/auth/me")).status_code == 200


# ── מדד 3: העלאת session_version מנתקת cookie קיים ────────────────────


async def test_bumping_session_version_invalidates_cookie(client):
    await _login(client)
    assert (await client.get("/api/auth/me")).status_code == 200

    async with SessionLocal() as session:
        await session.execute(
            text("UPDATE users SET session_version = session_version + 1 WHERE email = :e"), {"e": EMAIL}
        )
        await session.commit()

    assert (await client.get("/api/auth/me")).json()["authenticated"] is False, (
        "cookie שרד העלאת session_version"
    )


# ── מדד 4: Origin חסר או זר מקבל 403 ──────────────────────────────────


@pytest.mark.parametrize(
    "headers,label",
    [({}, "חסר"), ({"Origin": "https://evil.example"}, "זר")],
)
async def test_mutating_request_rejects_bad_origin(client, headers, label):
    response = await client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD}, headers=headers)
    assert response.status_code == 403, f"Origin {label} לא נחסם"


async def test_get_does_not_require_origin(client):
    """הבדיקה חלה על בקשות משנות מצב בלבד."""
    assert (await client.get("/api/health")).status_code == 200


# ── מדד 5: כישלונות רצופים מפעילים backoff ────────────────────────────


async def test_backoff_blocks_even_the_correct_password(client):
    for attempt in range(5):
        response = await client.post(
            "/api/auth/login",
            json={"email": EMAIL, "password": "wrong"},
            headers={"Origin": ORIGIN},
        )
        assert response.status_code == 401, f"ניסיון {attempt + 1}"

    # הראיה שהחסימה פעילה ולא סתם סיסמה שגויה: הסיסמה *הנכונה* נדחית.
    blocked = await client.post(
        "/api/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
        headers={"Origin": ORIGIN},
    )
    assert blocked.status_code == 401, "החסימה לא הופעלה אחרי חמישה כישלונות"

    # וההודעה זהה לזו של סיסמה שגויה — לא מסגירים שהחשבון חסום.
    wrong = await client.post(
        "/api/auth/login", json={"email": EMAIL, "password": "wrong"}, headers={"Origin": ORIGIN}
    )
    assert blocked.json() == wrong.json(), "תשובת החסימה נבדלת מתשובת הסיסמה השגויה"

    # אחרי שהחסימה פגה, הסיסמה הנכונה עובדת שוב.
    login_limiter._buckets.clear()
    assert (
        await client.post(
            "/api/auth/login", json={"email": EMAIL, "password": PASSWORD}, headers={"Origin": ORIGIN}
        )
    ).status_code == 200


async def test_successful_login_resets_the_counter(client):
    for _ in range(3):
        await client.post(
            "/api/auth/login", json={"email": EMAIL, "password": "wrong"}, headers={"Origin": ORIGIN}
        )
    await _login(client)
    # אחרי הצלחה המונה מתאפס, ולכן שלושה כישלונות נוספים עדיין לא חוסמים
    for _ in range(3):
        await client.post(
            "/api/auth/login", json={"email": EMAIL, "password": "wrong"}, headers={"Origin": ORIGIN}
        )
    assert (
        await client.post(
            "/api/auth/login", json={"email": EMAIL, "password": PASSWORD}, headers={"Origin": ORIGIN}
        )
    ).status_code == 200


# ── מדד 6: גוף מעל 1MB מקבל 413 בלי שנקרא במלואו ──────────────────────


async def test_oversized_body_is_rejected(client):
    limit = get_settings().max_body_bytes
    payload = {"email": EMAIL, "password": "x" * (limit + 1024)}
    response = await client.post("/api/auth/login", json=payload, headers={"Origin": ORIGIN})
    assert response.status_code == 413


async def test_hebrew_is_counted_in_bytes_not_characters(client):
    """תו עברי הוא שני בתים. גבול שנספר בתווים היה מפספס כאן."""
    limit = get_settings().max_body_bytes
    hebrew = "א" * (limit // 2 + 512)  # מתחת לגבול בתווים, מעליו בבתים
    assert len(hebrew) < limit < len(hebrew.encode("utf-8"))
    response = await client.post(
        "/api/auth/login", json={"email": EMAIL, "password": hebrew}, headers={"Origin": ORIGIN}
    )
    assert response.status_code == 413, "הגבול נספר בתווים ולא בבתים"


async def test_origin_is_checked_before_the_body_is_read(client):
    """סדר השכבות: מקור פסול נדחה בלי שנקרא ממנו מגה־בייט."""
    limit = get_settings().max_body_bytes
    payload = {"email": EMAIL, "password": "x" * (limit + 1024)}
    response = await client.post(
        "/api/auth/login", json=payload, headers={"Origin": "https://evil.example"}
    )
    assert response.status_code == 403, "BodySizeLimit רץ לפני OriginGuard"
