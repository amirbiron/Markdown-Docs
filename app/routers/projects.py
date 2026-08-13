"""CRUD לפרויקטים, כולל מסלול הקריאה הציבורי."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import slugs
from app.db import get_session
from app.deps import optional_user, require_user
from app.models import Document, Project, ProjectLink, User
from app.schemas import ProjectCreate, ProjectPrivate, ProjectPublic, ProjectUpdate
from app.services import projects as project_service
from app.services.errors import NotFound

logger = logging.getLogger("markdown_docs.projects")

router = APIRouter(prefix="/projects", tags=["projects"])

# תשובה אחת גם למשאב שלא קיים וגם למשאב פרטי של מישהו אחר. 403 על
# פרויקט פרטי היה מאשר שהוא קיים, וזה מספיק כדי למפות את המערכת (כלל 3).
NOT_FOUND = "הפרויקט לא נמצא"


async def load_project(
    session: AsyncSession, slug: str, viewer: User | None, *, with_documents: bool = False
) -> Project:
    """עטיפת HTTP סביב שכבת ה-service.

    הלוגיקה עצמה יושבת ב-app/services/projects.py, כדי ששרת ה-MCP
    יוכל לאכוף את אותם כללי נראות בדיוק בלי לייבא HTTPException לתוך
    שכבה שאינה HTTP. כאן נשאר רק התרגום לשפת ה-API.
    """
    try:
        return await project_service.load_project(
            session, slug, viewer, with_documents=with_documents
        )
    except NotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND) from None


def _payload(project: Project, documents: list[Document], links: list[ProjectLink]) -> dict:
    """בונה את שדות התשובה משמות מפורשים.

    project.__dict__ נראה קצר יותר, אבל הוא מחזיר את מצב ה-ORM הפנימי:
    הוא כולל את _sa_instance_state, מדלג על attributes שלא נטענו, ומשתנה
    בשקט לפי commit ו-expire. שמות מפורשים נשברים ברעש אם שדה משתנה.
    """
    return {
        "slug": project.slug,
        "name": project.name,
        "description": project.description,
        "visibility": project.visibility,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
        "documents": documents,
        "links": links,
    }


def _ordered_documents(project: Project) -> list[Document]:
    """המיון נגמר ב-id (כלל 8).

    מסמכים חדשים מקבלים position עוקב, אבל אחרי גרירה ידנית או ייבוא
    בכמות אפשר בהחלט להגיע לשני מסמכים באותו מקום — ואז מיון בלי
    tiebreaker מחזיר סדר שמתהפך בין טעינות בלי סיבה נראית לעין.
    """
    return sorted(project.documents, key=lambda doc: (doc.position, str(doc.id)))


def _ordered_links(project: Project) -> list[ProjectLink]:
    """אותו כלל כמו במסמכים — position ואז id."""
    return sorted(project.links, key=lambda link: (link.position, str(link.id)))


@router.get("", response_model=list[ProjectPublic] | list[ProjectPrivate])
async def list_projects(
    viewer: User | None = Depends(optional_user),
    session: AsyncSession = Depends(get_session),
):
    """בלי session מוחזרים פומביים בלבד."""
    projects = await project_service.list_visible_projects(session, viewer)

    model = ProjectPrivate if viewer is not None else ProjectPublic
    return [
        model.model_validate(_payload(project, _ordered_documents(project), _ordered_links(project)))
        for project in projects
    ]


@router.post("", response_model=ProjectPrivate, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    try:
        slug = slugs.resolve(payload.slug, payload.name)
    except slugs.SlugError as error:
        raise HTTPException(422, str(error)) from None

    project = Project(
        owner_id=user.id,
        slug=slug,
        name=payload.name.strip(),
        description=payload.description,
        visibility=payload.visibility,
    )
    session.add(project)

    # בדיקת "האם ה-slug תפוס" ואז INSERT אינה אטומית — שתי בקשות במקביל
    # עוברות שתיהן את הבדיקה. האכיפה היא אילוץ ה-UNIQUE ב-DB, ואנחנו רק
    # מתרגמים את השגיאה שלו (כלל 2).
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "כבר קיים פרויקט עם המזהה הזה") from None

    await session.refresh(project)
    return ProjectPrivate.model_validate(_payload(project, [], []))


@router.get("/{slug}", response_model=ProjectPublic | ProjectPrivate)
async def get_project(
    slug: str,
    viewer: User | None = Depends(optional_user),
    session: AsyncSession = Depends(get_session),
):
    project = await load_project(session, slug, viewer, with_documents=True)
    is_owner = viewer is not None and project.owner_id == viewer.id
    model = ProjectPrivate if is_owner else ProjectPublic
    return model.model_validate(_payload(project, _ordered_documents(project), _ordered_links(project)))


@router.patch("/{slug}", response_model=ProjectPrivate)
async def update_project(
    slug: str,
    payload: ProjectUpdate,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
):
    project = await load_project(session, slug, user, with_documents=True)
    if project.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)

    # ה-slug לא ניתן לשינוי בכוונה: הוא הכתובת שאנשים שמרו ושלחו, ושינוי
    # שלו שובר כל קישור קיים בלי שום סימן.
    if payload.name is not None:
        project.name = payload.name.strip()
    if payload.description is not None:
        project.description = payload.description
    if payload.visibility is not None:
        project.visibility = payload.visibility

    # ה-slug נשמר לפני ה-commit. הוא לא ניתן לשינוי ולכן לא היה נכתב
    # מחדש, אבל קריאה של attribute כלשהו אחרי commit היא בדיוק הדפוס
    # שכלל 5 אוסר — עדיף ערך פרימיטיבי ביד.
    project_slug = project.slug
    await session.commit()

    # שליפה מחדש במקום refresh חלקי. אחרי UPDATE, עמודות עם onupdate —
    # כאן updated_at — מתפוגגות במפורש גם כש-expire_on_commit=False, כי
    # הערך החדש חושב ב-DB והסשן לא מכיר אותו. גישה אליהן אחר כך מנסה
    # לטעון אותן מהקשר סינכרוני, וזה MissingGreenlet (כלל 5).
    fresh = await load_project(session, project_slug, user, with_documents=True)
    return ProjectPrivate.model_validate(
        _payload(fresh, _ordered_documents(fresh), _ordered_links(fresh))
    )


@router.delete("/{slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    slug: str,
    user: User = Depends(require_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    project = await load_project(session, slug, user)
    if project.owner_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)

    # המסמכים, הגרסאות והקישורים נמחקים ב-CASCADE ברמת ה-DB, ולא בלולאה
    # בקוד שעלולה לקרוס באמצע ולהשאיר יתומים.
    # ה-slug נשמר לפני ה-commit. אחריו האובייקט מחוק ו-attributes שלו
    # פגי תוקף, וקריאה אליהם מנסה טעינה מחדש מהקשר סינכרוני (כלל 5).
    deleted_slug = project.slug
    await session.delete(project)
    await session.commit()
    logger.info("פרויקט נמחק (%s)", deleted_slug)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
