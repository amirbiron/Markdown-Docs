"""רישום הכלים והרכבת השרת.

הקובץ הזה הוא חיווט בלבד: כל כלי פותח סשן, מאמת, ומעביר להנדלר.
הלוגיקה יושבת ב-app/mcp/handlers.py והאכיפה ב-app/services.
"""

from __future__ import annotations

import logging

from mcp.server.mcpserver import MCPServer

# מ-mcpserver.context ולא מ-server.context: יש שתי מחלקות בשם Context
# ב-SDK, וזו שהכלים מקבלים היא זו. ייבוא של השנייה נראה תקין לגמרי עד
# שהרישום מנסה לבנות סכמת JSON לפרומטר ונופל.
from mcp.server.mcpserver.context import Context
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations

from app.config import get_settings
from app.db import SessionLocal
from app.mcp import handlers
from app.mcp.auth import (
    AuthError,
    PermissionError_,
    require_read,
    require_write,
    resolve_identity,
)
from app.mcp.formatting import err

logger = logging.getLogger("markdown_docs.mcp")

SERVER_NAME = "markdown-docs"

# קריאה בלבד: לא משנה מצב, בטוח לחזור עליה, ואינה יוצאת החוצה.
READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)

# יצירה: כל קריאה מוסיפה מסמך חדש, ולכן אינה אידמפוטנטית. אינה הורסת
# דבר קיים.
CREATE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
)

# עדכון: התוכן הקודם נשמר כגרסה ולכן אינו נהרס. שליחת אותו תוכן פעם
# שנייה אינה מייצרת גרסה נוספת, ולכן אידמפוטנטי.
UPDATE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
)

# הוספה בסוף: כל קריאה מאריכה את המסמך, ולכן בהחלט לא אידמפוטנטית.
APPEND = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
)

mcp = MCPServer(SERVER_NAME)


async def _run(ctx: Context, handler, *args, needs_write: bool = False, **kwargs) -> dict:
    """מעטפת אחידה: סשן, אימות, הרשאה, ותרגום חריגות.

    כל כלי עובר דרך כאן, כדי ששום כלי לא יוכל לשכוח את האימות. בדיקת
    הכתיבה נעשית לפני שנפתחת עבודה כלשהי — ל-SDK אין scopes פר-כלי,
    והרישום אינו הצהרת הרשאה.
    """
    async with SessionLocal() as session:
        try:
            identity = await resolve_identity(session, ctx.headers)
        except AuthError as error:
            return err("unauthorized", message=str(error))

        # גם כלי קריאה נבדק. אחרת ה-scopes נאכפים לכיוון אחד בלבד,
        # וטוקן בלי read בכלל קורא הכול.
        try:
            if needs_write:
                require_write(identity)
            else:
                require_read(identity)
        except PermissionError_ as error:
            return err("insufficient_scope", message=str(error))

        try:
            return await handler(session, identity, *args, **kwargs)
        except Exception:  # noqa: BLE001 — גבול חיצוני; פנימי לא נחשף
            logger.exception("כלי MCP נכשל: %s", getattr(handler, "__name__", "?"))
            return err("internal_error", message="הפעולה נכשלה. נסו שוב.")


# ── כלי קריאה ─────────────────────────────────────────────────────────


@mcp.tool(
    name="mdocs_map",
    title="מפת הפרויקטים והמסמכים",
    annotations=READ_ONLY,
)
async def mdocs_map(ctx: Context) -> dict:
    """מחזיר את כל הפרויקטים והמסמכים בקריאה אחת, בלי תוכן.

    התחילו מכאן. הקריאה הזו נותנת את כל המזהים, ה-slugים והכותרות,
    ולכן היא חוסכת סיבוב ביניים לפני שליפת מסמך.

    שימו לב: משתמש מזוהה רואה את הפרויקטים שלו בלבד — גם פומביים של
    אחרים אינם מופיעים. זו התנהגות המערכת ולא תקלה.
    """
    return await _run(ctx, handlers.map_documents)


@mcp.tool(
    name="mdocs_search",
    title="חיפוש במסמכים",
    annotations=READ_ONLY,
)
async def mdocs_search(
    ctx: Context,
    query: str,
    limit: int | None = None,
    include_content: bool = False,
    content_limit: int | None = None,
) -> dict:
    """חיפוש טקסט מלא בכל המסמכים, עם נפילה לחיפוש דמיון.

    העדיפו include_content=true כשאתם מתכוונים לקרוא את התוצאה — הוא
    מחזיר את התוכן המלא של התוצאות המובילות באותה קריאה, במקום סיבוב
    נוסף לכל מסמך.

    כל תוצאה נושאת `rank`. הסולם שונה בין match="text" ל-match="fuzzy",
    אבל בתוך תשובה אחת ההשוואה תקפה — הדמיון רץ רק כשחיפוש הטקסט לא
    החזיר כלום.

    כשאין תוצאות מוחזרות כותרות קיימות והצעות קרובות, כדי שאפשר יהיה
    לנסות שוב בלי לנחש.
    """
    return await _run(
        ctx,
        handlers.search,
        query,
        limit=limit,
        include_content=include_content,
        content_limit=content_limit,
    )


