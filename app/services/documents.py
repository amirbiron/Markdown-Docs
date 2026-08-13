"""שליפה ותחזוקה של מסמכים.

הפונקציות כאן אינן מבצעות commit. הן פועלות בתוך הסשן שהצרכן פתח,
כדי שהראוטר או כלי ה-MCP ישלטו בגבולות הטרנזקציה.
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import slugs
from app.models import Document, DocumentVersion, Project
from app.services.errors import NotFound


async def load_document(
    session: AsyncSession, project: Project, slug: str, *, lock: bool = False
) -> Document:
    """שולף מסמך בתוך פרויקט לפי ה-slug שלו."""
    query = select(Document).where(
        Document.project_id == project.id,
        Document.slug == slugs.normalize(slug).lower(),
    )
    if lock:
        # נועל את השורה עד סוף הטרנזקציה. בלי זה, קריאת המצב הקודם
        # והכתיבה החדשה הן שתי פעולות נפרדות ששתי בקשות יכולות לשזור
        # ביניהן — ואז גרסה נשמרת פעמיים או בכלל לא (כלל 2).
        query = query.with_for_update()

    document = (await session.execute(query)).scalar_one_or_none()
    if document is None:
        raise NotFound("document")
    return document


async def trim_versions(session: AsyncSession, document_id, keep: int) -> None:
    """שומר רק את N הגרסאות האחרונות.

    בלי זה, שמירה אוטומטית כל כמה שניות מייצרת היסטוריה שגדלה בלי גבול
    לאורך יום עריכה אחד. אותו היגיון כמו ניקוי הגיבויים מהדיסק.
    """
    survivors = (
        select(DocumentVersion.id)
        .where(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.created_at.desc(), DocumentVersion.id.desc())
        .limit(keep)
        .scalar_subquery()
    )
    await session.execute(
        delete(DocumentVersion).where(
            DocumentVersion.document_id == document_id,
            DocumentVersion.id.not_in(survivors),
        )
    )
