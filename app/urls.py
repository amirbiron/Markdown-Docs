"""אימות כתובות שהמשתמש סיפק.

אותו היגיון כמו safeHref בצד הלקוח, ומאותה סיבה: הוולידציה חייבת להיות
בשני הצדדים. הלקוח מגן על מי שמסתכל עכשיו; השרת מגן על כל מי שיסתכל
אחר כך, כולל דרך לקוח אחר שלא כתבנו.
"""

from __future__ import annotations

from urllib.parse import urlparse

# רשימה לבנה ולא שחורה. `javascript:`, `data:` ו-`vbscript:` הם ערכי href
# חוקיים לגמרי מבחינת HTML, ורשימה שחורה תמיד מפספסת סכמה — אלה רק אלה
# שנזכרים בהם.
ALLOWED_SCHEMES = frozenset({"http", "https"})

MAX_URL_LENGTH = 2048

# תווי בקרה. דפדפנים מתעלמים מהם בזמן פענוח הסכמה, ולכן "java\tscript:"
# מתפרש כ-javascript: אבל עובר בדיקה נאיבית.
_CONTROL = {chr(code) for code in range(0x20)} | {chr(0x7F)}


class UrlError(ValueError):
    """הכתובת אינה שמישה. ההודעה בעברית ומיועדת למשתמש."""


def clean(raw: str) -> str:
    """מנקה ומאמת. מחזיר את הכתובת, או זורק UrlError."""
    url = "".join(ch for ch in (raw or "") if ch not in _CONTROL).strip()

    if not url:
        raise UrlError("הכתובת ריקה")
    if len(url) > MAX_URL_LENGTH:
        raise UrlError(f"הכתובת ארוכה מדי — עד {MAX_URL_LENGTH} תווים")

    try:
        parts = urlparse(url)
    except ValueError:
        raise UrlError("הכתובת אינה תקינה") from None

    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise UrlError("מותרות כתובות http ו-https בלבד")

    # `//example.com` מתפרש בדפדפן כיחסי-לפרוטוקול ויוצא החוצה, אבל
    # urlparse מחזיר עליו scheme ריק — ולכן הוא כבר נפסל למעלה.
    #
    # כאן בודקים hostname ולא netloc, וזה ההבדל שחשוב: ל-`https://user@`
    # יש netloc (המחרוזת "user@") אבל אין בו מארח כלל. בדיקה על netloc
    # הייתה מקבלת אותו.
    if not parts.hostname:
        raise UrlError("הכתובת חסרה שם מארח")

    # גישה ל-.port זורקת ValueError על פורט לא חוקי — `x.co:99999` או
    # `x.co:abc`. בלי הבדיקה הזו הכתובת מתקבלת, והשגיאה מתפוצצת אחר כך
    # במקום אחר לגמרי, אצל מי שינסה להשתמש בה.
    try:
        parts.port
    except ValueError:
        raise UrlError("מספר הפורט בכתובת אינו תקין") from None

    return url
