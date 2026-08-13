"""הלוגיקה של הכלים.

הקובץ הזה אינו מייבא דבר מ-MCP או מ-Starlette. כל פונקציה מקבלת
סשן, זהות ופרמטרים, ומחזירה dict — ולכן אפשר לבדוק כל כלי בלי
להרים שרת.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.mcp.auth import Identity
from app.mcp.formatting import clamp, did_you_mean, document_url, err, ok
from app.services import documents as document_service
from app.services import projects as project_service
from app.services import search as search_service
from app.services.errors import Conflict, InvalidInput, NotFound

# תקרות. חותכים ולא דוחים — ראו formatting.clamp.
SEARCH_LIMIT_DEFAULT = 20
SEARCH_LIMIT_MAX = 50

# כמה תוצאות מקבלות תוכן מלא כש-include_content דלוק. התקרה נמוכה
# בכוונה: תוכן של עשרים מסמכים בתשובה אחת שורף יותר הקשר משהוא חוסך.
CONTENT_LIMIT_DEFAULT = 3
CONTENT_LIMIT_MAX = 10


def _document_ref(document, project) -> dict[str, Any]:
    """הזהות של מסמך, תמיד באותה צורה.

    המזהה, שני ה-slugים והכותרת יוצאים יחד בכוונה. צרכן שמחזיק רק
    slug נשבר בשקט כשהוא משתנה; צרכן שמחזיק את כולם יכול תמיד לחזור
    למזהה היציב.
    """
    return {
        "id": str(document.id),
        "project_slug": project.slug,
        "doc_slug": document.slug,
        "title": document.title,
        "url": document_url(project.slug, document.slug),
    }


# ── מפה ───────────────────────────────────────────────────────────────


async def map_documents(session: AsyncSession, identity: Identity) -> dict:
    """כל הפרויקטים והמסמכים בקריאה אחת, בלי תוכן.

    זו הקריאה שמונעת את המסלול הארוך. בלעדיה, "מה כתוב ב-roadmap"
    היה דורש רשימת פרויקטים, ואז פרויקט כדי למצוא את ה-slug, ואז את
    המסמך — שלוש קריאות לכל שאלה.
    """
    projects = await project_service.list_visible_projects(session, identity.user)

    payload = []
    for project in projects:
        documents = sorted(project.documents, key=lambda d: (d.position, str(d.id)))
        payload.append(
            {
                "slug": project.slug,
                "name": project.name,
                "description": project.description,
                "visibility": project.visibility.value,
                "updated_at": project.updated_at.isoformat(),
                "documents": [
                    {
                        "id": str(document.id),
                        "slug": document.slug,
                        "title": document.title,
                        "position": document.position,
                        "updated_at": document.updated_at.isoformat(),
                        "size_bytes": len(document.content.encode("utf-8")),
                        "url": document_url(project.slug, document.slug),
                    }
                    for document in documents
                ],
            }
        )

    return ok(
        projects=payload,
        project_count=len(payload),
        document_count=sum(len(p["documents"]) for p in payload),
    )


# ── חיפוש ─────────────────────────────────────────────────────────────


async def search(
    session: AsyncSession,
    identity: Identity,
    query: str,
    limit: Any = None,
    include_content: bool = False,
    content_limit: Any = None,
) -> dict:
    """חיפוש, עם אפשרות להחזיר תוכן מלא לתוצאות המובילות.

    include_content הוא מה שהופך שאלה לתשובה בקריאה אחת במקום שתיים.
    """
    term = (query or "").strip()
    if not term:
        return err("empty_query", message="נדרש מונח חיפוש")

    capped = clamp(limit, 1, SEARCH_LIMIT_MAX, SEARCH_LIMIT_DEFAULT)
    results = await search_service.search_documents(session, identity.user, term, capped)

    if not results:
        # לא רק "לא נמצא": מחזירים את שמות המסמכים הקיימים כדי
        # שהמודל יוכל לנסות שוב במונח קרוב.
        projects = await project_service.list_visible_projects(session, identity.user)
        titles = [d.title for p in projects for d in p.documents]
        return err(
            "no_results",
            query=term,
            suggestions=did_you_mean(term, titles),
            available_titles=titles[:50],
            message="לא נמצאו תוצאות. suggestions מכיל כותרות קרובות.",
        )

    hits = [
        {
            "id": str(result.document_id),
            "project_slug": result.project_slug,
            "project_name": result.project_name,
            "doc_slug": result.doc_slug,
            "title": result.title,
            "snippet": result.snippet,
            "rank": result.rank,
            "updated_at": result.updated_at.isoformat(),
            "url": document_url(result.project_slug, result.doc_slug),
        }
        for result in results
    ]

    if include_content:
        top = clamp(content_limit, 1, CONTENT_LIMIT_MAX, CONTENT_LIMIT_DEFAULT)
        for hit in hits[:top]:
            try:
                document = await document_service.load_document_by_id(
                    session, uuid.UUID(hit["id"]), identity.user
                )
            except NotFound:
                # מרוץ טבעי: החיפוש והשליפה הם שתי פעולות נפרדות, ובין
                # לבין המסמך יכול להימחק או לשנות נראות. זה מצב צפוי ולא
                # תקלת שרת — סימון השדה עדיף על הפלת כל הקריאה.
                hit["content_unavailable"] = True
                continue
            hit["content"] = document.content

    return ok(
        query=term,
        # "text" מול "fuzzy" — הסולם של rank שונה בין השניים, וכל
        # תשובה מגיעה ממסלול אחד בלבד.
        match=results[0].match,
        count=len(hits),
        results=hits,
    )


# ── שליפת מסמך ────────────────────────────────────────────────────────


async def get_document(
    session: AsyncSession,
    identity: Identity,
    document_id: str | None = None,
    project_slug: str | None = None,
    doc_slug: str | None = None,
    title: str | None = None,
) -> dict:
    """שולף מסמך לפי המזהה היציב, לפי צמד slugים, או לפי כותרת.

    המזהה עדיף על כל השאר: ה-slug משתנה בכל שינוי כותרת, וה-slug
    שהתפנה יכול להיתפס על ידי מסמך אחר.
    """
    if document_id:
        try:
            parsed = uuid.UUID(document_id)
        except ValueError:
            return err("invalid_id", message="המזהה אינו UUID תקין")
        try:
            document = await document_service.load_document_by_id(session, parsed, identity.user)
        except NotFound:
            return err("not_found", message="לא נמצא מסמך עם המזהה הזה")
        return ok(document=_serialize(document, document.project))

    if project_slug and doc_slug:
        try:
            project = await project_service.load_project(session, project_slug, identity.user)
        except NotFound:
            return await _project_not_found(session, identity, project_slug)
        try:
            document = await document_service.load_document(session, project, doc_slug)
        except NotFound:
            return await _doc_not_found(session, identity, project, doc_slug)
        return ok(document=_serialize(document, project))

    if title:
        return await _by_title(session, identity, title)

    return err(
        "missing_identifier",
        message="נדרש document_id, או project_slug יחד עם doc_slug, או title",
    )


async def _by_title(session: AsyncSession, identity: Identity, title: str) -> dict:
    """התאמה לפי כותרת, עם החזרת מועמדים כשהיא אינה חד-משמעית."""
    projects = await project_service.list_visible_projects(session, identity.user)
    pairs = [(project, document) for project in projects for document in project.documents]

    needle = title.strip().casefold()
    exact = [(p, d) for p, d in pairs if d.title.strip().casefold() == needle]

    if len(exact) == 1:
        project, document = exact[0]
        return ok(document=_serialize(document, project))

    if len(exact) > 1:
        # לא בוחרים עבור המודל. מחזירים את כל המועמדים עם המזהים,
        # כדי שהקריאה הבאה תהיה חד-משמעית.
        return err(
            "ambiguous_title",
            title=title,
            candidates=[_document_ref(d, p) for p, d in exact],
            message="יותר ממסמך אחד עם הכותרת הזו. בחרו לפי id.",
        )

    titles = [d.title for _, d in pairs]
    close = did_you_mean(title, titles)
    return err(
        "not_found",
        title=title,
        suggestions=close,
        candidates=[_document_ref(d, p) for p, d in pairs if d.title in close],
        message="לא נמצאה כותרת מתאימה. suggestions מכיל כותרות קרובות.",
    )


async def _project_not_found(session: AsyncSession, identity: Identity, slug: str) -> dict:
    projects = await project_service.list_visible_projects(session, identity.user)
    slugs = [p.slug for p in projects]
    return err(
        "project_not_found",
        project_slug=slug,
        suggestions=did_you_mean(slug, slugs),
        available_projects=slugs,
        message="הפרויקט לא נמצא. available_projects מכיל את הקיימים.",
    )


async def _doc_not_found(session: AsyncSession, identity: Identity, project, slug: str) -> dict:
    documents = await document_service.list_project_documents(session, project)
    slugs = [d.slug for d in documents]
    return err(
        "not_found",
        project_slug=project.slug,
        doc_slug=slug,
        suggestions=did_you_mean(slug, slugs),
        available_documents=[_document_ref(d, project) for d in documents],
        message="המסמך לא נמצא בפרויקט. available_documents מכיל את הקיימים.",
    )


def _serialize(document, project) -> dict:
    return {
        **_document_ref(document, project),
        "content": document.content,
        "position": document.position,
        "created_at": document.created_at.isoformat(),
        "updated_at": document.updated_at.isoformat(),
        "size_bytes": len(document.content.encode("utf-8")),
    }


# ── גרסאות ────────────────────────────────────────────────────────────


async def list_versions(session: AsyncSession, identity: Identity, document_id: str) -> dict:
    """היסטוריית הגרסאות של מסמך, בלי התוכן עצמו."""
    try:
        parsed = uuid.UUID(document_id)
    except ValueError:
        return err("invalid_id", message="המזהה אינו UUID תקין")

    try:
        document = await document_service.load_document_by_id(session, parsed, identity.user)
    except NotFound:
        return err("not_found", message="לא נמצא מסמך עם המזהה הזה")

    versions = await document_service.list_versions(session, document)
    return ok(
        document=_document_ref(document, document.project),
        count=len(versions),
        versions=[
            {
                "id": str(version.id),
                "created_at": version.created_at.isoformat(),
                "size_bytes": len(version.content.encode("utf-8")),
            }
            for version in versions
        ],
        message=(
            "התוכן עצמו נשלף עם mdocs_get_version לפי id של גרסה."
            if versions
            else "אין עדיין גרסאות. גרסה נוצרת רק כשתוכן המסמך משתנה."
        ),
    )


async def create_document(
    session: AsyncSession,
    identity: Identity,
    project_slug: str,
    title: str,
    content: str = "",
    slug: str | None = None,
) -> dict:
    """יוצר מסמך חדש בפרויקט."""
    if not (title or "").strip():
        return err("missing_title", message="נדרשת כותרת")

    try:
        project = await project_service.owned_project(session, project_slug, identity.user)
    except NotFound:
        return await _project_not_found(session, identity, project_slug)

    try:
        document_id = await document_service.create_document(
            session, project, title=title, content=content, slug=slug
        )
        await session.commit()
    except InvalidInput as error:
        return err("invalid_slug", message=str(error))
    except Conflict:
        return err(
            "slug_taken",
            message="כבר קיים מסמך עם ה-slug הזה בפרויקט. בחרו slug אחר או השמיטו אותו.",
        )

    document = await document_service.load_document_by_id(session, document_id, identity.user)
    return ok(document=_serialize(document, project), created=True)


async def _load_for_write(session: AsyncSession, identity: Identity, document_id: str):
    """שולף מסמך נעול לכתיבה, או מחזיר dict של שגיאה.

    מחזיר זוג (document, error) כי שני הנתיבים הכותבים צריכים בדיוק את
    אותה הכנה, ושכפול שלה היה מזמין הבדל בין השניים.
    """
    try:
        parsed = uuid.UUID(document_id)
    except ValueError:
        return None, err("invalid_id", message="המזהה אינו UUID תקין")

    try:
        document = await document_service.load_document_by_id(
            session, parsed, identity.user, lock=True, require_owner=True
        )
    except NotFound:
        return None, err(
            "not_found",
            message="לא נמצא מסמך שניתן לעריכה עם המזהה הזה. השתמשו ב-mdocs_map כדי לקבל מזהים.",
        )
    return document, None


async def update_document(
    session: AsyncSession,
    identity: Identity,
    document_id: str,
    content: str | None = None,
    title: str | None = None,
    new_slug: str | None = None,
) -> dict:
    """מעדכן מסמך קיים. התוכן הקודם נשמר כגרסה.

    ה-slug אינו משתנה אלא אם התבקש במפורש דרך new_slug.
    """
    if content is None and title is None and new_slug is None:
        return err("nothing_to_update", message="נדרש לפחות content, title או new_slug")

    document, error = await _load_for_write(session, identity, document_id)
    if error is not None:
        return error

    project = document.project
    try:
        await document_service.apply_update(
            session,
            document,
            title=title,
            content=content,
            slug=new_slug,
            keep_versions=get_settings().document_versions_kept,
        )
        await session.commit()
    except InvalidInput as inner:
        return err("invalid_slug", message=str(inner))
    except Conflict:
        return err("slug_taken", message="כבר קיים מסמך עם ה-slug הזה בפרויקט")

    await session.refresh(document)
    return ok(document=_serialize(document, project), updated=True)


async def append_document(
    session: AsyncSession, identity: Identity, document_id: str, text: str
) -> dict:
    """מוסיף טקסט בסוף מסמך, בלי לשלוח את כולו מחדש.

    שימושי לעדכון roadmap או יומן: הוספת שורה אינה דורשת קריאה של כל
    המסמך והחזרתו.
    """
    if not text:
        return err("empty_text", message="נדרש טקסט להוספה")

    document, error = await _load_for_write(session, identity, document_id)
    if error is not None:
        return error

    project = document.project
    # שורה ריקה מפרידה בין מה שהיה למה שנוסף, אחרת ההוספה נדבקת
    # לפסקה האחרונה ומשנה את משמעות המארקדאון.
    separator = "" if not document.content or document.content.endswith("\n\n") else "\n\n"
    merged = f"{document.content}{separator}{text}"

    try:
        await document_service.apply_update(
            session,
            document,
            content=merged,
            keep_versions=get_settings().document_versions_kept,
        )
        await session.commit()
    except Conflict:
        return err("conflict", message="העדכון נכשל בגלל התנגשות. נסו שוב.")

    await session.refresh(document)
    return ok(document=_serialize(document, project), appended=True)


async def get_version(session: AsyncSession, identity: Identity, version_id: str) -> dict:
    """התוכן של גרסה קודמת.

    יכולת שלא הייתה קיימת ב-API: הגרסאות נשמרו בטבלה, אבל רק גודלן
    ותאריכן נחשפו — התוכן עצמו לא היה נגיש בשום נתיב.
    """
    try:
        parsed = uuid.UUID(version_id)
    except ValueError:
        return err("invalid_id", message="המזהה אינו UUID תקין")

    try:
        version = await document_service.load_version(session, parsed, identity.user)
    except NotFound:
        return err("not_found", message="לא נמצאה גרסה עם המזהה הזה")

    return ok(
        version={
            "id": str(version.id),
            "document_id": str(version.document_id),
            "created_at": version.created_at.isoformat(),
            "content": version.content,
            "size_bytes": len(version.content.encode("utf-8")),
        }
    )
