"""CRUD למסמכים, כולל שמירת גרסאות וסדר כתיבות."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.deps import optional_user, require_user
from app.models import Document, DocumentVersion, Project, User
from app.routers.projects import NOT_FOUND, load_project
from app.services import documents as document_service
from app.services import projects as project_service
from app.services.errors import Conflict, InvalidInput, NotFound
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
    """עטיפת HTTP סביב document_service.load_document."""
    try:
        return await document_service.load_document(session, project, slug, lock=lock)
    except NotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, DOC_NOT_FOUND) from None


async def _owned_project(session: AsyncSession, slug: str, user: User) -> Project:
    """עטיפת HTTP סביב project_service.owned_project.

    הבדיקה עצמה ישבה כאן וב-routers/links.py בשני עותקים זהים. עכשיו
    יש עותק אחד בשכבת ה-service, ושרת ה-MCP משתמש באותו אחד.
    """
    try:
        return await project_service.owned_project(session, slug, user)
    except NotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND) from None


async def _trim_versions(session: AsyncSession, document_id) -> None:
    await document_service.trim_versions(
        session, document_id, keep=settings.document_versions_kept
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
        document_id = await document_service.create_document(
            session,
            project,
            title=payload.title,
            content=payload.content,
            slug=payload.slug,
            position=payload.position,
        )
        await session.commit()
    except InvalidInput as error:
        raise HTTPException(422, str(error)) from None
    except Conflict:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "כבר קיים מסמך עם המזהה הזה בפרויקט"
        ) from None

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

    # סדר הכתיבות שייך לממשק ולא ללוגיקת המסמך: הוא נועד להגן על טאב
    # דפדפן מפני שמירה אוטומטית שלו עצמו שאיחרה. לכן הוא נשאר כאן ולא
    # עובר לשכבת ה-service, שאין לה מושג מה זה עורך.
    if payload.client_seq is not None:
        document.last_client_seq = payload.client_seq
        document.last_editor_id = payload.editor_id

    try:
        await document_service.apply_update(
            session,
            document,
            title=payload.title,
            content=payload.content,
            slug=payload.slug,
            slug_from_title=payload.slug_from_title,
            position=payload.position,
            keep_versions=settings.document_versions_kept,
        )
    except InvalidInput as error:
        # slug פסול הוא שגיאת קלט ולא התנגשות, ולכן 422 ולא 409.
        raise HTTPException(422, str(error)) from None
    except Conflict:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "כבר קיים מסמך עם המזהה הזה בפרויקט"
        ) from None

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
