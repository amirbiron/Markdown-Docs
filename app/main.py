"""נקודת הכניסה של השרת.

השרת מגיש גם את ה-API תחת /api וגם את הפרונט הסטטי מהשורש, מאותו origin.
זה מה שמייתר CORS ומאפשר ל-cookie להיות SameSite=Lax בלי סיבוכים.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

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
from app.routers import backup as backup_router
from app.routers import documents as documents_router
from app.routers import links as links_router
from app.routers import projects as projects_router
from app.routers import search as search_router
from app import scheduler
from app.mcp import oauth_consent
from app.mcp import server as mcp_server
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
# נגזרים מ-__file__ ולא מספריית העבודה. uvicorn שהופעל מספרייה אחרת היה
# מחפש את index.html במקום הלא נכון ונופל בעלייה.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ASSETS_DIR = PROJECT_ROOT / "assets"
INDEX_FILE = PROJECT_ROOT / "index.html"

# אותן כותרות שהיו ב-render.yaml כשהאתר היה סטטי. ברגע שהוא הפך לשירות
# web, Render לא מזריק אותן יותר — הן חייבות לצאת מהאפליקציה, אחרת הן
# פשוט נעלמות בלי שאף אחד ישים לב.
#
# 'unsafe-inline' ו-'unsafe-eval' נדרשים בפועל: ה-dc-runtime מריץ את
# לוגיקת הרכיב דרך new Function, וכל העיצוב הוא inline styles. לכן ה-CSP
# הזה לא יעצור הזרקת קוד.
#
# מה שהוא כן עושה, ומאז שהספריות והגופנים ירדו ל-assets הרבה יותר חזק:
# אין כאן אף מקור חיצוני, באף הנחיה. כלומר גם קוד שהצליח לרוץ בדף לא
# יכול למשוך קוד נוסף מבחוץ ולא יכול להבריח מידע החוצה — אפילו לא דרך
# בקשת גופן, שהיא ערוץ יציאה לכל דבר.
CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
        "style-src 'self' 'unsafe-inline'",
        "font-src 'self'",
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

    # מתזמן הגיבוי רץ כאן ולא כשירות נפרד, כי דיסק קבוע ב-Render נגיש
    # למופע אחד בלבד ו-Cron Job אינו יכול לגעת בו כלל.
    backup_task = None
    if settings.backup_enabled:
        backup_task = asyncio.create_task(scheduler.loop())

    yield

    if backup_task is not None:
        backup_task.cancel()
        # ההמתנה אינה טקס: בלעדיה התהליך יורד באמצע כתיבת ארכיון,
        # והקובץ החלקי נשאר על הדיסק.
        with suppress(asyncio.CancelledError):
            await backup_task

    await engine.dispose()


# נבנה רק כשיש טוקן. בלעדיו הנתיב כלל אינו קיים — נעילה במפתח ולא
# בשומר.
_mcp_app = mcp_server.build_asgi_app() if mcp_server.is_enabled() else None


def _mount_well_known(parent: FastAPI, mcp_app) -> None:
    """מעתיק את נתיבי ה-.well-known של ה-OAuth לשורש הדומיין.

    ה-SDK רושם אותם בתוך תת-האפליקציה, ולכן אחרי ההרכבה על /mcp הם
    מגיעים כ-/mcp/.well-known/... — והלקוח מחפש אותם בשורש. RFC 8414
    ו-RFC 9728 קובעים שם קבוע ביחס לשורש המארח, ולא ביחס לנתיב
    השירות, ולכן אין לזה מעקף בצד הלקוח.

    ה-endpoint עצמו ממוחזר כמו שהוא ולא נבנה מחדש: התוכן צריך להיות
    זהה, ושכפול היה מתפצל מה-SDK בשקט בשדרוג גרסה.

    שאר הנתיבים — /authorize, /token, /register, /revoke — נשארים תחת
    /mcp, וזה תקין: המטא-דאטה מצהירה עליהם ככתובות מלאות, וה-issuer
    מוגדר כ-<host>/mcp כדי שההצהרה תתאים למציאות.
    """
    for route in mcp_app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/.well-known/"):
            continue
        parent.router.add_route(
            path,
            route.endpoint,
            methods=list(getattr(route, "methods", None) or ["GET"]),
            include_in_schema=False,
        )
        logger.info("נתיב גילוי OAuth נרשם בשורש: %s", path)


class _BareMCPPath:
    """מעביר בקשה ל-``/mcp`` אל תת-האפליקציה, כאילו הגיעה ל-``/mcp/``.

    ההרכבה מייצרת התאמה ל-``/mcp/`` בלבד, ו-Starlette עונה על ``/mcp``
    בהפניה 307. הפניה אינה נושאת ``WWW-Authenticate`` — היא לא תשובת
    אימות — ולכן לקוח ששלח בקשה לכתובת הלא-מלוכסנת מקבל 307 ריק, ואם
    הוא קורא את האתגר מהתשובה הראשונה הוא לא מגלה שנדרש אימות בכלל.

    זה אינו תרחיש קצה: הכתובת שמדביקים ב-connector היא בדיוק
    ``https://<host>/mcp``, וכך גם כתוב בתיעוד. לקוח שאינו עוקב
    אוטומטית אחרי ההפניה מדווח "אין כלים" בלי להסביר למה.

    **הבקשה מועברת לאפליקציה כולה, לא ל-endpoint שבתוכה.** מיחזור של
    ה-endpoint לבדו נראה עובד — הוא אפילו מחזיר את ה-401 הנכון — אבל
    שכבת האימות של תת-האפליקציה לא רצה עליו, ולכן טוקן תקף נדחה גם
    הוא. המעבר דרך ``mcp_app`` שומר על ה-middleware ועל אותו מנהל
    סשנים, שמותר להריץ אותו פעם אחת בלבד.

    מחלקה ולא פונקציה, כי Starlette מזהה פונקציה כ-``endpoint`` שמקבל
    ``Request`` ועוטף אותה; אובייקט callable מטופל כ-ASGI app.
    """

    def __init__(self, mcp_app) -> None:
        self.mcp_app = mcp_app

    async def __call__(self, scope, receive, send) -> None:
        # בדיוק מה ש-Mount היה עושה: הנתיב הפנימי הוא השורש של
        # תת-האפליקציה, וה-root_path מציין מאיפה היא הורכבה.
        inner = dict(scope)
        inner["path"] = "/"
        inner["raw_path"] = b"/"
        inner["root_path"] = scope.get("root_path", "") + mcp_server.MOUNT_PATH
        await self.mcp_app(inner, receive, send)


def _mount_bare_path(parent: FastAPI, mcp_app, mount_path: str) -> None:
    """רושם את נקודת ה-MCP גם בלי הלוכסן הנגרר. ראו _BareMCPPath."""
    parent.router.add_route(
        mount_path,
        _BareMCPPath(mcp_app),
        methods=["GET", "POST", "DELETE"],
        include_in_schema=False,
    )
    logger.info("נקודת MCP נרשמה גם בלי לוכסן: %s", mount_path)


@asynccontextmanager
async def combined_lifespan(app: FastAPI):
    """משרשר את ה-lifespan של האפליקציה עם זה של ה-MCP.

    streamable_http מחזיק את מנהל הסשנים שלו בתוך ה-lifespan של
    תת-האפליקציה, ו-Starlette אינה מריצה lifespan של אפליקציה
    ממורכבת. בלי השרשור כאן, הבקשה הראשונה ל-/mcp נופלת על
    "task group was not initialized" — והשרת נראה עולה תקין עד אז.

    הפתרון המקובל הוא להעביר lifespan=mcp_app.lifespan ל-FastAPI, אבל
    כאן הוא היה דורס את ה-lifespan הקיים ומבטל את בדיקת הקידוד, את
    ה-seed ואת מתזמן הגיבויים. הסדר מכוון: הקיים בחוץ, כדי שבסיס
    הנתונים יהיה מוכן לפני שה-MCP עולה.
    """
    async with lifespan(app):
        if _mcp_app is None:
            yield
        else:
            # מעבירים את תת־האפליקציה ולא את ההורה. כרגע ה-SDK מגדיר
            # lifespan=lambda app: session_manager.run() ומתעלם מהארגומנט
            # לחלוטין, ולכן זה אינו משנה בפועל — אבל אפליקציה שמריצה
            # lifespan של אפליקציה אחרת היא בדיוק סוג הקוד שנשבר בשקט
            # כשגרסה עתידית תתחיל להשתמש בו.
            async with _mcp_app.router.lifespan_context(_mcp_app):
                yield


app = FastAPI(
    title="Markdown Docs", lifespan=combined_lifespan, docs_url=None, redoc_url=None
)


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

    # מעטפת האפליקציה חייבת לאמת מול השרת בכל טעינה.
    #
    # בלי הכותרת הזאת התשובה יוצאת עם ETag ו-Last-Modified בלבד, ואין בה
    # שום הנחיית טריות. במצב כזה RFC 9111 §4.2.2 מרשה לדפדפן לבחור זמן
    # חיים היוריסטי, והנפוץ הוא כ-10% מהגיל של הקובץ. index.html שלא
    # השתנה ארבעה ימים מקבל כך כעשר שעות של "טרי" — הדפדפן לא שולח
    # בקשה בכלל, וה-ETag שקיים חסר משמעות כי אין למי להשוות אותו.
    #
    # התוצאה היא פריסה שמגיעה למי שהאתר פתוח אצלו רק אחרי שעות, בלי שום
    # סימן. no-cache אינו "אל תשמור" אלא "שמור, אבל אמת בכל פעם": הגוף
    # נשאר במטמון, והאימות מחזיר 304 ריק כשאין שינוי.
    #
    # /api לא נכלל בכוונה — התשובות שם נוצרות בלי ETag ובלי Last-Modified,
    # ובלעדיהם אין לדפדפן על מה לבסס היוריסטיקה מלכתחילה.
    path = request.url.path
    if path == "/" or path.startswith("/assets/"):
        response.headers.setdefault("Cache-Control", "no-cache")
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
api.include_router(backup_router.router)
app.include_router(api)

# הסדר כאן הוא מה שקובע מה רץ קודם: מה שנוסף אחרון עוטף את הכול.
# OriginGuard חייב לרוץ לפני BodySizeLimit — אחרת בקשה עוינת עם גוף של
# מגה־בייט הייתה נקראת במלואה לפני שנגלה שהמקור שלה פסול.
app.add_middleware(BodySizeLimit, max_bytes=settings.max_body_bytes)
app.add_middleware(
    OriginGuard,
    allowlist=settings.origin_allowlist,
    allow_loopback=settings.allow_loopback_origins,
)


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(INDEX_FILE)


# מאונט על /assets בלבד. כל נתיב אחר שאינו /api ואינו / מקבל 404, ולכן
# אין דרך להגיע לקוד או לקונפיגורציה דרך השרת.
app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")

# שרת ה-MCP, אם הופעל. שימו לב ש-/mcp אינו מתחיל ב-/api ולכן OriginGuard
# אינו חל עליו — ההגנה שם היא טוקן Bearer בלבד, וזהות מ-cookie נדחית
# במפורש. ראו app/mcp/auth.py.
if _mcp_app is not None:
    _mount_well_known(app, _mcp_app)
    _mount_bare_path(app, _mcp_app, mcp_server.MOUNT_PATH)
    app.include_router(oauth_consent.router)
    app.mount(mcp_server.MOUNT_PATH, _mcp_app)
    logger.info("שרת ה-MCP מחובר על %s", mcp_server.MOUNT_PATH)
