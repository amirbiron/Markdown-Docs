"""מסך האישור של חיבור MCP.

זה החלק היחיד בזרימת ה-OAuth שה-SDK אינו מספק, ובצדק: הוא היחיד
שדורש להכיר את המשתמשים של האפליקציה ואת העיצוב שלה.

הנתיבים כאן יושבים **מחוץ** לתת-אפליקציית ה-MCP, כי הם דפי HTML רגילים
שנפתחים בדפדפן וזקוקים ל-cookie של ההתחברות. תת-האפליקציה, לעומת זאת,
מאמתת ב-Bearer בלבד ודוחה cookie במפורש.

הזרימה, מקצה לקצה:

1. claude.ai שולח את המשתמש ל-``/mcp/authorize``
2. ה-SDK מאמת את הבקשה וקורא ל-``provider.authorize``, שמחזיר הפניה לכאן
3. אם המשתמש אינו מחובר — מסך התחברות, ואז חזרה לכאן
4. המשתמש רואה מה מבקשים ומאשר
5. נוצר authorization code, והדפדפן מופנה חזרה ל-claude.ai
"""

from __future__ import annotations

import html
import uuid

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from mcp.server.auth.provider import construct_redirect_uri

from app.db import SessionLocal
from app.deps import optional_user
from app.mcp import oauth_store
from app.mcp.auth import SCOPE_WRITE
from app.mcp.oauth_provider import CONSENT_PATH, mint_code, open_txn
from app.models import User

router = APIRouter()

SCOPE_LABELS = {
    "read": "לקרוא את הפרויקטים והמסמכים שלך",
    "write": "ליצור מסמכים, לעדכן ולהוסיף להם תוכן",
}


def _page(title: str, body: str, *, status_code: int = 200) -> HTMLResponse:
    """עמוד עצמאי, בלי תלות ב-CSS של האפליקציה.

    הדף הזה נפתח בחלון קופץ שנשלט על ידי הלקוח, ולעיתים קרובות נסגר
    מיד אחרי האישור. טעינת גיליון סגנון חיצוני הייתה מוסיפה סיבוב רשת
    שלפעמים לא מספיק להסתיים, והמשתמש היה רואה טקסט לא מעוצב.
    """
    return HTMLResponse(
        status_code=status_code,
        content=f"""<!doctype html>
<html lang="he" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{html.escape(title)}</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{
    font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
    margin: 0; min-height: 100vh; display: grid; place-items: center;
    background: #f6f7f9; color: #1a1d21; padding: 1.5rem;
  }}
  .card {{
    background: #fff; border: 1px solid #e3e6ea; border-radius: 14px;
    padding: 2rem; max-width: 26rem; width: 100%;
    box-shadow: 0 1px 3px rgba(0,0,0,.06);
  }}
  h1 {{ font-size: 1.25rem; margin: 0 0 .75rem; }}
  p {{ line-height: 1.6; margin: 0 0 1rem; color: #3d444d; }}
  ul {{ margin: 0 0 1.5rem; padding-inline-start: 1.25rem; line-height: 1.9; }}
  .actions {{ display: flex; gap: .75rem; }}
  button, .btn {{
    font: inherit; padding: .6rem 1.2rem; border-radius: 8px;
    border: 1px solid transparent; cursor: pointer; text-decoration: none;
  }}
  .primary {{ background: #2f6feb; color: #fff; }}
  .secondary {{ background: #fff; color: #3d444d; border-color: #d0d5dc; }}
  code {{ background: #eef0f3; padding: .1rem .35rem; border-radius: 4px; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #15181c; color: #e6e9ed; }}
    .card {{ background: #1c2026; border-color: #2c3239; }}
    p {{ color: #aeb6c0; }}
    .secondary {{ background: #1c2026; color: #aeb6c0; border-color: #3a424b; }}
    code {{ background: #262c33; }}
  }}
</style>
</head>
<body><div class="card">{body}</div></body>
</html>""",
    )


# אורך מרבי לשם הלקוח בתצוגה. השם מגיע מהרישום, כלומר מהלקוח עצמו,
# ולכן הוא קלט לא מהימן: שם ארוך היה דוחף את כפתורי האישור מחוץ למסך
# ומשאיר משתמש שמאשר בלי לראות מה. חיתוך פשוט יותר בטוח מגלילה.
CLIENT_NAME_MAX = 60
UNNAMED_CLIENT = "לקוח חיצוני"