@mcp.tool(
    name="mdocs_get_document",
    title="שליפת מסמך",
    annotations=READ_ONLY,
)
async def mdocs_get_document(
    ctx: Context,
    document_id: str | None = None,
    project_slug: str | None = None,
    doc_slug: str | None = None,
    title: str | None = None,
) -> dict:
    """מחזיר את התוכן המלא של מסמך.

    העדיפו document_id. ה-slug אינו מזהה יציב: הוא משתנה בכל שינוי
    כותרת, ו-slug שהתפנה יכול להיתפס על ידי מסמך אחר — כלומר slug ישן
    עלול להחזיר מסמך שגוי בלי שום שגיאה.

    אפשר גם project_slug יחד עם doc_slug, או title. כותרת שמופיעה
    ביותר ממסמך אחד מחזירה את כל המועמדים עם המזהים שלהם.
    """
    return await _run(
        ctx,
        handlers.get_document,
        document_id=document_id,
        project_slug=project_slug,
        doc_slug=doc_slug,
        title=title,
    )


@mcp.tool(
    name="mdocs_list_versions",
    title="היסטוריית גרסאות",
    annotations=READ_ONLY,
)
async def mdocs_list_versions(ctx: Context, document_id: str) -> dict:
    """מחזיר את גרסאות המסמך, מהחדשה לישנה, בלי התוכן.

    גרסה נוצרת רק כשתוכן המסמך באמת משתנה. את התוכן עצמו שולפים
    עם mdocs_get_version.
    """
    return await _run(ctx, handlers.list_versions, document_id)


@mcp.tool(
    name="mdocs_get_version",
    title="תוכן של גרסה קודמת",
    annotations=READ_ONLY,
)
async def mdocs_get_version(ctx: Context, version_id: str) -> dict:
    """מחזיר את התוכן המלא של גרסה קודמת.

    שימושי כדי להשוות מה השתנה במסמך. את מזהי הגרסאות מקבלים
    מ-mdocs_list_versions.
    """
    return await _run(ctx, handlers.get_version, version_id)


# ── כלי כתיבה ─────────────────────────────────────────────────────────


@mcp.tool(
    name="mdocs_create_document",
    title="יצירת מסמך",
    annotations=CREATE,
)
async def mdocs_create_document(
    ctx: Context,
    project_slug: str,
    title: str,
    content: str = "",
    slug: str | None = None,
) -> dict:
    """יוצר מסמך חדש בפרויקט קיים.

    השמיטו את slug כדי שייגזר מהכותרת. את רשימת הפרויקטים מקבלים
    מ-mdocs_map.
    """
    return await _run(
        ctx,
        handlers.create_document,
        project_slug,
        title,
        content=content,
        slug=slug,
        needs_write=True,
    )


@mcp.tool(
    name="mdocs_update_document",
    title="עדכון מסמך",
    annotations=UPDATE,
)
async def mdocs_update_document(
    ctx: Context,
    document_id: str,
    content: str | None = None,
    title: str | None = None,
    new_slug: str | None = None,
) -> dict:
    """מחליף את תוכן המסמך ו/או את כותרתו.

    התוכן הקודם נשמר אוטומטית כגרסה, וניתן לשחזר אותו דרך
    mdocs_list_versions ו-mdocs_get_version.

    **ה-slug אינו משתנה** גם כשמשנים את הכותרת, אלא אם ביקשתם זאת
    במפורש ב-new_slug. זו התנהגות מכוונת: שינוי slug שובר כל קישור
    קיים למסמך.

    שולח את כל התוכן. להוספה בסוף העדיפו mdocs_append_document.
    """
    return await _run(
        ctx,
        handlers.update_document,
        document_id,
        content=content,
        title=title,
        new_slug=new_slug,
        needs_write=True,
    )


@mcp.tool(
    name="mdocs_append_document",
    title="הוספה בסוף מסמך",
    annotations=APPEND,
)
async def mdocs_append_document(ctx: Context, document_id: str, text: str) -> dict:
    """מוסיף טקסט בסוף מסמך קיים, בלי לשלוח את כולו מחדש.

    זו הדרך הנכונה לעדכן roadmap או יומן. שורה ריקה נוספת אוטומטית
    בין הקיים לחדש, כדי שההוספה לא תידבק לפסקה האחרונה.
    """
    return await _run(ctx, handlers.append_document, document_id, text, needs_write=True)


# ── הרכבה ─────────────────────────────────────────────────────────────


def build_asgi_app():
    """בונה את אפליקציית ה-ASGI של ה-MCP.

    streamable_http_path="/" ולא "/mcp": תת-האפליקציה ממפה את הנתיב
    בתוך עצמה, וההרכבה על "/mcp" בצד ההורה כבר מוסיפה את הקידומת.
    שני אלה יחד היו נותנים /mcp/mcp.

    stateless_http=True — אין מצב סשן בין בקשות. השירות רץ במופע יחיד
    עם דיסק קבוע, אבל חוסר מצב הוא ממילא הדבר הנכון: הוא שורד דיפלוי
    באמצע שיחה.

    הגנת DNS rebinding מכובה במפורש. היא נועדה לשרתים שרצים על
    localhost, שם דף עוין יכול לגרום לדפדפן לפנות אליהם; בשרת ציבורי
    היא רק מפילה כל בקשה ב-421 כי כותרת ה-Host היא הדומיין האמיתי
    ולא 127.0.0.1. ההגנה כאן היא הטוקן, לא רשימת מארחים.
    """
    return mcp.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        ),
    )


def is_enabled() -> bool:
    return get_settings().mcp_enabled
