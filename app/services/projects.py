"""שליפת פרויקטים לפי כללי הנראות.

זו נקודת האכיפה היחידה של Visibility. כל נתיב שמגיע לפרויקט — ראוטר
או כלי MCP — חייב לעבור דרך כאן ולא לשכפל את הכללים.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app import slugs
from app.models import Project, User, Visibility
from app.services.errors import NotFound


async def load_project(
    session: AsyncSession, slug: str, viewer: User | None, *, with_documents: bool = False
) -> Project:
    """שולף פרויקט לפי הכללים של מי שמסתכל, או זורק NotFound."""
    query = select(Project).where(Project.slug == slugs.normalize(slug).lower())
    if with_documents:
        query = query.options(selectinload(Project.documents), selectinload(Project.links))

    project = (await session.execute(query)).scalar_one_or_none()
    if project is None:
        raise NotFound("project")

    if project.visibility is Visibility.PUBLIC:
        return project
    if viewer is not None and project.owner_id == viewer.id:
        return project
    raise NotFound("project")


async def owned_project(
    session: AsyncSession, slug: str, user: User, *, with_documents: bool = False
) -> Project:
    """כמו load_project, אבל דורש בעלות ולא רק נראות.

    load_project לבדה מחזירה גם פרויקט פומבי של מישהו אחר, ולכן כל
    נתיב כותב חייב את הבדיקה הנוספת הזו. היא ישבה בעבר בשני עותקים
    נפרדים — ב-routers/documents.py וב-routers/links.py — וכל תיקון
    באחד מהם היה חייב להיזכר גם בשני.
    """
    project = await load_project(session, slug, user, with_documents=with_documents)
    if project.owner_id != user.id:
        raise NotFound("project")
    return project


async def list_visible_projects(session: AsyncSession, viewer: User | None) -> list[Project]:
    """אנונימי מקבל פומביים; מחובר מקבל את שלו בלבד.

    שים לב לאסימטריה: משתמש מחובר אינו רואה פרויקטים פומביים של
    אחרים. זו התנהגות קיימת של המערכת, לא באג — אבל היא מפתיעה, ולכן
    היא מתועדת גם בתיאור הכלי ב-MCP.
    """
    query = select(Project).options(selectinload(Project.documents), selectinload(Project.links))
    if viewer is None:
        query = query.where(Project.visibility == Visibility.PUBLIC)
    else:
        query = query.where(Project.owner_id == viewer.id)

    # שם ואז id — בלי ה-id שני פרויקטים בעלי אותו שם מקבלים סדר שרירותי (כלל 8).
    query = query.order_by(Project.name, Project.id)
    return list((await session.execute(query)).scalars().all())
