"""שכבות ASGI שרצות לפני שהבקשה מגיעה לאפליקציה.

שתיהן כתובות כ-ASGI גולמי ולא כ-BaseHTTPMiddleware, כי BaseHTTPMiddleware
קורא את הגוף לפני שהוא מעביר הלאה — וזה בדיוק מה שהגבלת הגודל אמורה
למנוע.
"""

from __future__ import annotations

import json
import logging
from urllib.parse import urlsplit

from app.config import get_settings

logger = logging.getLogger("markdown_docs.middleware")
settings = get_settings()

MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# רק שמות שמצביעים על המכונה עצמה. אין כאן תבנית ואין סיומת — "localhost"
# הוא שם מדויק, ולכן evil-localhost.example אינו נכנס.
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _is_loopback_origin(origin: str) -> bool:
    """האם המקור מצביע על המכונה הזאת עצמה.

    הפירוק הוא urlsplit ולא השוואת מחרוזות: hostname מנרמל את הסוגריים
    של IPv6 ואת האותיות הגדולות, ומחזיר None כשה-Origin מעוות. השוואת
    startswith הייתה מאשרת גם http://localhost.evil.example.
    """
    try:
        parts = urlsplit(origin)
    except ValueError:
        # "http://[::1" — סוגר מרובע חסר — מפיל את urlsplit. ה-Origin מגיע
        # מהרשת, ולכן קלט פסול חייב להיות תשובת 403 ולא חריגה שמטפסת
        # החוצה והופכת ל-500.
        return False
    if parts.scheme not in ("http", "https"):
        return False
    return (parts.hostname or "").lower() in LOOPBACK_HOSTS


async def _send_error(send, status: int, body: dict) -> None:
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(payload)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})


def _headers(scope) -> dict[str, str]:
    return {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}


class OriginGuard:
    """חוסם בקשות משנות מצב שלא הגיעו ממקור מאושר.

    הכלל הוא allowlist: Origin חסר נדחה בדיוק כמו Origin זר. הניסוח
    המתבקש "אם הכותרת קיימת ולא תואמת — חסום" נשמע זהיר, אבל הוא מתיר
    כל בקשה שלא נשלחה בה הכותרת — וזו בדיוק הבקשה שתוקף ישלח.

    דפדפנים שולחים Origin בכל fetch שמשנה מצב, ולכן הלקוח שלנו לא נפגע.
    """

    def __init__(
        self,
        app,
        allowlist: frozenset[str],
        protected_prefixes: tuple[str, ...] = ("/api", "/mcp-consent"),
        allow_loopback: bool = False,
    ) -> None:
        self.app = app
        self.allowlist = allowlist
        # יותר מקידומת אחת, כי מסך אישור ה-OAuth של ה-MCP הוא טופס HTML
        # רגיל שאינו יושב תחת /api. בלי ההגנה כאן, מי שנרשם כלקוח MCP
        # (הרישום פתוח — כך claude.ai מתחבר) יכול לייצר בקשת authorize
        # משלו ולגרום לדפדפן של המשתמש המחובר לאשר אותה בלי ידיעתו.
        #
        # /mcp עצמו אינו ברשימה בכוונה: הוא מאמת ב-Bearer בלבד ודוחה
        # cookie, ולכן אינו פגיע ל-CSRF מלכתחילה — וכפיית Origin עליו
        # הייתה חוסמת כל לקוח שאינו דפדפן.
        self.protected_prefixes = tuple(protected_prefixes)
        self.allow_loopback = allow_loopback

    def _accepts(self, origin: str) -> bool:
        if origin in self.allowlist:
            return True
        return self.allow_loopback and _is_loopback_origin(origin)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if scope["method"] in MUTATING_METHODS and scope["path"].startswith(self.protected_prefixes):
            origin = _headers(scope).get("origin", "").rstrip("/")
            if not self._accepts(origin):
                logger.warning(
                    "בקשה משנה מצב נדחתה: %s %s (Origin=%s)",
                    scope["method"],
                    scope["path"],
                    origin or "חסר",
                )
                return await _send_error(send, 403, {"detail": "מקור הבקשה אינו מאושר"})
        return await self.app(scope, receive, send)


class BodySizeLimit:
    """דוחה גוף בקשה שחורג מהגבול, לפני שהאפליקציה רואה אותו.

    שני מסלולים. אם יש Content-Length, מספיק לקרוא אותו ולדחות מיד בלי
    לגעת ברשת. אם אין — כלומר chunked — קוראים תוך כדי ספירה ומפסיקים
    ברגע החריגה, כך שלכל היותר מוחזק בזיכרון גבול+1 בתים.

    הספירה היא בבתים ולא בתווים. תו עברי הוא שני בתים ב-UTF-8, וגבול
    שנאכף על אורך המחרוזת אחרי הפענוח היה כפול ממה שהתכוונו אליו —
    ובעיקר, הוא היה נאכף אחרי שכל הגוף כבר בזיכרון.
    """

    def __init__(self, app, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in MUTATING_METHODS:
            return await self.app(scope, receive, send)

        declared = _headers(scope).get("content-length")
        if declared is not None:
            try:
                length = int(declared)
            except ValueError:
                return await _send_error(send, 400, {"detail": "בקשה לא תקינה"})
            # Content-Length שלילי אינו חוקי, ואילו הועבר הלאה הוא היה
            # עובר את בדיקת הגבול ומגיע לאפליקציה כאילו הכול תקין.
            if length < 0:
                return await _send_error(send, 400, {"detail": "בקשה לא תקינה"})
            if length > self.max_bytes:
                return await self._too_large(send, declared)
            return await self.app(scope, receive, send)

        # אין Content-Length: קוראים בעצמנו עד הגבול, ומשמיעים מחדש
        # לאפליקציה. החסם על הזיכרון הוא הגבול עצמו.
        chunks: list[bytes] = []
        total = 0
        more = True
        disconnected = False
        while more:
            message = await receive()
            if message["type"] != "http.request":
                # הלקוח התנתק באמצע. שומרים את האירוע ומעבירים אותו
                # הלאה — אפליקציה שממירה אותו לגוף ריק חושבת שהבקשה
                # הושלמה כרגיל, ומעבדת חצי בקשה.
                disconnected = True
                break
            body = message.get("body", b"")
            total += len(body)
            if total > self.max_bytes:
                return await self._too_large(send, "chunked")
            chunks.append(body)
            more = message.get("more_body", False)

        buffered = b"".join(chunks)
        delivered = disconnected  # אם התנתק, אין גוף שלם להשמיע

        async def replay_receive():
            """מוסר את הגוף פעם אחת, ואז מדווח על ניתוק.

            החזרת אותו גוף שוב ושוב הופכת כל קריאה נוספת ל-receive ללולאה
            אינסופית מבחינת מי שקורא — הוא לעולם לא רואה את סוף הזרם.
            """
            nonlocal delivered
            if delivered:
                return {"type": "http.disconnect"}
            delivered = True
            return {"type": "http.request", "body": buffered, "more_body": False}

        return await self.app(scope, replay_receive, send)

    async def _too_large(self, send, hint: str) -> None:
        logger.warning("גוף בקשה נדחה בגודל (%s > %d בתים)", hint, self.max_bytes)
        await _send_error(
            send,
            413,
            {"detail": f"הבקשה גדולה מדי — הגבול הוא {self.max_bytes} בתים"},
        )
