"""חיפוש חוצה-פרויקטים.

השאילתה עצמה יושבת ב-app/services/search.py, כדי ששרת ה-MCP יריץ את
אותו חיפוש בדיוק. כאן נשאר רק מה שנחשף החוצה.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.deps import optional_user
from app.models import User
from app.schemas import SearchHit
from app.services import search as search_service

router = APIRouter(prefix="/search", tags=["search"])


@router.get("", response_model=list[SearchHit])
async def search(
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(default=20, ge=1, le=100),
    viewer: User | None = Depends(optional_user),
    session: AsyncSession = Depends(get_session),
):
    """חיפוש טקסט מלא, עם נפילה לחיפוש מטושטש כשאין תוצאות."""
    results = await search_service.search_documents(session, viewer, q, limit)
    return [
        SearchHit(
            project_slug=result.project_slug,
            project_name=result.project_name,
            doc_slug=result.doc_slug,
            title=result.title,
            snippet=result.snippet,
            rank=result.rank,
            # המזהה נחשף לבעלים בלבד. ראו ההערה ב-SearchHit.doc_id.
            doc_id=result.document_id if viewer is not None else None,
            updated_at=result.updated_at,
            match=result.match,
        )
        for result in results
    ]
