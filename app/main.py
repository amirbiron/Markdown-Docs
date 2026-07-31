"""נקודת הכניסה של השרת.

השרת מגיש גם את ה-API תחת /api וגם את הפרונט הסטטי מהשורש, מאותו origin.
זה מה שמייתר CORS ומאפשר ל-cookie להיות SameSite=Lax בלי סיבוכים.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import engine, get_session

# בלי הגדרת רמה מפורשת, logger.info לא מגיע לפלט של uvicorn — ובדיקת
# הקידוד בעלייה הייתה רצה ולא מדווחת כלום, כלומר לא שווה כלום.
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:     %(message)s",
)
logger = logging.getLogger("markdown_docs")
settings = get_settings()

STATIC_ROOT = "."
INDEX_FILE = "index.html"

# אותן כותרות שהיו ב-render.yaml כשהאתר היה סטטי. ברגע שהוא הפך לשירות
# web, Render לא מזריק אותן יותר — הן חייבות לצאת מהאפליקציה, אחרת הן
# פשוט נעלמות בלי שאף אחד ישים לב.
#
# 'unsafe-inline' ו-'unsafe-eval' נדרשים בפועל: ה-dc-runtime מריץ את
# לוגיקת הרכיב דרך new Function, וכל העיצוב הוא inline styles. המשמעות
# היא שה-CSP הזה מגביל מאיפה נטען קוד ולאן אפשר לפנות — הוא לא שכבת
# הגנה מפני XSS.
CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://unpkg.com https://cdn.jsdelivr.net",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com",
        "img-src 'self' data:",
        "connect-src 'self' https://cdn.jsdelivr.net https://unpkg.com",
        "base-uri 'self'",
        "form-action 'none'",
        "frame-ancestors 'none'",
        "object-src 'none'",
    ]
)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), interest-cohort=()",
    "Content-Security-Policy": CSP,
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """בדיקת שפיות על הקידוד בעלייה.

    server_encoding שאינו UTF8 משחית עברית, והתיקון דורש מיגרציית נתונים.
    עדיף להיכשל ברעש בעלייה מאשר לגלות את זה משורה שנשמרה עקום.
    """
    try:
        async with engine.connect() as conn:
            encoding = (await conn.execute(text("SHOW server_encoding"))).scalar_one()
            timezone = (await conn.execute(text("SHOW timezone"))).scalar_one()
        if encoding.upper() != "UTF8":
            logger.error("server_encoding הוא %s ולא UTF8 — טקסט עברי ישתבש", encoding)
        logger.info("בסיס הנתונים מחובר (encoding=%s, timezone=%s)", encoding, timezone)
    except Exception:
        # השרת עולה גם בלי DB, כדי ש-/api/health יוכל לדווח על התקלה
        # במקום שהתהליך פשוט ימות ו-Render יראה קריסה בלי סיבה.
        logger.exception("החיבור לבסיס הנתונים נכשל בעלייה")
    yield
    await engine.dispose()


app = FastAPI(title="Markdown Docs", lifespan=lifespan, docs_url=None, redoc_url=None)


@app.middleware("http")
async def security_headers(request: Request, call_next) -> Response:
    response = await call_next(request)
    for header, value in SECURITY_HEADERS.items():
        response.headers.setdefault(header, value)
    return response


api = APIRouter(prefix="/api")


@api.get("/health")
async def health() -> dict[str, str]:
    """בדיקה רדודה — האם התהליך חי.

    זו הבדיקה ש-Render מפנה אליה. היא לא נוגעת ב-DB בכוונה: תקלת DB
    חולפת הייתה גורמת ל-Render להפעיל מחדש את השירות בלולאה, וזה מחמיר
    את התקלה במקום לפתור אותה. לבדיקה העמוקה יש נתיב נפרד.
    """
    return {"status": "ok"}


@api.get("/health/db")
async def health_db(session: AsyncSession = Depends(get_session)) -> Response:
    """בדיקה עמוקה — האם ה-DB עונה. מחזירה 503 כשלא."""
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        logger.exception("בדיקת בריאות ה-DB נכשלה")
        return JSONResponse({"status": "degraded", "database": "error"}, status_code=503)
    return JSONResponse({"status": "ok", "database": "ok"})


app.include_router(api)


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(INDEX_FILE)


# נרשם אחרון כדי שכל נתיב /api ייתפס לפני שהמאונט הסטטי רואה אותו.
app.mount("/", StaticFiles(directory=STATIC_ROOT, html=True), name="static")
