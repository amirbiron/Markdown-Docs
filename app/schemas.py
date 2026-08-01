"""מודלי בקשה ותשובה.

התשובה הציבורית והתשובה המאומתת הן שני מודלים נפרדים, ולא מודל אחד עם
דגל. מודל עם דגל שוכח את הדגל בדיוק פעם אחת, וזו הפעם שדולפת.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models import Visibility

TITLE_MAX = 300
NAME_MAX = 200

# 2**31 - 1. הטיפוס בעמודה הוא Integer, לא BigInteger.
PG_INT_MAX = 2_147_483_647

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

    # מונה עולה שהעורך מנהל, יחד עם מזהה העורך ששלח אותו. הגבול העליון
    # הוא המקסימום של Integer ב-Postgres — בלעדיו ערך גדול יותר עובר את
    # הוולידציה ונכשל רק ב-INSERT, כשגיאת 500 במקום 422.
    client_seq: int | None = Field(default=None, ge=0, le=PG_INT_MAX)
    editor_id: str | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode="after")
    def _sequence_needs_an_editor(self) -> "DocumentUpdate":
        """מונה בלי מזהה עורך הוא חסר משמעות.

        ההגנה על סדר הכתיבות חלה בתוך עורך אחד. בלי לדעת מי שלח, אי אפשר
        להבחין בין "שמירה ישנה של אותו טאב" לבין "טאב אחר שהתחיל מאפס" —
        והבלבול הזה נועל את הטאב השני לחלוטין.
        """
        if self.client_seq is not None and self.editor_id is None:
            raise ValueError("client_seq דורש גם editor_id")
        return self


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
    last_editor_id: str | None


class LinkPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    url: str
    position: int


class ProjectPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    slug: str
    name: str
    description: str | None
    documents: list[DocumentSummary] = []
    links: list[LinkPublic] = []


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
    created_at: datetime

    # בבתים של UTF-8, כמו כל מדידת גודל אחרת במערכת. len() על מחרוזת היה
    # מחזיר בערך חצי מהגודל האמיתי של מסמך בעברית.
    size_bytes: int


# ─────────────────────────── קישורי פרויקט ───────────────────────────


class LinkCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=2048)
    position: int | None = Field(default=None, ge=0, le=PG_INT_MAX)


class LinkUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    url: str | None = Field(default=None, min_length=1, max_length=2048)
    position: int | None = Field(default=None, ge=0, le=PG_INT_MAX)




# ─────────────────────────── חיפוש ───────────────────────────


class SearchHit(BaseModel):
    project_slug: str
    project_name: str
    doc_slug: str
    title: str

    # קטע מהתוכן עם סימון סביב ההתאמה. הסימנים הם « ו-» ולא תגיות HTML,
    # כי הלקוח מרנדר רכיבי React ולא מזריק HTML.
    snippet: str

    # "text" = התאמה בחיפוש טקסט מלא. "fuzzy" = נמצא רק בהשוואת דמיון,
    # אחרי שהחיפוש הרגיל לא החזיר כלום. הלקוח יכול להציג את זה אחרת.
    match: str
