"""נקודת הכניסה של השרת.

השרת מגיש גם את ה-API תחת /api וגם את הפרונט הסטטי מהשורש, מאותו origin.
זה מה שמייתר CORS ומאפשר ל-cookie להיות SameSite=Lax בלי סיבוכים.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.security import PasswordPolicyError
from app.db import SessionLocal, engine, get_session
from app.middleware import BodySizeLimit, OriginGuard
from app.routers import auth as auth_router
from app.routers import documents as documents_router
from app.routers import links as links_router
from app.routers import projects as projects_router
from app.routers import search as search_router
from app.seed import seed_admin

# בלי הגדרת רמה מפורשת, logger.info לא מגיע לפלט של uvicorn — ובדיקת
# הקידוד בעלייה הייתה רצה ולא מדווחת כלום, כלומר לא שווה כלום.
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:     %(message)s",
)
logger = logging.getLogger("markdown_docs")
settings = get_settings()

# רק תיקיית הנכסים מוגשת, ולא שורש הריפו. STATIC_ROOT="." עם mount על
# "/" הפך כל קובץ בפרויקט לנגיש — alembic.ini, app/config.py, .env.example
# והשאר. index.html מוגש בנתיב מפורש, ולכן אין צורך למאונט לראות אותו.
ASSETS_DIR = "assets"
INDEX_FILE = "index.html"

# אותן כותרות שהיו ב-render.yaml כשהאתר היה סטטי. ברגע שהוא הפך לשירות
# web, Render לא מזריק אותן יותר — הן חייבות לצאת מהאפליקציה, אחרת הן
# פשוט נעלמות בלי שאף אחד ישים לב.
#
# 'unsafe-inline' ו-'unsafe-eval' נדרשים בפועל: ה-dc-runtime מריץ את
# לוגיקת הרכיב דרך new Function, וכל העיצוב הוא inline styles. לכן ה-CSP
# הזה לא יעצור הזרקת קוד.
#
# מה שהוא כן עושה, ומאז שהספריות ירדו ל-assets/vendor הרבה יותר חזק:
# script-src כבר לא מכיל שום מקור חיצוני, ו-connect-src הוא 'self' בלבד.
# כלומר גם קוד שהצליח לרוץ בדף לא יכול למשוך קוד נוסף מבחוץ ולא יכול
# להבריח מידע החוצה. נשארו רק הגופנים, ואלה לא מריצים כלום.
CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' https://fonts.gstatic.com",
        "img-src 'self' data:",
        "connect-src 'self'",
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
    encoding = None
    try:
        async with engine.connect() as conn:
            encoding = (await conn.execute(text("SHOW server_encoding"))).scalar_one()
            timezone = (await conn.execute(text("SHOW timezone"))).scalar_one()
        logger.info("בסיס הנתונים מחובר (encoding=%s, timezone=%s)", encoding, timezone)
    except Exception:
        # תקלת חיבור היא זמנית: השרת עולה בכל זאת כדי ש-/api/health יוכל
        # לדווח עליה, במקום שהתהליך ימות ו-Render יראה קריסה בלי סיבה.
        logger.exception("החיבור לבסיס הנתונים נכשל בעלייה")

    # קידוד שגוי הוא סיפור אחר לגמרי: הוא לא חולף, הוא משחית כל טקסט עברי
    # שנשמר, והתיקון דורש מיגרציית נתונים. מפילים את העלייה במקום להמשיך
    # ולכתוב נתונים פגומים.
    if encoding is not None and encoding.upper() != "UTF8":
        raise RuntimeError(f"server_encoding הוא {encoding} ולא UTF8 — טקסט עברי יישמר משובש")

    try:
        async with SessionLocal() as session:
            await seed_admin(session)
    except PasswordPolicyError:
        # מדיניות סיסמה שנכשלה היא שגיאת קונפיגורציה, לא תקלה חולפת.
        # מפילים את העלייה במקום להמשיך בלי משתמש.
        raise
    except Exception:
        logger.exception("יצירת המשתמש הראשוני נכשלה")
    yield
    await engine.dispose()


app = FastAPI(title="Markdown Docs", lifespan=lifespan, docs_url=None, redoc_url=None)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """תשובה גנרית בעברית על קלט לא תקין.

    ברירת המחדל של FastAPI מחזירה את הקלט השגוי בחזרה בתוך גוף התשובה.
    שני דברים רעים נובעים מזה. האחד הוא כלל 3 — התשובה מחזירה שמות שדות
    פנימיים ואת ההודעות הטכניות באנגלית של pydantic. השני מפתיע יותר:
    קלט כמו {"position": NaN} עובר את הפענוח של json, נדחה על ידי
    pydantic, ואז *נכשל בסריאליזציה חזרה* — כי NaN אינו JSON חוקי. כלומר
    בקשה שגויה הייתה מחזירה 500 במקום 422.

    שמות השדות שנכשלו כן מוחזרים, כי בלעדיהם אי אפשר לתקן את הבקשה.
    הערכים עצמם לא.
    """
    fields = sorted({".".join(str(part) for part in error["loc"][1:]) for error in exc.errors()})
    logger.info("קלט לא תקין ב-%s: %s", request.url.path, fields)
    return JSONResponse(
        {"detail": "הנתונים שנשלחו אינם תקינים", "fields": fields},
        status_code=422,
    )


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


api.include_router(auth_router.router)
api.include_router(projects_router.router)
api.include_router(documents_router.router)
api.include_router(links_router.router)
api.include_router(search_router.router)
app.include_router(api)

# הסדר כאן הוא מה שקובע מה רץ קודם: מה שנוסף אחרון עוטף את הכול.
# OriginGuard חייב לרוץ לפני BodySizeLimit — אחרת בקשה עוינת עם גוף של
# מגה־בייט הייתה נקראת במלואה לפני שנגלה שהמקור שלה פסול.
app.add_middleware(BodySizeLimit, max_bytes=settings.max_body_bytes)
app.add_middleware(OriginGuard, allowlist=settings.origin_allowlist)


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(INDEX_FILE)


# מאונט על /assets בלבד. כל נתיב אחר שאינו /api ואינו / מקבל 404, ולכן
# אין דרך להגיע לקוד או לקונפיגורציה דרך השרת.
app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")
