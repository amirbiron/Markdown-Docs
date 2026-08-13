"""אימות שרת ה-MCP.

נקודת אכיפה אחת. כל כלי מקבל את הזהות מכאן, ואף כלי אינו קורא
כותרות בעצמו.
"""

from __future__ import annotations

import logging
import secrets
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import User

logger = logging.getLogger("markdown_docs.mcp.auth")

SCOPE_READ = "read"
SCOPE_WRITE = "write"


class AuthError(Exception):
    """הטוקן חסר או שגוי."""


class PermissionError_(Exception):
    """הטוקן תקף אבל אין לו את ההרשאה הנדרשת.

    שם עם קו תחתון נגרר כדי לא להאפיל על PermissionError המובנה של
    פייתון, שנזרק גם על שגיאות מערכת קבצים ואינו קשור לכאן.
    """


@dataclass(frozen=True)
class Identity:
    """מי מבצע את הקריאה, ומה מותר לו."""

    user: User
    scopes: frozenset[str]

    def has(self, scope: str) -> bool:
        return scope in self.scopes


def extract_bearer(headers) -> str | None:
    """מוציא את הטוקן מכותרת Authorization, ורק ממנה.

    זהות מבוססת cookie נדחית כאן במכוון, וזו אינה החמרה תיאורטית:
    הנתיב /mcp אינו מתחיל ב-/api, ולכן OriginGuard אינו חל עליו כלל
    (app/middleware.py:94). אם היינו מקבלים cookie, כל דף באינטרנט
    היה יכול לגרום לדפדפן של המשתמש לשלוח POST ל-/mcp עם ה-cookie
    שלו, בלי בדיקת Origin ובלי ידיעתו. טוקן Bearer אינו נשלח
    אוטומטית על ידי הדפדפן, ולכן הוא חסין לזה.
    """
    raw = headers.get("authorization") or headers.get("Authorization")
    if not raw:
        return None
    parts = raw.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


def token_matches(candidate: str) -> bool:
    """השוואה בזמן קבוע מול הטוקן המוגדר.

    compare_digest ולא ==, כדי שזמן התשובה לא ידלוף כמה תווים
    ראשונים נוכחו.

    ההשוואה על bytes ולא על str: compare_digest זורק TypeError על
    מחרוזת שאינה ASCII. טוקן עם תו עברי או אמוג'י — קל להדביק כזה
    בטעות — היה מפיל את הפונקציה, החריגה הייתה עוקפת את AuthError,
    והלקוח היה מקבל internal_error במקום unauthorized. כלומר שגיאת
    אימות רגילה נראית כמו תקלה בשרת.
    """
    settings = get_settings()
    expected = (settings.mcp_token or "").strip()
    if not expected:
        return False
    return secrets.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


async def resolve_identity(session: AsyncSession, headers, verified=None) -> Identity:
    """מאמת את הבקשה ומחזיר את הזהות, או זורק AuthError.

    שני מסלולים, ונקודת יציאה אחת:

    1. ``verified`` — טוקן ש-SDK כבר אימת (OAuth). כשה-OAuth דלוק,
       ה-middleware של ה-SDK חוסם כל בקשה לא מאומתת עוד לפני שהגענו
       לכאן, ומעביר את הטוקן ב-context. הטוקן נושא ``subject`` עם
       מזהה המשתמש שאישר, ואת ה-scopes שהוא אישר בפועל.
    2. הטוקן הסטטי מ-``MCP_TOKEN``, למי שמתחבר ישירות בלי דפדפן.

    בשני המקרים המשתמש נשלף מה-DB ולא נבנה מהטוקן — שליפה אמיתית
    מוודאת שהוא קיים, במקום להניח.
    """
    settings = get_settings()

    if verified is not None:
        return Identity(
            user=await _user_for(session, getattr(verified, "subject", None)),
            # ה-scopes מגיעים מההענקה עצמה ולא מהקונפיג: משתמש שאישר
            # קריאה בלבד לא אמור לקבל כתיבה רק כי המשתנה בסביבה מרשה
            # אותה. צמצום ההרשאה חייב להיות בידי מי שאישר.
            scopes=frozenset(getattr(verified, "scopes", None) or ()),
        )

    if not settings.mcp_enabled:
        # לא אמור לקרות — בלי טוקן הנתיב אינו נרשם בכלל — אבל אם
        # מישהו יקרא לשכבה הזו ישירות, ברירת המחדל היא דחייה.
        raise AuthError("שרת ה-MCP מכובה")

    token = extract_bearer(headers)
    if token is None or not token_matches(token):
        raise AuthError("טוקן חסר או שגוי")

    return Identity(user=await _user_for(session, None), scopes=settings.mcp_scopes)


async def _user_for(session: AsyncSession, subject: str | None) -> User:
    """המשתמש שבשמו הבקשה פועלת.

    ``subject`` מגיע מטוקן OAuth ומזהה את מי שאישר את החיבור. בלעדיו —
    כלומר בטוקן הסטטי — נלקח המשתמש היחיד, כי המערכת חד-משתמשית ומי
    שמחזיק את הטוקן פועל בשמו.
    """
    if subject:
        try:
            user = await session.get(User, uuid.UUID(subject))
        except ValueError:
            user = None
        if user is None:
            raise AuthError("המשתמש שהטוקן מצביע עליו אינו קיים")
        return user

    user = (
        await session.execute(select(User).order_by(User.created_at, User.id).limit(1))
    ).scalar_one_or_none()
    if user is None:
        raise AuthError("לא הוגדר משתמש במערכת")
    return user


def require_write(identity: Identity) -> None:
    """נקראת כשורה הראשונה בגוף כל כלי כותב.

    ל-SDK אין scopes פר-כלי: הרישום הוא דקורטור שמכריז על קיום הכלי,
    לא על ההרשאה שנדרשת כדי להריץ אותו. כלי שנרשם ולא קורא לכאן ירוץ
    בשמחה עם טוקן קריאה בלבד. הבדיקה
    tests/test_mcp_require_write.py קיימת בדיוק כדי לתפוס כלי כותב
    שנוסף ונשכח.
    """
    if not identity.has(SCOPE_WRITE):
        raise PermissionError_(
            "לטוקן אין הרשאת כתיבה. הוסיפו write ל-MCP_TOKEN_SCOPES."
        )


def require_read(identity: Identity) -> None:
    """נקראת בכל כלי קריאה, בדיוק כמו require_write בכלי כתיבה.

    בלי זה ה-scopes נאכפים רק לכיוון אחד: טוקן עם
    MCP_TOKEN_SCOPES ריק — או עם ערך שכולו שגיאת כתיב, שנשמט
    ב-mcp_scopes — היה נדחה מכתיבה אבל קורא הכול. ההצהרה חייבת
    להיאכף בשני הכיוונים, אחרת היא אינה הצהרה.

    כתיבה גוררת קריאה: כלי הכתיבה שולפים את המסמך לפני שהם משנים
    אותו, ולכן טוקן write בלי read הוא רק מלכודת. זו הקלה מכוונת
    ומוצהרת, לא פרצה — write הוא ההרשאה הרחבה מבין השתיים.
    """
    if not (identity.has(SCOPE_READ) or identity.has(SCOPE_WRITE)):
        raise PermissionError_(
            "לטוקן אין הרשאת קריאה. הוסיפו read ל-MCP_TOKEN_SCOPES."
        )
