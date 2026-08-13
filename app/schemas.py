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

    # ה-slug הוא שם הקובץ שמוצג במסך ובהורדה, ולכן שינוי שם המסמך גורר
    # אותו. אפשר לתת אותו במפורש, כמו ב-DocumentCreate.
    slug: str | None = Field(default=None, min_length=1, max_length=100)

    # או לבקש שייגזר מהכותרת. הלקוח משתמש בזה ולא גוזר בעצמו, כי כללי
    # ה-slug נשענים על מחלקות תווים של יוניקוד: ‎\w ב-Python תחת re.UNICODE
    # כולל אותיות בכל כתב, ואילו ‎\w ב-JavaScript הוא ASCII בלבד. תאום
    # בצד הלקוח היה נראה נכון ופוסל שמות עבריים לגיטימיים.
    #
    # דגל מפורש ולא גזירה אוטומטית מכל title שנשלח: השמירה האוטומטית
    # של העורך אינה שולחת כותרת היום, אבל אם מישהו יוסיף אותה, גזירה
    # שקטה הייתה משנה את שם הקובץ בכל הקלדה.
    slug_from_title: bool = False

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
    """שורה ברשימת המסמכים של פרויקט, בתצוגה הציבורית."""

    model_config = ConfigDict(from_attributes=True)

    slug: str
    title: str
    position: int


class DocumentSummaryPrivate(DocumentSummary):
    """אותה שורה, עם המזהה היציב, לבעלים.

    ראו DocumentPrivate.id להסבר למה המזהה נחשף בכלל.
    """

    id: uuid.UUID


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

    # המזהה היציב היחיד של מסמך. ה-slug אינו מזהה: הוא משתנה בכל עדכון
    # עם slug מפורש או slug_from_title, והייחודיות (project_id, slug)
    # נאכפת רק ב-flush — כלומר slug שהתפנה ניתן לתפיסה מחדש על ידי מסמך
    # אחר. צרכן שמחזיק slug ישן אינו מקבל שגיאה, אלא מגיע בשקט למסמך
    # הלא נכון. זה נסבל בממשק שמרענן את הרשימה בכל טעינה, אבל לא לצרכן
    # שמחזיק הקשר בין קריאות — ומכאן הצורך.
    #
    # נחשף לבעלים בלבד, לפי אותו היגיון של LinkPublic מול LinkPrivate:
    # מזהה חסר שימוש למי שאינו יכול לערוך.
    id: uuid.UUID

    created_at: datetime
    last_client_seq: int
    last_editor_id: str | None


class LinkPublic(BaseModel):
    """מה שאנונימי רואה. בלי מזהה — הוא חסר שימוש למי שלא יכול לערוך."""

    model_config = ConfigDict(from_attributes=True)

    title: str
    url: str
    position: int


class LinkPrivate(LinkPublic):
    """לבעלים. המזהה נדרש לו כדי לעדכן ולמחוק."""

    id: uuid.UUID


class ProjectPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    slug: str
    name: str
    description: str | None
    documents: list[DocumentSummary] = []
    links: list[LinkPublic] = []


class ProjectPrivate(ProjectPublic):
    documents: list[DocumentSummaryPrivate] = []
    links: list[LinkPrivate] = []
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

    # המזהה היציב של המסמך, לבעלים בלבד — ריק לצופה אנונימי, לפי אותה
    # מוסכמה של DocumentPublic מול DocumentPrivate. אין כאן אסימטריה
    # נוספת: משתמש מחובר רואה בחיפוש רק את הפרויקטים שלו ממילא, ולכן
    # "יש צופה" ו"הצופה הוא הבעלים" הם אותו תנאי.
    doc_id: uuid.UUID | None = None

    # קטע מהתוכן עם סימון סביב ההתאמה. הסימנים הם « ו-» ולא תגיות HTML,
    # כי הלקוח מרנדר רכיבי React ולא מזריק HTML.
    snippet: str

    updated_at: datetime

    # ציון הרלוונטיות. בלעדיו צרכן שאינו אנושי אינו יכול להבחין בין
    # התאמה חזקה לחלשה, ולכן הוא נאלץ למשוך את תוכן כל התוצאות רק כדי
    # להחליט — בזבוז שמורגש מיד בשרת ה-MCP.
    #
    # הסולם אינו אחיד בין שני סוגי ההתאמה: ב-"text" זה ts_rank וב-"fuzzy"
    # זה similarity. ההשוואה תקפה בתוך אותה תשובה, כי כל תשובה מגיעה
    # ממסלול אחד בלבד — הדמיון רץ רק כשחיפוש הטקסט לא החזיר כלום.
    rank: float

    # "text" = התאמה בחיפוש טקסט מלא. "fuzzy" = נמצא רק בהשוואת דמיון,
    # אחרי שהחיפוש הרגיל לא החזיר כלום. הלקוח יכול להציג את זה אחרת.
    match: str
