"""מעטפת התשובות של הכלים, ועזרי עיצוב.

שני עקרונות שחוזרים בכל הכלים:

1. מעטפת אחידה — `{"ok": true, ...}` או `{"ok": false, "error": ...}`.
   קוד השגיאה קצר ויציב, כדי שהמודל יוכל לפעול לפיו ולא לנתח טקסט.

2. לעולם לא רק "לא נמצא". מודל שקיבל `{"error": "not_found"}` תקוע —
   הוא אינו יודע אם טעה בשם, אם המשאב נמחק, או אם הוא מסתכל במקום
   הלא נכון. כל שגיאת "לא נמצא" מלווה במשהו שאפשר להמשיך ממנו:
   רשימת מועמדים, הצעות דומות, או לפחות מה כן קיים.
"""

from __future__ import annotations

import difflib
import math
from typing import Any

from app.config import get_settings


def ok(**fields: Any) -> dict:
    return {"ok": True, **fields}


def err(code: str, **fields: Any) -> dict:
    return {"ok": False, "error": code, **fields}


def clamp(value: Any, low: int, high: int, default: int) -> int:
    """חותך ערך לטווח, ולא דוחה אותו.

    מודל ששלח limit=1000 התכוון ל"הרבה". דחייה מכריחה אותו לנחש שוב
    ולשרוף סיבוב; חיתוך נותן לו תשובה שימושית מיד.

    NaN ו-Infinity נבדקים במפורש לפני ההמרה: json.loads מקבל את
    הליטרלים האלה כברירת מחדל, כך ש-limit: Infinity מגיע לכאן כ-float
    אינסופי. int(float("inf")) זורק OverflowError — ולא ValueError —
    ופונקציה שכל תפקידה הוא לא ליפול על קלט חריג לא אמורה ליפול על
    הקלט החריג ביותר. OverflowError נשאר ברשימה כרשת ביטחון לטיפוסים
    שאינם float אבל מתרגמים למספר עצום.
    """
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return max(low, min(high, number))


def document_url(project_slug: str, doc_slug: str) -> str | None:
    """הקישור הציבורי למסמך.

    נבנה כאן ידנית כי אין לזה utility בקוד — הבנייה היחידה היא בפרונט
    (index.html), והיא hash-routing ולא נתיב שרת. בלי RENDER_EXTERNAL_URL
    אין בסיס אמין, ולכן מחזירים None במקום לנחש מארח.
    """
    base = (get_settings().render_external_url or "").strip().rstrip("/")
    if not base:
        return None
    return f"{base}/#/{project_slug}/{doc_slug}"


def did_you_mean(needle: str, haystack: list[str], limit: int = 5) -> list[str]:
    """מועמדים קרובים, כדי ששגיאת "לא נמצא" תהיה ניתנת להמשך.

    difflib ולא התאמה מדויקת: הטעות הנפוצה היא שגיאת כתיב או צורת
    כתיבה אחרת, לא שם שהומצא מאפס.
    """
    return difflib.get_close_matches(needle, haystack, n=limit, cutoff=0.5)
