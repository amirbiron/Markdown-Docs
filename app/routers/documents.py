"""CRUD למסמכים, כולל שמירת גרסאות וסדר כתיבות."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import slugs
from app.config import get_settings
from app.db import get_session
from app.deps import optional_user, require_user
from app.models import Document, DocumentVersion, Project, User
from app.routers.projects import NOT_FOUND, load_project
from app.schemas import (
    DocumentCreate,
    DocumentPrivate,
    DocumentPublic,
    DocumentUpdate,
    DocumentWriteResult,
    VersionSummary,
)

logger = logging.getLogger("markdown_docs.documents")
settings = get_settings()

router = APIRouter(prefix="/projects/{project_slug}/docs", tags=["documents"])

DOC_NOT_FOUND = "המסמך לא נמצא"


async def _load_document(session: AsyncSession, project: Project, slug: str, *, lock: bool = False) -> Document:
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
        raise HTTPException(status.HTTP_404_NOT_FOUND, DOC_NOT_FOUND)
    return document


async def _owned_project(session: AsyncSession, slug: str, user: User) -> Project:
    project = await load_project(session, slug, user)
    if project.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    return project


async def _trim_versions(session: AsyncSession, document_id) -> None:
    """שומר רק את N הגרסאות האחרונות.

    בלי זה, שמירה אוטומטית כל כמה שניות מייצרת היסטוריה שגדלה בלי גבול
    לאורך יום עריכה אחד. אותו היגיון כמו ניקוי הגיבויים מהדיסק.
    """
    keep = settings.document_versions_kept
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


@router.post("", response_model=DocumentPrivate, status_code=status.HTTP_201_CREATED)
async def create_document(
    project_slug: str,
    payload: DocumentCreate,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    project = await _owned_project(session, project_slug, user)

    try:
        slug = slugs.resolve(payload.slug, payload.title)
    except slugs.SlugError as error:
        raise HTTPException(422, str(error)) from None

    # המקום מחושב *בתוך* פקודת ה-INSERT, ולא בשאילתה נפרדת שקודמת לה.
    # SELECT MAX ואז INSERT הן שתי פעולות ששתי יצירות במקביל יכולות
    # לשזור ביניהן, ושתיהן היו מקבלות את אותו מקום (כלל 2).
    if payload.position is None:
        next_position = (
            select(func.coalesce(func.max(Document.position), -1) + 1)
            .where(Document.project_id == project.id)
            .scalar_subquery()
        )
    else:
        next_position = payload.position

    statement = (
        pg_insert(Document)
        .values(
            project_id=project.id,
            slug=slug,
            title=payload.title.strip(),
            content=payload.content,
            position=next_position,
        )
        .returning(Document.id)
    )

    try:
        document_id = (await session.execute(statement)).scalar_one()
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "כבר קיים מסמך עם המזהה הזה בפרויקט") from None

    document = await session.get(Document, document_id)
    return DocumentPrivate.model_validate(document)


@router.get("/{doc_slug}", response_model=DocumentPublic | DocumentPrivate)
async def get_document(
    project_slug: str,
    doc_slug: str,
    viewer: User | None = Depends(optional_user),
    session: AsyncSession = Depends(get_session),
):
    project = await load_project(session, project_slug, viewer)
    document = await _load_document(session, project, doc_slug)
    is_owner = viewer is not None and project.owner_id == viewer.id
    model = DocumentPrivate if is_owner else DocumentPublic
    return model.model_validate(document)


@router.put("/{doc_slug}", response_model=DocumentWriteResult)
async def update_document(
    project_slug: str,
    doc_slug: str,
    payload: DocumentUpdate,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    project = await _owned_project(session, project_slug, user)
    document = await _load_document(session, project, doc_slug, lock=True)

    # סדר הכתיבות. בקשה עם מונה שאינו גדול מהאחרון שהתקבל הגיעה מאוחר
    # ואיננה רלוונטית — מחזירים 200 עם המצב הנוכחי ולא שגיאה, כי מבחינת
    # המשתמש לא קרה שום דבר רע.
    # הדחייה חלה רק כשהבקשה הגיעה *מאותו עורך*. עורך אחר שהתחיל למנות
    # מאפס אינו "מאחר" — הוא פשוט עורך אחר, ושם הכלל הוא האחרון מנצח.
    same_editor = payload.editor_id is not None and payload.editor_id == document.last_editor_id
    if same_editor and payload.client_seq is not None and payload.client_seq <= document.last_client_seq:
        logger.info(
            "כתיבה ישנה נדחתה (%s/%s): seq=%d, אחרון=%d",
            project.slug,
            document.slug,
            payload.client_seq,
            document.last_client_seq,
        )
        # בונים את התשובה *לפני* ה-rollback. אחריו כל ה-attributes של
        # האובייקט פגי תוקף, וקריאה אליהם מנסה לטעון אותם מחדש מתוך
        # הקשר סינכרוני — וזה בדיוק MissingGreenlet (כלל 5).
        snapshot = DocumentPrivate.model_validate(document)
        await session.rollback()  # משחרר את הנעילה שנתפסה ב-FOR UPDATE
        return DocumentWriteResult(applied=False, document=snapshot)

    # הגרסה הקודמת נשמרת רק כשהתוכן באמת השתנה. בלי התנאי, כל שמירה
    # אוטומטית שלא שינתה כלום הייתה מייצרת עוד עותק זהה.
    if payload.content is not None and payload.content != document.content:
        session.add(DocumentVersion(document_id=document.id, content=document.content))

    if payload.title is not None:
        document.title = payload.title.strip()
    # slug מפורש גובר על גזירה מהכותרת, כדי שבקשה שנותנת את שניהם לא
    # תהיה תלויה בסדר הבדיקות כאן.
    if payload.slug is not None or (payload.slug_from_title and payload.title is not None):
        # אותו אימות בדיוק כמו ביצירה. slug פסול הוא שגיאת קלט ולא
        # התנגשות, ולכן 422 ולא 409.
        try:
            document.slug = slugs.resolve(payload.slug, payload.title or "")
        except slugs.SlugError as error:
            await session.rollback()
            raise HTTPException(422, str(error)) from None
    if payload.content is not None:
        document.content = payload.content
    if payload.position is not None:
        document.position = payload.position
    if payload.client_seq is not None:
        document.last_client_seq = payload.client_seq
        document.last_editor_id = payload.editor_id

    # ה-flush הוא המקום שבו UniqueConstraint על (project_id, slug) נאכף.
    # התפיסה כאן ולא בדיקת SELECT מראש, מאותה סיבה שבה היצירה עושה את
    # זה: בדיקה ואז כתיבה הן שתי פעולות ששתי בקשות מקבילות משזרות
    # ביניהן, ושתיהן היו עוברות אותה (כלל 2).
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "כבר קיים מסמך עם המזהה הזה בפרויקט"
        ) from None

    await _trim_versions(session, document.id)
    await session.commit()
    await session.refresh(document)

    return DocumentWriteResult(applied=True, document=DocumentPrivate.model_validate(document))


@router.get("/{doc_slug}/versions", response_model=list[VersionSummary])
async def list_versions(
    project_slug: str,
    doc_slug: str,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    """היסטוריית הגרסאות. לבעלים בלבד — היא לא חלק מהתשובה הציבורית."""
    project = await _owned_project(session, project_slug, user)
    document = await _load_document(session, project, doc_slug)

    versions = (
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
    return [
        VersionSummary(created_at=v.created_at, size_bytes=len(v.content.encode("utf-8")))
        for v in versions
    ]


@router.delete("/{doc_slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    project_slug: str,
    doc_slug: str,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    project = await _owned_project(session, project_slug, user)
    document = await _load_document(session, project, doc_slug)
    await session.delete(document)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
