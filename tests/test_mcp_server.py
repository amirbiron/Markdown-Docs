"""שרת ה-MCP דרך הפרוטוקול עצמו.

הבדיקות כאן מדברות JSON-RPC מול הנתיב המורכב, ולא קוראות ל-handlers
ישירות — כי מה שנשבר בהרכבה נשבר בדיוק כאן: ה-lifespan של מנהל
הסשנים, זיהוי הכלים, והאימות.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
# קבועים ופונקציות עזר בלבד — לא fixtures. ראו conftest.
from tests.conftest import (
    GOOD,
    MCP_TOKEN,
    _payload,
    _rpc,
    _tool_output,
    make_document,
    make_project,
)

READ_TOOLS = {
    "mdocs_map",
    "mdocs_search",
    "mdocs_get_document",
    "mdocs_list_versions",
    "mdocs_get_version",
}

WRITE_TOOLS = {
    "mdocs_create_document",
    "mdocs_update_document",
    "mdocs_append_document",
}

EXPECTED_TOOLS = READ_TOOLS | WRITE_TOOLS


@pytest.fixture
async def mcp(mcp_lifespan, seeded_admin):
    """לקוח שכבר ביצע initialize מול השרת המורכב."""
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/mcp/",
            json=_rpc(
                1,
                "initialize",
                {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "tests", "version": "1"},
                },
            ),
            headers=GOOD,
        )
        yield client


# ── ההרכבה עצמה ───────────────────────────────────────────────────────


async def test_mount_is_alive(mcp):
    """אם ה-lifespan לא שורשר, כאן נופל task group was not initialized."""
    response = await mcp.post("/mcp/", json=_rpc(2, "tools/list"), headers=GOOD)
    assert response.status_code == 200
    names = {tool["name"] for tool in _payload(response)["result"]["tools"]}
    assert names == EXPECTED_TOOLS


async def test_tools_declare_their_true_nature_over_the_wire(mcp):
    """ה-annotations כפי שהלקוח באמת רואה אותן.

    הבדיקה כאן ולא רק מול האובייקטים בפייתון, כי לקוח מקבל JSON —
    וכלי כותב שמוצהר read-only מטעה כל לקוח שמסתמך על ההצהרה הזו
    כדי להחליט אם לבקש אישור מהמשתמש.
    """
    response = await mcp.post("/mcp/", json=_rpc(2, "tools/list"), headers=GOOD)
    for tool in _payload(response)["result"]["tools"]:
        annotations = tool.get("annotations") or {}
        expected = tool["name"] in READ_TOOLS
        assert annotations.get("readOnlyHint") is expected, tool["name"]


async def test_the_bare_path_challenges_instead_of_redirecting(mcp):
    """הנתיב ללא הלוכסן הנגרר חייב לענות בעצמו, לא בהפניה.

    זו הכתובת שמדביקים ב-connector, וכך גם כתוב בתיעוד. הפניה 307
    אינה נושאת WWW-Authenticate — היא לא תשובת אימות — ולכן לקוח
    שקורא את האתגר מהתשובה הראשונה לא מגלה שנדרש אימות בכלל, ומדווח
    "אין כלים" בלי להסביר למה. זה מה שקרה בפועל מול claude.ai.
    """
    anonymous = {k: v for k, v in GOOD.items() if k != "Authorization"}
    response = await mcp.post("/mcp", json=_rpc(10, "tools/list"), headers=anonymous)

    assert response.status_code == 401, response.text
    assert "www-authenticate" in response.headers, "האתגר נעלם בהפניה"
    assert "/.well-known/oauth-protected-resource/mcp" in response.headers["www-authenticate"]


async def test_the_bare_path_serves_tools_with_a_valid_token(mcp):
    """והצד השני: אותו נתיב חייב לעבוד עם טוקן תקף.

    מיחזור ה-endpoint הפנימי לבדו החזיר את ה-401 הנכון אבל דחה גם
    טוקן תקף, כי שכבת האימות של תת-האפליקציה לא רצה עליו. הבדיקה הזו
    היא מה שתופס את זה.
    """
    response = await mcp.post("/mcp", json=_rpc(11, "tools/list"), headers=GOOD)

    assert response.status_code == 200, response.text
    assert {t["name"] for t in _payload(response)["result"]["tools"]} == EXPECTED_TOOLS


async def test_mount_path_without_trailing_slash_still_reaches_the_server(mcp):
    """/mcp בלי לוכסן — כך לקוחות מגדירים את הכתובת בפועל.

    ההרכבה מייצרת הפניה 307 ל-/mcp/. הבדיקה מוודאת שההפניה קיימת
    ושהיא שומרת על POST ועל הגוף; 301/302 היו הופכים אותו ל-GET
    והלקוח היה מקבל 405 בלי הסבר.
    """
    response = await mcp.post(
        "/mcp", json=_rpc(7, "tools/list"), headers=GOOD, follow_redirects=True
    )
    assert response.status_code == 200, response.text
    assert {t["name"] for t in _payload(response)["result"]["tools"]} == EXPECTED_TOOLS


async def test_existing_app_still_works_after_mounting(mcp, owner):
    """רגרסיה: שרשור ה-lifespan הוא בדיוק המקום שבו הגיבוי נשבר בשקט.

    הגיבוי נבדק עם לקוח מחובר ומצפה ל-200 מדויק. קבלת 401 גם כן
    הייתה עוברת גם אילו המסלול היה שבור לגמרי, כלומר לא בודקת כלום.
    """
    assert (await mcp.get("/api/health")).status_code == 200
    backup = await owner.get("/api/backup")
    assert backup.status_code == 200, backup.text


# ── אימות ─────────────────────────────────────────────────────────────


async def test_valid_token_reaches_the_tool(mcp, owner):
    await make_project(owner, slug="docs", name="תיעוד")
    await make_document(owner, project="docs", title="התקנה")

    response = await mcp.post(
        "/mcp/", json=_rpc(3, "tools/call", {"name": "mdocs_map", "arguments": {}}), headers=GOOD
    )
    output = _tool_output(response)
    assert output["ok"] is True
    assert output["document_count"] == 1


@pytest.mark.parametrize(
    "headers",
    [
        dict(GOOD, Authorization="Bearer wrong-token"),
        dict(GOOD, Authorization="Basic " + MCP_TOKEN),
        {k: v for k, v in GOOD.items() if k != "Authorization"},
    ],
    ids=["טוקן שגוי", "סכמה שגויה", "בלי כותרת"],
)
async def test_bad_credentials_are_rejected(mcp, headers):
    """הדחייה היא 401 ברמת התחבורה, ולא שגיאה בתוך תשובה מוצלחת.

    זה לא פרט טכני. כשהתשובה הייתה 200 עם ``{"ok": false}`` בגוף,
    הלקוח ראה בקשה שהצליחה ולא ידע שנדרש אימות בכלל — וזו בדיוק
    הסיבה ש-claude.ai הציג "מחובר" על חיבור שכל קריאה בו נכשלה.
    """
    response = await mcp.post(
        "/mcp/", json=_rpc(4, "tools/call", {"name": "mdocs_map", "arguments": {}}), headers=headers
    )
    assert response.status_code == 401, response.text
    assert response.json()["error"] == "invalid_token"


async def test_the_challenge_points_at_the_metadata(mcp):
    """הכותרת שמפעילה את זרימת ה-OAuth בצד הלקוח.

    בלי resource_metadata בכותרת, לקוח שאין לו טוקן אינו יודע לאן
    ללכת כדי להשיג אחד, ופשוט נכשל.
    """
    response = await mcp.post(
        "/mcp/", json=_rpc(9, "tools/list"), headers={k: v for k, v in GOOD.items() if k != "Authorization"}
    )
    assert response.status_code == 401, response.text
    challenge = response.headers["www-authenticate"]
    assert challenge.startswith("Bearer ")
    assert '.well-known/oauth-protected-resource/mcp' in challenge


async def test_session_cookie_is_not_an_identity(mcp, owner):
    """חור ה-CSRF, ברמת הפרוטוקול.

    /mcp אינו תחת /api ולכן OriginGuard אינו חל עליו. cookie תקף
    לחלוטין — owner כבר התחבר — ובכל זאת חייב להידחות.
    """
    cookie = owner.cookies.get("mdocs_session")
    assert cookie, "ה-fixture אמור להיות מחובר"

    headers = {k: v for k, v in GOOD.items() if k != "Authorization"}
    headers["Cookie"] = f"mdocs_session={cookie}"

    response = await mcp.post(
        "/mcp/", json=_rpc(5, "tools/call", {"name": "mdocs_map", "arguments": {}}), headers=headers
    )
    assert response.status_code == 401, response.text


# ── תוכן ──────────────────────────────────────────────────────────────


async def test_search_with_content_is_one_round_trip(mcp, owner):
    await make_project(owner)
    await make_document(owner, title="התקנה", content="השלב הראשון")

    response = await mcp.post(
        "/mcp/",
        json=_rpc(
            6,
            "tools/call",
            {"name": "mdocs_search", "arguments": {"query": "התקנה", "include_content": True}},
        ),
        headers=GOOD,
    )
    output = _tool_output(response)
    assert output["ok"] is True
    assert output["results"][0]["content"] == "השלב הראשון"
