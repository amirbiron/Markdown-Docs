"""חיפוש חוצה-פרויקטים.

השאילתה עצמה יושבת כאן ולא בראוטר, כדי שהראוטר ושרת ה-MCP יריצו את
אותו חיפוש בדיוק. מנוע חיפוש שני היה נפרד מהראשון בשקט — ואז תוצאות
בממשק ותוצאות אצל הסוכן היו מתחילות להיות שונות בלי שאיש ישים לב.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Document, Project, User, Visibility

logger = logging.getLogger("markdown_docs.search")

# סף הדמיון לחיפוש המטושטש. נמוך מדי מחזיר רעש, גבוה מדי לא סובל אף
# שגיאת כתיב. 0.3 היא ברירת המחדל של pg_trgm ועובדת סביר על עברית.
TRIGRAM_THRESHOLD = 0.3

# הסימנים סביב ההתאמה בקטע. לא תגיות HTML, כי הלקוח מרנדר רכיבי React
# ולא מזריק HTML — שרשרת שהייתה הופכת כל תוצאת חיפוש לווקטור הזרקה.
HEADLINE_OPTIONS = "StartSel=«, StopSel=», MaxWords=28, MinWords=12, ShortWord=2, MaxFragments=1"


@dataclass(frozen=True)
class SearchResult:
    """תוצאה אחת, לפני שהוחלט מה ממנה נחשף לצרכן.

    document_id נמצא כאן תמיד. ההחלטה אם להעביר אותו הלאה היא של
    הצרכן: הראוטר משמיט אותו לצופה אנונימי, ושרת ה-MCP תמיד פועל
    כבעלים ולכן מחזיר אותו.
    """

    document_id: uuid.UUID
    project_slug: str
    project_name: str
    doc_slug: str
    title: str
    snippet: str
    rank: float
    updated_at: datetime
    match: str


def _visible(query, viewer: User | None):
    """מגביל לפרויקטים שמותר למי שמסתכל לראות."""
    if viewer is None:
        return query.where(Project.visibility == Visibility.PUBLIC)
    return query.where(Project.owner_id == viewer.id)


def _row_to_result(row, match: str) -> SearchResult:
    """שני המסלולים בוחרים את אותן עמודות באותו סדר."""
    return SearchResult(
        project_slug=row[0],
        project_name=row[1],
        doc_slug=row[2],
        title=row[3],
        snippet=row[4],
        rank=float(row[5]),
        document_id=row[6],
        updated_at=row[7],
        match=match,
    )


async def search_documents(
    session: AsyncSession, viewer: User | None, term: str, limit: int
) -> list[SearchResult]:
    """חיפוש טקסט מלא, עם נפילה לחיפוש מטושטש כשאין תוצאות."""
    term = term.strip()
    if not term:
        return []

    # websearch_to_tsquery ולא to_tsquery: הוא מבין מרכאות לביטוי מדויק
    # ומינוס להחרגה, והוא לעולם לא זורק שגיאת תחביר על קלט חופשי.
    # to_tsquery היה מתפוצץ על סימן שאלה בודד.
    tsquery = func.websearch_to_tsquery("simple", func.strip_nikud(term))

    rank = func.ts_rank(Document.search_vector, tsquery)
    snippet = func.ts_headline(
        "simple",
        # הקטע נלקח מהתוכן המקורי ולא מזה שהוסר ממנו הניקוד, כדי שמה
        # שמוצג יהיה מה שנכתב. המחיר: התאמה שנמצאה רק בזכות הסרת הניקוד
        # לא תסומן — התוצאה עדיין מופיעה, בלי ההדגשה.
        Document.content,
        tsquery,
        HEADLINE_OPTIONS,
    )

    query = _visible(
        select(
            Project.slug,
            Project.name,
            Document.slug,
            Document.title,
            snippet,
            rank.label("rank"),
            Document.id,
            Document.updated_at,
        ).join(Project, Document.project_id == Project.id),
        viewer,
    ).where(Document.search_vector.op("@@")(tsquery))

    # ts_rank מחזיר את אותו ערך למסמכים שונים לעיתים קרובות, ובלי
    # tiebreaker ייחודי הסדר משתנה בין הרצות (כלל 8).
    query = query.order_by(literal_column("rank").desc(), Document.id).limit(limit)

    rows = (await session.execute(query)).all()
    if rows:
        return [_row_to_result(row, "text") for row in rows]

    return await _fuzzy(session, term, limit, viewer)


async def _fuzzy(
    session: AsyncSession, term: str, limit: int, viewer: User | None
) -> list[SearchResult]:
    """נפילה לחיפוש דמיון על הכותרת.

    זה מה שתופס שגיאות כתיב. אין כאן LIKE ואין תבנית שהמשתמש שולט בה —
    similarity מקבל את המחרוזת כפרמטר, ולכן `%` בקלט הוא תו רגיל ולא
    wildcard (כלל 9).
    """
    similarity = func.similarity(Document.title, term)
    query = (
        _visible(
            select(
                Project.slug,
                Project.name,
                Document.slug,
                Document.title,
                func.left(Document.content, 200),
                similarity.label("sim"),
                Document.id,
                Document.updated_at,
            ).join(Project, Document.project_id == Project.id),
            viewer,
        )
        # האופרטור % הוא מה שמאפשר ל-ix_documents_title_trgm להיכנס
        # לתמונה. תנאי similarity(...) > x לבדו הוא ביטוי שהמתכנן לא
        # יכול לתרגם לאינדקס, והוא היה מריץ סריקה מלאה עם חישוב לכל שורה.
        # הסף המפורש נשאר אחריו, כי % נשען על pg_trgm.similarity_threshold
        # שהוא הגדרה גלובלית של החיבור ולא ערך שאנחנו שולטים בו כאן.
        .where(Document.title.op("%")(term))
        .where(similarity > TRIGRAM_THRESHOLD)
        .order_by(literal_column("sim").desc(), Document.id)
        .limit(limit)
    )

    rows = (await session.execute(query)).all()
    logger.info("חיפוש '%s' נפל לדמיון והחזיר %d תוצאות", term, len(rows))
    return [_row_to_result(row, "fuzzy") for row in rows]
