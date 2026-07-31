"""יצירה ואימות של slug.

ה-slug תומך בעברית. `/p/תיעוד-מוצר` היא כתובת חוקית לגמרי — הדפדפן
מקודד אותה ב-percent-encoding ומציג אותה קריאה בחזרה. זה עדיף על
תעתיק אוטומטי, שתמיד מייצר משהו מגושם.
"""

from __future__ import annotations

import re
import unicodedata

MAX_SLUG_LENGTH = 100

# מותר: אותיות בכל כתב, ספרות ומקף. אסור: רווח, נקודה, לוכסן וכל סימן
# אחר — כך אין דרך להבריח מעבר נתיב או להתחזות לסיומת קובץ.
_DISALLOWED = re.compile(r"[^\w\-]", re.UNICODE)
_UNDERSCORE = re.compile(r"_")
_WHITESPACE = re.compile(r"\s+", re.UNICODE)
_MULTI_DASH = re.compile(r"-{2,}")


class SlugError(ValueError):
    """ה-slug שהתקבל או שנגזר אינו שמיש."""


def normalize(value: str) -> str:
    """NFC על כל slug, לפני השוואה ולפני שמירה.

    בלי זה נכנסת תקלה שקשה לאתר: `café` שנכתב עם התו המורכב U+00E9 ו-`café`
    שנכתב כ-`e` ועוד טעם משולב נראים זהים לחלוטין על המסך, אבל הם מחרוזות
    שונות. אילוץ ה-UNIQUE לא היה תופס אותם, והיו נוצרים שני פרויקטים עם
    אותו מזהה לכאורה.

    לעברית זה פחות קריטי — ניקוד עברי לא מורכב ב-NFC, ולכן `שָׁלוֹם` הוא אותה
    מחרוזת בשתי הצורות. הנרמול כאן קיים בשביל כל שאר הכתבים, ובשביל צורות
    תצוגה כמו U+FB2A שכן מתפרקות.
    """
    return unicodedata.normalize("NFC", value)


def slugify(value: str) -> str:
    """גוזר slug מטקסט חופשי. זורק SlugError אם לא נשאר כלום."""
    text = normalize(value).strip().lower()
    text = _WHITESPACE.sub("-", text)
    text = _UNDERSCORE.sub("-", text)  # \w כולל קו תחתון, ואנחנו לא רוצים אותו
    text = _DISALLOWED.sub("", text)
    text = _MULTI_DASH.sub("-", text).strip("-")

    if not text:
        raise SlugError("לא ניתן לגזור מזהה מהשם שהוזן — יש להזין מזהה במפורש")
    return text[:MAX_SLUG_LENGTH].strip("-")


def validate(value: str) -> str:
    """מאמת slug שהמשתמש סיפק, ומחזיר אותו מנורמל."""
    text = normalize(value).strip().lower()
    if not text:
        raise SlugError("המזהה ריק")
    if len(text) > MAX_SLUG_LENGTH:
        raise SlugError(f"המזהה ארוך מדי — עד {MAX_SLUG_LENGTH} תווים")
    if text != slugify(text):
        raise SlugError("המזהה מכיל תווים שאינם מותרים — אותיות, ספרות ומקפים בלבד")
    return text


def resolve(explicit: str | None, fallback_name: str) -> str:
    """ה-slug שהמשתמש נתן, ואם לא נתן — נגזר מהשם."""
    if explicit is not None and explicit.strip():
        return validate(explicit)
    return slugify(fallback_name)
