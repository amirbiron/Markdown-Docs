"""שליפה ותחזוקה של מסמכים.

הפונקציות כאן אינן מבצעות commit. הן פועלות בתוך הסשן שהצרכן פתח,
כדי שהראוטר או כלי ה-MCP ישלטו בגבולות הטרנזקציה.
"""

from __future__ import annotations

import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app import slugs
from app.models import Document, DocumentVersion, Project, User, Visibility
from app.services.errors import Conflict, InvalidInput, NotFound


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


async def load_document_by_id(
    session: AsyncSession,
    document_id: uuid.UUID,
    viewer: User | None,
    *,
    lock: bool = False,
    require_owner: bool = False,
) -> Document:
    """שולף מסמך לפי המזהה היציב שלו, אחרי בדיקת נראות מלאה.

    מזהה שמגיע מבחוץ הוא IDOR עד שהוכח אחרת. אימות שה-UUID תקין אינו
    בדיקת הרשאה — הוא רק אומר שהמחרוזת תקינה. לכן הפונקציה שולפת את
    הפרויקט יחד עם המסמך ומחילה עליו בדיוק את אותם כללים שמחיל
    services.projects.load_project, ולא רק בודקת שהשורה קיימת.

    require_owner נדרש לנתיבי כתיבה: פרויקט פומבי של מישהו אחר גלוי,
    אבל אינו ניתן לעריכה.
    """
    query = (
        select(Document)
        .where(Document.id == document_id)
        .options(selectinload(Document.project))
    )
    if lock:
        # נועל את שורת המסמך בלבד. selectinload מוציא את הפרויקט
        # בשאילתה נפרדת, ולכן FOR UPDATE כאן אינו נוגע בו (כלל 2).
        query = query.with_for_update(of=Document)

    document = (await session.execute(query)).scalar_one_or_none()
    if document is None:
        raise NotFound("document")

    project = document.project
    is_owner = viewer is not None and project.owner_id == viewer.id
    if require_owner:
        if not is_owner:
            raise NotFound("document")
    elif project.visibility is not Visibility.PUBLIC and not is_owner:
        raise NotFound("document")

    return document


async def list_project_documents(session: AsyncSession, project: Project) -> list[Document]:
    """מסמכי הפרויקט, בשאילתה מפורשת.

    בכוונה לא project.documents: היחס הזה נטען עצלנית, ובקוד אסינכרוני
    גישה אליו מחוץ להקשר שטען אותו זורקת MissingGreenlet (כלל 5).
    שאילתה מפורשת עובדת בכל הקשר, ומשאירה את מסלול השליפה הרגיל זול —
    הוא אינו צריך את הרשימה כלל.
    """
    return list(
        (
            await session.execute(
                select(Document)
                .where(Document.project_id == project.id)
                .order_by(Document.position, Document.id)
            )
        )
        .scalars()
        .all()
    )


async def list_versions(session: AsyncSession, document: Document) -> list[DocumentVersion]:
    """היסטוריית הגרסאות, מהחדשה לישנה."""
    return list(
        (
            await session.execute(
                select(DocumentVersion)
                .where(DocumentVersion.document_id == document.id)
                .order_by(DocumentVersion.created_at.desc(), DocumentVersion.id.desc())
            )
        )
        .scalars()
        .all()
    )


async def load_version(
    session: AsyncSession, version_id: uuid.UUID, viewer: User | None
) -> DocumentVersion:
    """שולף גרסה בודדת אחרי בדיקת נראות של המסמך שאליו היא שייכת.

    מזהה גרסה הוא מזהה משאב ככל אחר, ולכן הוא IDOR עד שהוכח אחרת:
    השליפה חייבת לעבור דרך המסמך ודרך הפרויקט, ולא להסתפק בכך
    שה-UUID קיים בטבלה.
    """
    version = (
        await session.execute(
            select(DocumentVersion).where(DocumentVersion.id == version_id)
        )
    ).scalar_one_or_none()
    if version is None:
        raise NotFound("version")

    # load_document_by_id הוא שמחיל את כללי הנראות. אם הוא זורק,
    # הגרסה אינה נגישה — בלי קשר לכך שהיא קיימת.
    await load_document_by_id(session, version.document_id, viewer)
    return version


async def create_document(
    session: AsyncSession,
    project: Project,
    *,
    title: str,
    content: str = "",
    slug: str | None = None,
    position: int | None = None,
) -> uuid.UUID:
    """יוצר מסמך ומחזיר את המזהה שלו. אינו מבצע commit.

    ה-position מחושב בתוך ה-INSERT ולא בשאילתה נפרדת: בדיקה ואז
    כתיבה הן שתי פעולות ששתי בקשות מקבילות משזרות ביניהן (כלל 2).
    """
    try:
        resolved = slugs.resolve(slug, title)
    except slugs.SlugError as error:
        raise InvalidInput(str(error)) from None

    if position is None:
        next_position = (
            select(func.coalesce(func.max(Document.position), -1) + 1)
            .where(Document.project_id == project.id)
            .scalar_subquery()
        )
    else:
        next_position = position

    statement = (
        pg_insert(Document)
        .values(
            project_id=project.id,
            slug=resolved,
            title=title.strip(),
            content=content,
            position=next_position,
        )
        .returning(Document.id)
    )

    try:
        return (await session.execute(statement)).scalar_one()
    except IntegrityError:
        await session.rollback()
        raise Conflict("document") from None


async def apply_update(
    session: AsyncSession,
    document: Document,
    *,
    title: str | None = None,
    content: str | None = None,
    slug: str | None = None,
    slug_from_title: bool = False,
    position: int | None = None,
    keep_versions: int,
) -> None:
    """מחיל שינוי על מסמך שכבר ננעל, כולל שמירת גרסה. בלי commit.

    זו נקודת הכתיבה היחידה. מסלול עדכון שני היה יוצר גרסאות בכללים
    משלו, והיסטוריית המסמך הייתה תלויה בשאלה דרך איזה ממשק נערך.

    המסמך חייב להגיע נעול (with_for_update), אחרת קריאת התוכן הקודם
    והכתיבה החדשה הן שתי פעולות ששתי בקשות משזרות ביניהן (כלל 2).
    """
    # הגרסה נשמרת רק כשהתוכן באמת השתנה. בלי התנאי, כל שמירה
    # אוטומטית הייתה מייצרת עותק זהה.
    if content is not None and content != document.content:
        session.add(DocumentVersion(document_id=document.id, content=document.content))

    if title is not None:
        document.title = title.strip()

    # slug מפורש גובר על גזירה מהכותרת, כדי שבקשה שנותנת את שניהם לא
    # תהיה תלויה בסדר הבדיקות כאן.
    #
    # שימו לב שברירת המחדל היא לא לגעת ב-slug. זה מה שמאפשר לצרכן
    # שמחזיק הקשר בין קריאות — שרת ה-MCP — לערוך מסמך בלי לשנות את
    # הכתובת שלו בלי כוונה.
    if slug is not None or (slug_from_title and title is not None):
        try:
            document.slug = slugs.resolve(slug, title or "")
        except slugs.SlugError as error:
            await session.rollback()
            raise InvalidInput(str(error)) from None
    if content is not None:
        document.content = content
    if position is not None:
        document.position = position

    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise Conflict("document") from None

    await trim_versions(session, document.id, keep=keep_versions)


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
