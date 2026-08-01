"""קישורים בתחתית עמוד הפרויקט."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app import urls
from app.db import get_session
from app.deps import optional_user, require_user
from app.models import Project, ProjectLink, User
from app.routers.projects import NOT_FOUND, load_project
from app.schemas import LinkCreate, LinkPublic, LinkUpdate

router = APIRouter(prefix="/projects/{project_slug}/links", tags=["links"])

LINK_NOT_FOUND = "הקישור לא נמצא"


async def _owned_project(session: AsyncSession, slug: str, user: User) -> Project:
    project = await load_project(session, slug, user)
    if project.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    return project


async def _load_link(session: AsyncSession, project: Project, link_id: uuid.UUID) -> ProjectLink:
    link = (
        await session.execute(
            select(ProjectLink).where(
                ProjectLink.id == link_id, ProjectLink.project_id == project.id
            )
        )
    ).scalar_one_or_none()
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, LINK_NOT_FOUND)
    return link


def _clean_url(raw: str) -> str:
    try:
        return urls.clean(raw)
    except urls.UrlError as error:
        raise HTTPException(422, str(error)) from None


@router.get("", response_model=list[LinkPublic])
async def list_links(
    project_slug: str,
    viewer: User | None = Depends(optional_user),
    session: AsyncSession = Depends(get_session),
):
    """קריאה פתוחה בפרויקט פומבי, כמו המסמכים."""
    project = await load_project(session, project_slug, viewer)
    links = (
        (
            await session.execute(
                select(ProjectLink)
                .where(ProjectLink.project_id == project.id)
                # position ואז id — בלי ה-id, שני קישורים באותו מקום
                # מחליפים סדר בין טעינות (כלל 8).
                .order_by(ProjectLink.position, ProjectLink.id)
            )
        )
        .scalars()
        .all()
    )
    return [LinkPublic.model_validate(link) for link in links]


@router.post("", response_model=LinkPublic, status_code=status.HTTP_201_CREATED)
async def create_link(
    project_slug: str,
    payload: LinkCreate,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    project = await _owned_project(session, project_slug, user)
    url = _clean_url(payload.url)

    if payload.position is None:
        position = (
            select(func.coalesce(func.max(ProjectLink.position), -1) + 1)
            .where(ProjectLink.project_id == project.id)
            .scalar_subquery()
        )
    else:
        position = payload.position

    statement = (
        pg_insert(ProjectLink)
        .values(
            project_id=project.id,
            title=payload.title.strip(),
            url=url,
            position=position,
        )
        .returning(ProjectLink.id)
    )
    link_id = (await session.execute(statement)).scalar_one()
    await session.commit()

    link = await session.get(ProjectLink, link_id)
    return LinkPublic.model_validate(link)


@router.patch("/{link_id}", response_model=LinkPublic)
async def update_link(
    project_slug: str,
    link_id: uuid.UUID,
    payload: LinkUpdate,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    project = await _owned_project(session, project_slug, user)
    link = await _load_link(session, project, link_id)

    if payload.title is not None:
        link.title = payload.title.strip()
    if payload.url is not None:
        link.url = _clean_url(payload.url)
    if payload.position is not None:
        link.position = payload.position

    await session.commit()
    await session.refresh(link)
    return LinkPublic.model_validate(link)


@router.delete("/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_link(
    project_slug: str,
    link_id: uuid.UUID,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    project = await _owned_project(session, project_slug, user)
    link = await _load_link(session, project, link_id)
    await session.delete(link)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
