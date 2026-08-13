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
import secrets
from urllib.parse import parse_qs, urlparse

import pytest
from httpx import ASGITransport, AsyncClient

from app.db import SessionLocal
from app.main import app
from app.mcp import oauth_store as store
from tests.conftest import (  # noqa: F401
    EMAIL,
    MCP_ISSUER,
    MCP_TOKEN,
    ORIGIN,
    PASSWORD,
    WRITE,
    clean_projects,
    make_document,
    make_project,
    owner,
    seeded_admin,
)
from tests.test_mcp_server import GOOD, _rpc, _tool_output  # noqa: F401

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
    # ה-endpoints חייבים להצביע לאן שהם באמת נמצאים — הם יושבים בתוך
    # תת-האפליקציה, ולכן תחת /mcp. issuer שמצביע לשורש היה שולח את
    # הלקוח ל-/authorize שאינו קיים.
    assert meta["authorization_endpoint"] == f"{MCP_ISSUER}/mcp/authorize"
    assert meta["token_endpoint"] == f"{MCP_ISSUER}/mcp/token"
    assert meta["registration_endpoint"] == f"{MCP_ISSUER}/mcp/register"
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
    token_response = await client.post(
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
    tokens = (
        await client.post(
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
    ).json()

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
    tokens = (
        await client.post(
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
    ).json()

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
    first = (
        await client.post(
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
    ).json()

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
    tokens = (
        await client.post(
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
    ).json()

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
