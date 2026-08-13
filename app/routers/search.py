"""חיפוש חוצה-פרויקטים."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps import optional_user
from app.models import Document, Project, User, Visibility
from app.schemas import SearchHit

logger = logging.getLogger("markdown_docs.search")

router = APIRouter(prefix="/search", tags=["search"])

# סף הדמיון לחיפוש המטושטש. נמוך מדי מחזיר רעש, גבוה מדי לא סובל אף
# שגיאת כתיב. 0.3 היא ברירת המחדל של pg_trgm ועובדת סביר על עברית.
TRIGRAM_THRESHOLD = 0.3

# הסימנים סביב ההתאמה בקטע. לא תגיות HTML, כי הלקוח מרנדר רכיבי React
# ולא מזריק HTML — שרשרת שהייתה הופכת כל תוצאת חיפוש לווקטור הזרקה.
HEADLINE_OPTIONS = "StartSel=«, StopSel=», MaxWords=28, MinWords=12, ShortWord=2, MaxFragments=1"


def _visible(query, viewer: User | None):
    """מגביל לפרויקטים שמותר למי שמסתכל לראות."""
    if viewer is None:
        return query.where(Project.visibility == Visibility.PUBLIC)
    return query.where(Project.owner_id == viewer.id)


@router.get("", response_model=list[SearchHit])
async def search(
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(default=20, ge=1, le=100),
    viewer: User | None = Depends(optional_user),
    session: AsyncSession = Depends(get_session),
):
    """חיפוש טקסט מלא, עם נפילה לחיפוש מטושטש כשאין תוצאות."""
    term = q.strip()
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
        return [_hit(row, match="text", viewer=viewer) for row in rows]

    return await _fuzzy(session, term, limit, viewer)


def _hit(row, *, match: str, viewer: User | None) -> SearchHit:
    """בונה תוצאה אחת. שני המסלולים בוחרים את אותן עמודות באותו סדר."""
    return SearchHit(
        project_slug=row[0],
        project_name=row[1],
        doc_slug=row[2],
        title=row[3],
        snippet=row[4],
        rank=float(row[5]),
        # המזהה נחשף רק לבעלים. ראו ההערה ב-SearchHit.doc_id.
        doc_id=row[6] if viewer is not None else None,
        updated_at=row[7],
        match=match,
    )


async def _fuzzy(session: AsyncSession, term: str, limit: int, viewer: User | None):
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
    return [_hit(row, match="fuzzy", viewer=viewer) for row in rows]
