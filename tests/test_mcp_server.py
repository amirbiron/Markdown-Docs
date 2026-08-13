"""שרת ה-MCP דרך הפרוטוקול עצמו.

הבדיקות כאן מדברות JSON-RPC מול הנתיב המורכב, ולא קוראות ל-handlers
ישירות — כי מה שנשבר בהרכבה נשבר בדיוק כאן: ה-lifespan של מנהל
הסשנים, זיהוי הכלים, והאימות.
"""

from __future__ import annotations

import asyncio
import json
import re

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.conftest import (  # noqa: F401
    MCP_TOKEN,
    WRITE,
    clean_projects,
    make_document,
    make_project,
    owner,
    seeded_admin,
)

GOOD = {
    "Authorization": f"Bearer {MCP_TOKEN}",
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}

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
    done, _ = await asyncio.wait({waiter, task}, return_when=asyncio.FIRST_COMPLETED)
    if waiter not in done:
        waiter.cancel()
        await task  # מרים את החריגה המקורית
        raise RuntimeError("ה-lifespan הסתיים בלי לאותת על עלייה")

    yield
    stopping.set()
    await task


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
    response = await mcp.post(
        "/mcp/", json=_rpc(4, "tools/call", {"name": "mdocs_map", "arguments": {}}), headers=headers
    )
    assert _tool_output(response)["error"] == "unauthorized"


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
    assert _tool_output(response)["error"] == "unauthorized"


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
