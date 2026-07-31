"""מודלי בקשה ותשובה.

התשובה הציבורית והתשובה המאומתת הן שני מודלים נפרדים, ולא מודל אחד עם
דגל. מודל עם דגל שוכח את הדגל בדיוק פעם אחת, וזו הפעם שדולפת.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import Visibility

TITLE_MAX = 300
NAME_MAX = 200

# `position` ו-`client_seq` מוגדרים כ-int ולא כ-float בכוונה. json של
# פייתון מקבל את הליטרלים NaN ו-Infinity, ו-NaN עובר כל בדיקת טווח כי כל
# השוואה איתו מחזירה False (כלל 4). שדה int דוחה אותם בשכבת הפענוח, לפני
# שהערך בכלל מגיע לקוד שלנו — ויש על זה טסט.


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=NAME_MAX)
    slug: str | None = Field(default=None, max_length=100)
    description: str | None = None
    visibility: Visibility = Visibility.PRIVATE


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=NAME_MAX)
    description: str | None = None
    visibility: Visibility | None = None


class DocumentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=TITLE_MAX)
    slug: str | None = Field(default=None, max_length=100)
    content: str = ""
    position: int | None = Field(default=None, ge=0, le=1_000_000)


class DocumentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=TITLE_MAX)
    content: str | None = None
    position: int | None = Field(default=None, ge=0, le=1_000_000)

    # מונה עולה שהעורך מנהל. כשהוא מגיע, כתיבה עם מונה נמוך או שווה
    # לאחרון שהתקבל נדחית — כך שמירה שנתקעה ברשת לא דורסת חדשה ממנה.
    # כשהוא לא מגיע, חוזרים לכלל "האחרון לפי סדר ההגעה מנצח".
    client_seq: int | None = Field(default=None, ge=0)


# ─────────────────────────── תשובות ───────────────────────────


class DocumentSummary(BaseModel):
    """שורה ברשימת המסמכים של פרויקט. זהה לציבורי ולמאומת."""

    model_config = ConfigDict(from_attributes=True)

    slug: str
    title: str
    position: int


class DocumentPublic(BaseModel):
    """מסמך מלא כפי שהוא נחשף החוצה. בלי מזהים פנימיים ובלי גרסאות."""

    model_config = ConfigDict(from_attributes=True)

    slug: str
    title: str
    content: str
    position: int
    updated_at: datetime


class DocumentPrivate(DocumentPublic):
    """מה שהבעלים רואה בנוסף."""

    created_at: datetime
    last_client_seq: int


class ProjectPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    slug: str
    name: str
    description: str | None
    documents: list[DocumentSummary] = []


class ProjectPrivate(ProjectPublic):
    visibility: Visibility
    created_at: datetime
    updated_at: datetime


class DocumentWriteResult(BaseModel):
    """תשובת כתיבה.

    `applied=False` מסמן בקשה שהגיעה מאוחר ונדחתה לפי סדר. זו לא שגיאה
    ולכן הסטטוס נשאר 200 — אין סיבה להבהיל את המשתמש בגלל בקשה שכבר לא
    רלוונטית.
    """

    applied: bool
    document: DocumentPrivate


class VersionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    created_at: datetime
    size: int
