"""זרימת ה-OAuth של שרת ה-MCP, מקצה לקצה.

הבדיקה המרכזית כאן רצה את כל הזרימה כפי שלקוח אמיתי מריץ אותה: גילוי,
רישום עצמי, אישור בדפדפן, החלפת קוד בטוקן, וקריאה לכלי עם הטוקן שהתקבל.
בלעדיה כל חלק נבדק לחוד ואף אחד לא מוכיח שהם מתחברים.

הזרימה הזו קיימת מסיבה אחת: **claude.ai אינו מציע שדה להזנת טוקן.**
המסך שלו מבקש OAuth Client ID ו-Client Secret, שניהם אופציונליים, כי
הוא מצפה להירשם בעצמו. טוקן סטטי פשוט אין לאן להזין.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import re
import secrets
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import ASGITransport, AsyncClient

from app.db import SessionLocal
from app.main import app
from app.mcp import oauth_consent, oauth_store as store
# קבועים ופונקציות עזר בלבד. את ה-fixtures — seeded_admin,
# clean_projects, mcp_lifespan — אין לייבא: pytest מוצא אותם ב-conftest
# לבד, וייבוא רושם אותם מחדש במודול עם cache נפרד. ראו conftest.
from tests.conftest import (
    EMAIL,
    GOOD,
    MCP_ISSUER,
    PASSWORD,
    WRITE,
    _rpc,
    _tool_output,
    make_document,
    make_project,
)

REDIRECT = "https://claude.ai/api/mcp/auth_callback"


def _pkce() -> tuple[str, str]:
    """זוג verifier/challenge בשיטת S256, כפי שהלקוח מייצר."""
    verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


@pytest.fixture
async def client(mcp_lifespan, seeded_admin):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url=MCP_ISSUER, follow_redirects=False
    ) as http:
        yield http


async def _login(http: AsyncClient) -> None:
    response = await http.post(
        "/api/auth/login", json={"email": EMAIL, "password": PASSWORD}, headers=WRITE
    )
    assert response.status_code == 200, response.text


async def _register(http: AsyncClient) -> dict:
    response = await http.post(
        "/mcp/register",
        json={
            "client_name": "בדיקה",
            "redirect_uris": [REDIRECT],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "client_secret_post",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _exchange(http: AsyncClient, registration: dict, code: str, verifier: str):
    """מחליף קוד בזוג טוקנים ומחזיר את התשובה כמו שהיא.

    מוחזרת התשובה ולא רק הגוף, כי חלק מהבדיקות בודקות את קוד המצב.
    """
    return await http.post(
        "/mcp/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT,
            "client_id": registration["client_id"],
            "client_secret": registration.get("client_secret", ""),
            "code_verifier": verifier,
        },
    )


# ── גילוי ─────────────────────────────────────────────────────────────


async def test_discovery_documents_live_at_the_host_root(client):
    """RFC 8414 ו-RFC 9728 קובעים מיקום ביחס לשורש המארח.

    ה-SDK רושם אותם בתוך תת-האפליקציה, כלומר תחת /mcp אחרי ההרכבה.
    בלי ההעתקה לשורש הלקוח פשוט לא מוצא אותם, ואין לזה מעקף בצד שלו.
    """
    prm = await client.get("/.well-known/oauth-protected-resource/mcp")
    assert prm.status_code == 200
    assert prm.json()["resource"] == f"{MCP_ISSUER}/mcp"

    asm = await client.get("/.well-known/oauth-authorization-server")
    assert asm.status_code == 200
    meta = asm.json()
    # ה-endpoints מוצהרים מהשורש, כי שם claude.ai מחפש אותם בפועל —
    # ראו test_metadata_points_at_the_host_root. הם קיימים גם תחת /mcp,
    # ולכן שתי ההצהרות נכונות.
    #
    # ה-issuer נבדק במפורש: הוא השדה שממנו כל השאר נגזרים, ושינוי בו
    # מזיז את כולם בבת אחת.
    assert meta["issuer"].rstrip("/") == MCP_ISSUER
    assert meta["authorization_endpoint"] == f"{MCP_ISSUER}/authorize"
    assert meta["token_endpoint"] == f"{MCP_ISSUER}/token"
    assert meta["registration_endpoint"] == f"{MCP_ISSUER}/register"
    assert set(meta["scopes_supported"]) == {"read", "write"}
    assert "S256" in meta["code_challenge_methods_supported"]


async def test_unauthenticated_call_challenges_with_the_metadata_url(client):
    response = await client.post(
        "/mcp/",
        json=_rpc(1, "tools/list"),
        headers={k: v for k, v in GOOD.items() if k != "Authorization"},
    )
    assert response.status_code == 401
    assert "/.well-known/oauth-protected-resource/mcp" in response.headers["www-authenticate"]


# ── רישום עצמי ────────────────────────────────────────────────────────


async def test_client_registers_itself(client):
    """Dynamic Client Registration — בלעדיו אין client_id להזין."""
    registration = await _register(client)
    assert registration["client_id"]
    assert REDIRECT in registration["redirect_uris"]


async def test_the_registration_round_trips_intact(client):
    """מה שנשמר הוא בדיוק מה ש-get_client מחזיר.

    ה-ClientAuthenticator של ה-SDK משווה מול client.client_secret כפי
    שהוא חוזר מכאן, ולכן המסמך חייב לשרוד את הסיבוב במלואו. אחסון
    מוצפן או hashed היה מחייב להחליף את שכבת אימות הלקוחות כולה —
    ראו MCPOAuthClient.registration להסבר למה זה מקובל דווקא לסוד של
    לקוח, ולמה סודות המשתמש כן נשמרים hashed.
    """
    registration = await _register(client)
    async with SessionLocal() as session:
        row = await store.load_client(session, registration["client_id"])
    assert row is not None
    assert row.registration["client_id"] == registration["client_id"]
    if registration.get("client_secret"):
        assert row.registration["client_secret"] == registration["client_secret"]


# ── הזרימה המלאה ──────────────────────────────────────────────────────


async def test_full_authorization_flow(client, clean_projects):
    """גילוי → רישום → אישור → טוקן → קריאה לכלי.

    זו הבדיקה שמוכיחה שהחלקים מתחברים. כל שלב לחוד כבר נבדק, אבל
    התקלה שגרמה לכל העבודה הזו הייתה בדיוק בחיבור: הכל נראה תקין
    ובכל זאת שום קריאה לא עברה.
    """
    registration = await _register(client)
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(8)

    # /authorize מפנה למסך האישור, ולא מאשר בעצמו.
    authorize = await client.get(
        "/mcp/authorize",
        params={
            "client_id": registration["client_id"],
            "redirect_uri": REDIRECT,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        },
    )
    assert authorize.status_code in (302, 303, 307), authorize.text
    consent_url = authorize.headers["location"]
    assert consent_url.startswith("/mcp-consent?txn=")

    # בלי התחברות אין אישור — הזהות מגיעה מה-cookie ולא מהבקשה.
    anonymous = await client.get(consent_url)
    assert anonymous.status_code == 401

    await _login(client)
    form = await client.get(consent_url)
    assert form.status_code == 200
    assert EMAIL in form.text

    txn = parse_qs(urlparse(consent_url).query)["txn"][0]
    approved = await client.post(
        "/mcp-consent", data={"txn": txn, "decision": "allow"}, headers=WRITE
    )
    assert approved.status_code == 303, approved.text

    callback = urlparse(approved.headers["location"])
    params = parse_qs(callback.query)
    assert params["state"] == [state], "state חייב לחזור כמו שהוא"
    code = params["code"][0]

    # החלפת הקוד בטוקן, עם ה-verifier שמוכיח שזה אותו לקוח.
    token_response = await _exchange(client, registration, code, verifier)
    assert token_response.status_code == 200, token_response.text
    tokens = token_response.json()
    assert tokens["token_type"] == "Bearer"
    assert tokens["refresh_token"]

    # והמבחן האמיתי: קריאה לכלי עם הטוקן שהתקבל.
    await make_project(client, slug="docs", name="תיעוד")
    await make_document(client, project="docs", title="התקנה")

    call = await client.post(
        "/mcp/",
        json=_rpc(2, "tools/call", {"name": "mdocs_map", "arguments": {}}),
        headers=dict(GOOD, Authorization=f"Bearer {tokens['access_token']}"),
    )
    assert call.status_code == 200, call.text
    output = _tool_output(call)
    assert output["ok"] is True, output
    assert output["document_count"] == 1


async def test_denying_returns_the_client_to_its_callback(client):
    """סירוב הוא תשובת פרוטוקול, לא דף שגיאה.

    לקוח שקיבל דף שגיאה במקום הפניה נשאר תלוי ומחכה.
    """
    registration = await _register(client)
    _, challenge = _pkce()
    authorize = await client.get(
        "/mcp/authorize",
        params={
            "client_id": registration["client_id"],
            "redirect_uri": REDIRECT,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "abc",
        },
    )
    txn = parse_qs(urlparse(authorize.headers["location"]).query)["txn"][0]

    await _login(client)
    denied = await client.post(
        "/mcp-consent", data={"txn": txn, "decision": "deny"}, headers=WRITE
    )
    assert denied.status_code == 303
    params = parse_qs(urlparse(denied.headers["location"]).query)
    assert params["error"] == ["access_denied"]
    assert params["state"] == ["abc"]


# ── הגנות ─────────────────────────────────────────────────────────────


async def test_consent_post_requires_a_trusted_origin(client):
    """הגנת ה-CSRF על מסך האישור.

    רישום לקוחות פתוח — כך claude.ai מתחבר — ולכן תוקף יכול להירשם,
    לפתוח בקשת authorize משלו, ולקבל txn חתום תקף. בלי בדיקת Origin,
    הוא היה יכול לגרום לדפדפן של המשתמש המחובר לאשר אותה בשקט.
    """
    registration = await _register(client)
    _, challenge = _pkce()
    authorize = await client.get(
        "/mcp/authorize",
        params={
            "client_id": registration["client_id"],
            "redirect_uri": REDIRECT,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
    )
    txn = parse_qs(urlparse(authorize.headers["location"]).query)["txn"][0]

    await _login(client)
    forged = await client.post(
        "/mcp-consent",
        data={"txn": txn, "decision": "allow"},
        headers={"Origin": "https://evil.example"},
    )
    assert forged.status_code == 403, forged.text


async def test_a_tampered_txn_is_rejected(client):
    """העסקה חתומה, ולכן שינוי שלה אינו עובר בשקט."""
    await _login(client)
    response = await client.get("/mcp-consent", params={"txn": "not-a-real-txn"})
    assert response.status_code == 400


async def test_code_is_single_use(client):
    """קוד שנוצל פעם אחת לא ניתן להחלפה שנייה."""
    registration = await _register(client)
    verifier, challenge = _pkce()
    authorize = await client.get(
        "/mcp/authorize",
        params={
            "client_id": registration["client_id"],
            "redirect_uri": REDIRECT,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
    )
    txn = parse_qs(urlparse(authorize.headers["location"]).query)["txn"][0]
    await _login(client)
    approved = await client.post(
        "/mcp-consent", data={"txn": txn, "decision": "allow"}, headers=WRITE
    )
    code = parse_qs(urlparse(approved.headers["location"]).query)["code"][0]

    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT,
        "client_id": registration["client_id"],
        "client_secret": registration.get("client_secret", ""),
        "code_verifier": verifier,
    }
    assert (await client.post("/mcp/token", data=payload)).status_code == 200
    assert (await client.post("/mcp/token", data=payload)).status_code != 200


async def test_tokens_are_not_stored_in_the_clear(client):
    """גיבוי שדלף לא אמור להכיל טוקנים שמישהו יכול להשתמש בהם."""
    registration = await _register(client)
    verifier, challenge = _pkce()
    authorize = await client.get(
        "/mcp/authorize",
        params={
            "client_id": registration["client_id"],
            "redirect_uri": REDIRECT,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
    )
    txn = parse_qs(urlparse(authorize.headers["location"]).query)["txn"][0]
    await _login(client)
    approved = await client.post(
        "/mcp-consent", data={"txn": txn, "decision": "allow"}, headers=WRITE
    )
    code = parse_qs(urlparse(approved.headers["location"]).query)["code"][0]
    tokens = (await _exchange(client, registration, code, verifier)).json()

    async with SessionLocal() as session:
        row = await store.load_token(session, tokens["access_token"], store.TOKEN_ACCESS)
    assert row is not None
    assert row.token_hash != tokens["access_token"]
    assert row.token_hash == store.token_hash(tokens["access_token"])


# ── הטוקן הסטטי ───────────────────────────────────────────────────────


async def test_the_static_token_still_works(client, clean_projects):
    """MCP_TOKEN לא בוטל.

    הוא המסלול למי שמתחבר בלי דפדפן — Claude Code, סקריפטים, curl —
    ואין לו זרימת אישור. OAuth הוא הרחבה, לא החלפה.
    """
    await _login(client)
    await make_project(client, slug="docs", name="תיעוד")

    call = await client.post(
        "/mcp/",
        json=_rpc(3, "tools/call", {"name": "mdocs_map", "arguments": {}}),
        headers=GOOD,
    )
    assert call.status_code == 200, call.text
    assert _tool_output(call)["ok"] is True


# ── אטומיות תחת מקביליות ──────────────────────────────────────────────
#
# הבדיקות למעלה מריצות את הצריכה סדרתית, ולכן הן עוברות גם כשהמימוש
# הוא load-then-delete — כלומר הן אינן מוכיחות חד-פעמיות. הבדיקות כאן
# מריצות את אותה החלפה במקביל, וזה ההבדל בין הצהרה למימוש.


async def _grant(http: AsyncClient) -> tuple[dict, str, str]:
    """מביא זוג (רישום, קוד, verifier) מוכן להחלפה."""
    registration = await _register(http)
    verifier, challenge = _pkce()
    authorize = await http.get(
        "/mcp/authorize",
        params={
            "client_id": registration["client_id"],
            "redirect_uri": REDIRECT,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
    )
    txn = parse_qs(urlparse(authorize.headers["location"]).query)["txn"][0]
    await _login(http)
    approved = await http.post(
        "/mcp-consent", data={"txn": txn, "decision": "allow"}, headers=WRITE
    )
    code = parse_qs(urlparse(approved.headers["location"]).query)["code"][0]
    return registration, code, verifier


async def test_concurrent_code_exchange_yields_exactly_one_grant(client):
    """אישור אחד של המשתמש חייב לייצר הענקה אחת, גם תחת מרוץ.

    עם load-then-delete שתי הבקשות היו קוראות את הקוד לפני שהמחיקה
    הראשונה נסגרה, ושתיהן היו מנפיקות טוקנים.
    """
    registration, code, verifier = await _grant(client)
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT,
        "client_id": registration["client_id"],
        "client_secret": registration.get("client_secret", ""),
        "code_verifier": verifier,
    }

    results = await asyncio.gather(
        *(client.post("/mcp/token", data=payload) for _ in range(4)),
        return_exceptions=True,
    )
    ok = [r for r in results if not isinstance(r, Exception) and r.status_code == 200]
    assert len(ok) == 1, [
        r.status_code if not isinstance(r, Exception) else repr(r) for r in results
    ]


async def test_concurrent_refresh_rotates_exactly_once(client):
    """רוטציה שאפשר לרוץ אותה פעמיים במקביל אינה רוטציה."""
    registration, code, verifier = await _grant(client)
    tokens = (await _exchange(client, registration, code, verifier)).json()

    payload = {
        "grant_type": "refresh_token",
        "refresh_token": tokens["refresh_token"],
        "client_id": registration["client_id"],
        "client_secret": registration.get("client_secret", ""),
    }
    results = await asyncio.gather(
        *(client.post("/mcp/token", data=payload) for _ in range(4)),
        return_exceptions=True,
    )
    ok = [r for r in results if not isinstance(r, Exception) and r.status_code == 200]
    assert len(ok) == 1, [
        r.status_code if not isinstance(r, Exception) else repr(r) for r in results
    ]


async def test_rotation_kills_the_old_access_token(client):
    """טוקן גישה ששרד רוטציה הוא בדיוק מה שהיא נועדה למנוע."""
    registration, code, verifier = await _grant(client)
    first = (await _exchange(client, registration, code, verifier)).json()

    rotated = await client.post(
        "/mcp/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": first["refresh_token"],
            "client_id": registration["client_id"],
            "client_secret": registration.get("client_secret", ""),
        },
    )
    assert rotated.status_code == 200, rotated.text

    stale = await client.post(
        "/mcp/",
        json=_rpc(7, "tools/call", {"name": "mdocs_map", "arguments": {}}),
        headers=dict(GOOD, Authorization=f"Bearer {first['access_token']}"),
    )
    assert stale.status_code == 401, "טוקן הגישה הישן היה אמור למות עם הרוטציה"


async def test_revoking_kills_both_sides_of_the_grant(client):
    """ביטול שמשאיר את הצד השני בחיים אינו ביטול."""
    registration, code, verifier = await _grant(client)
    tokens = (await _exchange(client, registration, code, verifier)).json()

    revoked = await client.post(
        "/mcp/revoke",
        data={
            "token": tokens["refresh_token"],
            "client_id": registration["client_id"],
            "client_secret": registration.get("client_secret", ""),
        },
    )
    assert revoked.status_code == 200, revoked.text

    call = await client.post(
        "/mcp/",
        json=_rpc(8, "tools/call", {"name": "mdocs_map", "arguments": {}}),
        headers=dict(GOOD, Authorization=f"Bearer {tokens['access_token']}"),
    )
    assert call.status_code == 401, "ביטול הרענון היה אמור להרוג גם את הגישה"


async def test_the_consent_screen_names_the_requesting_client(client):
    """המשתמש חייב לדעת מי מבקש גישה, לא רק שמישהו מבקש.

    זו השאלה הראשונה שמסך אישור אמור לענות עליה. השם מגיע מהרישום,
    כלומר מהלקוח עצמו, ולכן הוא מוצג כטקסט אחרי escape וחיתוך — לעולם
    לא כקישור.
    """
    response = await client.post(
        "/mcp/register",
        json={
            "client_name": "Claude מבית Anthropic",
            "redirect_uris": [REDIRECT],
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "client_secret_post",
        },
    )
    registration = response.json()
    _, challenge = _pkce()
    authorize = await client.get(
        "/mcp/authorize",
        params={
            "client_id": registration["client_id"],
            "redirect_uri": REDIRECT,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
    )
    await _login(client)
    form = await client.get(authorize.headers["location"])
    assert form.status_code == 200
    assert "Claude מבית Anthropic" in form.text


async def test_a_hostile_client_name_cannot_inject_markup(client):
    """שם הלקוח הוא קלט לא מהימן — רישום פתוח לכל דורש."""
    response = await client.post(
        "/mcp/register",
        json={
            "client_name": "<script>alert(1)</script>",
            "redirect_uris": [REDIRECT],
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "client_secret_post",
        },
    )
    registration = response.json()
    _, challenge = _pkce()
    authorize = await client.get(
        "/mcp/authorize",
        params={
            "client_id": registration["client_id"],
            "redirect_uri": REDIRECT,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
    )
    await _login(client)
    form = await client.get(authorize.headers["location"])
    assert "<script>alert(1)</script>" not in form.text
    assert "&lt;script&gt;" in form.text


# ── נתיבי OAuth בשורש הדומיין ─────────────────────────────────────────
#
# claude.ai מתעלם מ-registration_endpoint שבמטא-דאטה ושולח את הרישום
# ל-<host>/register. בלוגים של הפרודקשן זה נראה כ-404 חוזר, והחיבור
# נכשל עם "Couldn't register with the sign-in service".


async def test_metadata_points_at_the_host_root(client):
    """ה-issuer הוא השורש, ולכן כל נתיבי הזרימה מוצהרים משם."""
    meta = (await client.get("/.well-known/oauth-authorization-server")).json()

    assert meta["issuer"].rstrip("/") == MCP_ISSUER
    assert meta["authorization_endpoint"] == f"{MCP_ISSUER}/authorize"
    assert meta["token_endpoint"] == f"{MCP_ISSUER}/token"
    assert meta["registration_endpoint"] == f"{MCP_ISSUER}/register"
    assert meta["revocation_endpoint"] == f"{MCP_ISSUER}/revoke"

    # המשאב עצמו נשאר תחת /mcp — הוא אינו שרת ההרשאות.
    prm = await client.get("/.well-known/oauth-protected-resource/mcp")
    assert prm.status_code == 200
    assert prm.json()["resource"] == f"{MCP_ISSUER}/mcp"


@pytest.mark.parametrize("path", ["/authorize", "/token", "/register", "/revoke"])
async def test_every_declared_endpoint_exists_at_the_root(client, path):
    """כל נתיב שהמטא-דאטה מצהירה עליו חייב להתקיים.

    הכשל בפרודקשן היה בדיוק פער כזה: המטא-דאטה הצהירה על נתיב אחד
    והלקוח פנה לאחר. בדיקה פר-נתיב תופסת נתיב שנשמט מהרשימה
    ב-_mount_oauth_routes, גם אם הזרימה המלאה במקרה לא עוברת דרכו.

    405 ו-401 נחשבים קיימים — הבדיקה היא על הניתוב, לא על התוכן.
    404 הוא הכשל היחיד שמעניין כאן.
    """
    response = await client.post(path, data={})
    assert response.status_code != 404, f"{path} אינו קיים בשורש"


async def test_registration_works_at_the_root(client):
    """זו הבקשה שנכשלה ב-404 בפרודקשן."""
    response = await client.post(
        "/register",
        json={
            "client_name": "בדיקה",
            "redirect_uris": [REDIRECT],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "client_secret_post",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["client_id"]


async def test_the_whole_flow_runs_through_the_root_paths(client, clean_projects):
    """אותה זרימה מלאה, אבל דרך הנתיבים שבשורש בלבד.

    זו הזרימה ש-claude.ai מריץ בפועל. הבדיקה הקיימת עוברת דרך /mcp/*
    ולכן היא לא הייתה תופסת את הכשל.
    """
    registration = (
        await client.post(
            "/register",
            json={
                "client_name": "בדיקה",
                "redirect_uris": [REDIRECT],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "client_secret_post",
            },
        )
    ).json()
    verifier, challenge = _pkce()

    authorize = await client.get(
        "/authorize",
        params={
            "client_id": registration["client_id"],
            "redirect_uri": REDIRECT,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
    )
    assert authorize.status_code in (302, 303, 307), authorize.text

    txn = parse_qs(urlparse(authorize.headers["location"]).query)["txn"][0]
    await _login(client)
    approved = await client.post(
        "/mcp-consent", data={"txn": txn, "decision": "allow"}, headers=WRITE
    )
    code = parse_qs(urlparse(approved.headers["location"]).query)["code"][0]

    tokens = await client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT,
            "client_id": registration["client_id"],
            "client_secret": registration.get("client_secret", ""),
            "code_verifier": verifier,
        },
    )
    assert tokens.status_code == 200, tokens.text
    access = tokens.json()["access_token"]

    await make_project(client, slug="docs", name="תיעוד")
    await make_document(client, project="docs", title="התקנה")

    # והקריאה עצמה, על הנתיב בלי הלוכסן — בדיוק כמו ב-connector.
    call = await client.post(
        "/mcp",
        json=_rpc(20, "tools/call", {"name": "mdocs_map", "arguments": {}}),
        headers=dict(GOOD, Authorization=f"Bearer {access}"),
    )
    assert call.status_code == 200, call.text
    output = _tool_output(call)
    assert output["ok"] is True, output
    assert output["document_count"] == 1


# ── ה-CSP של מסך האישור ───────────────────────────────────────────────
#
# הבאג שהחלק הזה נולד ממנו: ה-CSP הגלובלי מכריז ``form-action 'none'``,
# כי האפליקציה היא SPA בלי טפסי HTML. מסך האישור הוא הטופס היחיד
# במערכת, ולכן הדפדפן חסם את הלחיצה על "אישור" — בשקט, בלי שגיאה
# גלויה ובלי בקשת רשת. מבחוץ זה נראה ככפתור מת.
#
# אף בדיקה קיימת לא תפסה את זה, כי httpx אינו אוכף CSP: הן שלחו POST
# ישירות לנתיב במקום להגיש את הטופס שבדף. הבדיקות כאן קוראות את ה-HTML
# עצמו ומוודאות שההצהרה מתירה את מה שהדף באמת עושה.


def _csp_directive(header: str, name: str) -> list[str]:
    """מחלץ הנחיה אחת מכותרת CSP. מחזיר רשימה ריקה אם היא לא הוצהרה."""
    for chunk in header.split(";"):
        parts = chunk.split()
        if parts and parts[0].lower() == name:
            return parts[1:]
    return []


def _origin(url: str) -> str:
    """scheme://host[:port] — מה ש-form-action מצפה לו."""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


async def _consent_page(client, redirect: str = REDIRECT) -> tuple[str, str]:
    """מגיע למסך האישור המחובר ומחזיר (HTML, כותרת ה-CSP)."""
    registration = (
        await client.post(
            "/register",
            json={
                "client_name": "בדיקת CSP",
                "redirect_uris": [redirect],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "client_secret_post",
            },
        )
    ).json()
    _, challenge = _pkce()
    authorize = await client.get(
        "/authorize",
        params={
            "client_id": registration["client_id"],
            "redirect_uri": redirect,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        },
    )
    await _login(client)
    page = await client.get(authorize.headers["location"])
    assert page.status_code == 200, page.text
    return page.text, page.headers.get("content-security-policy", "")


async def test_the_consent_page_allows_its_own_form_to_be_submitted(client):
    """הבדיקה השורשית: ההצהרה נגזרת מה-HTML, לא מרשימה קבועה.

    כל ``action`` שמופיע בדף חייב להיות מותר ב-``form-action``. אם ייווסף
    בעתיד טופס נוסף, או ישתנה היעד של הקיים, הבדיקה תיפול מעצמה — במקום
    שהכפתור ימות בשקט אצל המשתמש.
    """
    html, csp = await _consent_page(client)
    actions = re.findall(r"<form[^>]*\baction=\"([^\"]*)\"", html)
    assert actions, "מסך האישור אמור להכיל טופס"

    allowed = _csp_directive(csp, "form-action")
    assert allowed, "דף עם טופס חייב להצהיר form-action משלו"
    assert "'none'" not in allowed, "form-action 'none' חוסם את הטופס שבדף עצמו"

    for action in actions:
        # יעד יחסי נשלח לאותו מקור, ולכן 'self' הוא מה שמתיר אותו.
        assert action.startswith("/"), f"יעד לא צפוי בטופס: {action}"
        assert "'self'" in allowed, f"{action} נשלח ל-origin שלנו ו-'self' חסר"

    # וגם הכיוון ההפוך: ההיתר מוצהר במלואו ואין בו מקור עודף. בלי זה
    # הבדיקה הייתה עוברת גם על form-action *, שהוא הרפיה גמורה.
    assert set(allowed) == {"'self'", _origin(REDIRECT)}, f"מקורות עודפים ב-form-action: {allowed}"


async def test_the_consent_csp_allows_the_redirect_target(client):
    """ההפניה אחרי האישור יוצאת ל-origin של הלקוח.

    חלק מהדפדפנים אוכפים ``form-action`` גם על ההפניה שנובעת משליחת
    הטופס, ולכן ``'self'`` לבדו אינו מספיק: בלי מקור ההפניה, האישור היה
    מצליח בשרת והמשתמש היה נתקע על מסך ריק.
    """
    _, csp = await _consent_page(client)
    assert _origin(REDIRECT) in _csp_directive(csp, "form-action")


# שתי הבדיקות הבאות חולקות שורש אחד: **דפדפן שנתקל ב-source-expression
# לא חוקי עלול לפסול את ההנחיה כולה.** ואז form-action נעלם, הטופס נחסם,
# והמשתמש חוזר בדיוק לכפתור המת שהקוד הזה בא לתקן.
#
# הרישום פתוח לכל דורש, ולכן שתי הכתובות שנבדקות כאן הן קלט אפשרי ולא
# תרחיש תיאורטי: ה-SDK מאמת שה-redirect_uri תואם למה שנרשם — לא שהוא
# ניתן לביטוי ב-CSP.


async def test_a_scheme_that_cannot_be_expressed_is_left_out(client):
    """``myapp://`` אינו ניתן לביטוי, ולכן הוא מושמט ולא נדחף פגום.

    הדף נשאר עם ``'self'`` בלבד — מספיק לטופס עצמו, שנשלח לנתיב שלנו.
    """
    _, csp = await _consent_page(client, redirect="myapp://callback")
    assert _csp_directive(csp, "form-action") == ["'self'"]


async def test_credentials_are_stripped_from_the_origin(client):
    """פרטי הזדהות בכתובת נחתכים, וההפניה עצמה נשארת מותרת.

    ``https://user:pass@host`` אינו source-expression חוקי, אבל המקור
    שמאחוריו כן — והוא גם לאן שהדפדפן באמת ינווט, שכן הוא מתעלם
    מפרטי ההזדהות בניווט. לכן חיתוך מדויק יותר מהשמטה: הוא משאיר את
    ההיתר תואם ליעד בפועל, במקום להסתמך על ``'self'`` שאינו מכסה אותו.
    """
    _, csp = await _consent_page(client, redirect="https://user:pass@example.com/cb")
    allowed = _csp_directive(csp, "form-action")

    assert allowed == ["'self'", "https://example.com"]
    assert not any("pass" in source or "@" in source for source in allowed), (
        f"פרטי הזדהות דלפו להנחיה: {allowed}"
    )


async def test_the_consent_csp_stays_stricter_than_the_global_one(client):
    """ההיתר לטופס אינו נפתח לשום כיוון אחר.

    הדף עצמאי — בלי JS, בלי תמונות, בלי מקורות חיצוניים — ולכן ההצהרה
    שלו צריכה להיות מחמירה מזו של האפליקציה, ולא רק שונה ממנה.
    """
    _, csp = await _consent_page(client)
    assert _csp_directive(csp, "default-src") == ["'none'"]
    assert _csp_directive(csp, "frame-ancestors") == ["'none'"]
    assert _csp_directive(csp, "base-uri") == ["'none'"]
    # העיצוב inline, ולכן ההיתר נדרש — אבל רק הוא. מקור חיצוני שיתווסף
    # לדף בעתיד ייפול כאן, ולא יעבור בשקט.
    assert _csp_directive(csp, "style-src") == ["'unsafe-inline'"]
    assert not _csp_directive(csp, "script-src"), "אין סקריפטים בדף, ולכן אין מה להתיר"


async def test_pages_without_a_form_keep_the_global_policy(client):
    """דף הודעה אינו שולח כלום, ולכן אינו מקבל היתר.

    ההרפיה ניתנת לדף שיש בו טופס בלבד. דף שגיאה שמקבל אותה "ליתר ביטחון"
    מרחיב את המשטח בלי סיבה.
    """
    expired = await client.get("/mcp-consent", params={"txn": "not-a-real-txn"})
    assert expired.status_code == 400
    assert "form-action 'none'" in expired.headers.get("content-security-policy", "")


@pytest.mark.parametrize(
    ("redirect_uri", "expected"),
    [
        ("https://claude.ai/api/mcp/auth_callback", "https://claude.ai"),
        ("http://127.0.0.1:8765/cb", "http://127.0.0.1:8765"),
        ("https://user:pass@example.com/cb", "https://example.com"),
        ("http://[::1]:9000/cb", ""),
        ("https://xn--4dbrk0ce.example/cb", "https://xn--4dbrk0ce.example"),
        ("https://ישראל.example/cb", ""),
        ("myapp://callback", ""),
        ("https:///cb", ""),
        ("http://host:notaport/cb", ""),
        ("", ""),
    ],
    ids=[
        "רגיל",
        "עם-פורט",
        "פרטי-הזדהות",
        "IPv6",
        "IDN-מקודד",
        "IDN-גולמי",
        "scheme-זר",
        "בלי-host",
        "פורט-פגום",
        "ריק",
    ],
)
def test_the_origin_is_built_only_from_parts_csp_can_express(redirect_uri, expected):
    """בדיקת יחידה על הפונקציה עצמה, כדי לכסות את הענפים בלי לרוץ זרימה.

    ``host-char`` בדקדוק של CSP הוא ALPHA / DIGIT / "-" בלבד, ולכן
    **IPv6 אינו ניתן לביטוי כלל** — לא בסוגריים ולא בלעדיהן. אותו כלל
    פוסל דומיין לא-ASCII שלא קודד ל-punycode, ומזה נובע גם שאי אפשר
    להזריק ``;`` או רווח לכותרת דרך הערך הזה.
    """
    assert oauth_consent._redirect_origin(redirect_uri) == expected