async def _client_label(txn: dict) -> str:
    """השם שיוצג במסך האישור.

    בלי זה המשתמש מאשר גישה למסמכים שלו בלי לדעת מי מבקש אותה — וזו
    השאלה הראשונה שמסך אישור אמור לענות עליה.

    השם מוצג כטקסט בלבד, אחרי escape ואחרי חיתוך. לעולם לא כקישור:
    הוא נשלט על ידי מי שנרשם, ורישום פתוח לכל דורש.
    """
    async with SessionLocal() as session:
        row = await oauth_store.load_client(session, txn["client_id"])

    name = ((row.registration if row else {}).get("client_name") or "").strip()
    if not name:
        return UNNAMED_CLIENT
    return name if len(name) <= CLIENT_NAME_MAX else name[:CLIENT_NAME_MAX] + "…"


def _expired() -> HTMLResponse:
    return _page(
        "הבקשה פגה",
        "<h1>הבקשה פגה</h1>"
        "<p>בקשת החיבור אינה תקפה יותר. חזרו ללקוח ונסו לחבר שוב.</p>",
        status_code=status.HTTP_400_BAD_REQUEST,
    )


# response_model=None: מחזיר או HTML או הפניה, ו-FastAPI מנסה לגזור
# מודל תשובה מהאיחוד הזה ונופל בזמן ייבוא.
@router.get(CONSENT_PATH, include_in_schema=False, response_model=None)
async def consent_form(
    request: Request, txn: str = "", user: User | None = Depends(optional_user)
) -> HTMLResponse | RedirectResponse:
    payload = open_txn(txn)
    if payload is None:
        return _expired()

    if user is None:
        # ההתחברות היא בעמוד הראשי (ניתוב hash בצד הלקוח), ולכן ה-next
        # נשמר כפרמטר שאיננו יכולים לקרוא בשרת. במקום לנחש, פשוט
        # מסבירים ומחזירים לכאן — העסקה החתומה שורדת את הסיבוב.
        return _page(
            "נדרשת התחברות",
            "<h1>נדרשת התחברות</h1>"
            "<p>כדי לאשר את החיבור צריך להיות מחובר לחשבון.</p>"
            f'<div class="actions"><a class="btn primary" href="/">התחברות</a>'
            f'<a class="btn secondary" href="{html.escape(request.url.path)}?txn={html.escape(txn)}">'
            "כבר התחברתי</a></div>",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    scopes = list(payload.get("scopes") or [])
    items = "".join(
        f"<li>{html.escape(SCOPE_LABELS.get(scope, scope))}</li>" for scope in scopes
    )
    writes = SCOPE_WRITE in scopes
    warning = (
        "<p>החיבור יוכל <strong>לשנות</strong> מסמכים. כל שינוי נשמר כגרסה "
        "וניתן לשחזור.</p>"
        if writes
        else ""
    )

    return _page(
        "אישור חיבור",
        f"<h1>לאשר חיבור ל-Markdown-Docs?</h1>"
        f"<p><strong>{html.escape(await _client_label(payload))}</strong> מבקש גישה "
        f"לחשבון <code>{html.escape(user.email)}</code>.</p>"
        f"<p>הוא יוכל:</p><ul>{items}</ul>"
        f"{warning}"
        f'<form method="post" action="{html.escape(CONSENT_PATH)}">'
        f'<input type="hidden" name="txn" value="{html.escape(txn)}">'
        f'<div class="actions">'
        f'<button class="primary" type="submit" name="decision" value="allow">אישור</button>'
        f'<button class="secondary" type="submit" name="decision" value="deny">ביטול</button>'
        f"</div></form>",
    )


@router.post(CONSENT_PATH, include_in_schema=False, response_model=None)
async def consent_submit(
    txn: str = Form(""),
    decision: str = Form("deny"),
    user: User | None = Depends(optional_user),
) -> HTMLResponse | RedirectResponse:
    payload = open_txn(txn)
    if payload is None:
        return _expired()

    if user is None:
        # לא מציגים שוב את הטופס: ה-POST הגיע בלי זהות, וזה או session
        # שפג באמצע או בקשה שנשלחה מבחוץ. שני המקרים נדחים.
        return _page(
            "נדרשת התחברות",
            "<h1>נדרשת התחברות</h1><p>ההתחברות פגה. התחברו ונסו שוב.</p>",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    redirect_uri = payload["redirect_uri"]
    state = payload.get("state")

    if decision != "allow":
        # סירוב מוחזר ללקוח כשגיאת פרוטוקול תקנית, ולא כדף שגיאה: כך
        # הלקוח יודע שהמשתמש בחר לבטל, במקום להיתקע בהמתנה.
        return RedirectResponse(
            construct_redirect_uri(
                redirect_uri, error="access_denied", error_description="המשתמש ביטל", state=state
            ),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    code = await mint_code(payload, uuid.UUID(str(user.id)))
    return RedirectResponse(
        construct_redirect_uri(redirect_uri, code=code, state=state),
        status_code=status.HTTP_303_SEE_OTHER,
    )
