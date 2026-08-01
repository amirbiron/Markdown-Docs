"""בניית ארכיון הגיבוי.

הארכיון נבנה בזרימה: הבתים יוצאים תוך כדי הקריאה מבסיס הנתונים, ואין
שלב שבו כל התוכן יושב בזיכרון או על הדיסק. zipfile של Python יודע לכתוב
לזרם שאי אפשר לחזור בו אחורה, ולכן זה עובד גם מול תשובת HTTP.
"""

from __future__ import annotations

import io
import json
import logging
import zipfile
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.models import Document, Project, ProjectLink

logger = logging.getLogger("markdown_docs.backup")

# רמת דחיסה בינונית. Markdown נדחס מצוין, והפרש הזמן בין 6 ל-9 גדול
# בהרבה מהפרש הגודל.
COMPRESSION = zipfile.ZIP_DEFLATED


class _Buffer(io.RawIOBase):
    """מקבל את הבתים ש-zipfile כותב ומחזיק אותם עד שנשאבו.

    zipfile רוצה אובייקט שנראה כמו קובץ; אנחנו רוצים גנרטור. זו החוליה
    ביניהם: הוא צובר, והגנרטור שולף ומרוקן אחרי כל פריט.
    """

    def __init__(self) -> None:
        self._chunks: list[bytes] = []

    def writable(self) -> bool:
        return True

    def write(self, data) -> int:
        self._chunks.append(bytes(data))
        return len(data)

    def drain(self) -> bytes:
        out = b"".join(self._chunks)
        self._chunks.clear()
        return out


def safe_name(value: str, fallback: str) -> str:
    """שם רכיב נתיב בטוח לארכיון.

    slug מנורמל כבר לא מכיל מפרידי נתיב, אבל ארכיון הוא בדיוק המקום שבו
    הנחה כזאת מתנקמת: קובץ בשם "../../etc/passwd" בתוך ZIP הוא התקפת
    Zip Slip קלאסית, והיא מתבצעת אצל מי שפותח את הקובץ ולא אצלנו.
    """
    cleaned = (value or "").replace("\\", "/").replace("/", "-").strip().strip(".")
    cleaned = "".join(ch for ch in cleaned if ch.isprintable() and ch not in '<>:"|?*')
    return cleaned or fallback


async def _snapshot(session: AsyncSession) -> list[tuple[Project, list[Document], list[ProjectLink]]]:
    """קורא את כל התוכן בתוך הטרנזקציה הפתוחה.

    הקריאה מסודרת לפי מפתחות ייחודיים, כך ששני גיבויים של אותו מצב
    מייצרים בדיוק את אותו ארכיון (כלל 8).
    """
    projects = (await session.execute(select(Project).order_by(Project.name, Project.id))).scalars().all()

    out = []
    for project in projects:
        documents = (
            await session.execute(
                select(Document)
                .where(Document.project_id == project.id)
                .order_by(Document.position, Document.id)
            )
        ).scalars().all()
        links = (
            await session.execute(
                select(ProjectLink)
                .where(ProjectLink.project_id == project.id)
                .order_by(ProjectLink.position, ProjectLink.id)
            )
        ).scalars().all()
        out.append((project, list(documents), list(links)))
    return out


async def stream_archive() -> AsyncIterator[bytes]:
    """מייצר את הארכיון, פריט אחרי פריט.

    ה-session נפתח כאן ולא מגיע מ-Depends. תלות של FastAPI נסגרת בסוף
    הטיפול בבקשה, וזה קורה *לפני* שגוף התשובה הזורמת נצרך — כלומר
    ה-snapshot היה מת באמצע הכתיבה. הגנרטור מחזיק את הטרנזקציה שלו
    מהבייט הראשון ועד האחרון.
    """
    async with SessionLocal() as session:
        # REPEATABLE READ פותח snapshot אחד לכל הטרנזקציה. בלעדיו כל
        # שאילתה רואה מצב אחר, ועריכה באמצע הייצוא מייצרת ארכיון שבו
        # פרויקט אחד מלפני השינוי ואחר מאחריו. גיבוי לא עקבי גרוע
        # מגיבוי חסר, כי הוא נראה תקין.
        await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})

        content = await _snapshot(session)

        buffer = _Buffer()
        stamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

        with zipfile.ZipFile(buffer, "w", COMPRESSION) as archive:
            manifest = {
                "created_at": stamp,
                "projects": len(content),
                "documents": sum(len(docs) for _, docs, _ in content),
            }
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            chunk = buffer.drain()
            if chunk:
                yield chunk

            used_dirs: set[str] = set()
            for project, documents, links in content:
                folder = safe_name(project.slug, "project")
                # שני slugs שונים יכולים להצטמצם לאותו שם אחרי הניקוי.
                # בלי הבדיקה הזאת פרויקט אחד היה דורס את השני בשקט.
                base, n = folder, 2
                while folder in used_dirs:
                    folder = f"{base}-{n}"
                    n += 1
                used_dirs.add(folder)

                used_files: set[str] = set()
                for document in documents:
                    name = safe_name(document.slug, "document")
                    base_f, k = name, 2
                    while name in used_files:
                        name = f"{base_f}-{k}"
                        k += 1
                    used_files.add(name)

                    archive.writestr(f"{folder}/{name}.md", document.content or "")
                    chunk = buffer.drain()
                    if chunk:
                        yield chunk

                payload = {
                    "name": project.name,
                    "slug": project.slug,
                    "description": project.description,
                    "visibility": str(project.visibility),
                    "links": [
                        {"title": link.title, "url": link.url, "position": link.position}
                        for link in links
                    ],
                }
                archive.writestr(f"{folder}/links.json", json.dumps(payload, ensure_ascii=False, indent=2))
                chunk = buffer.drain()
                if chunk:
                    yield chunk

        # הסגירה כותבת את הספרייה המרכזית, ובלעדיה הקובץ אינו ZIP תקין.
        tail = buffer.drain()
        if tail:
            yield tail

        logger.info(
            "גיבוי נבנה: %d פרויקטים, %d מסמכים",
            len(content),
            sum(len(docs) for _, docs, _ in content),
        )


async def archive_bytes() -> bytes:
    """הארכיון כולו כמחרוזת בתים.

    למסלול המתוזמן, שממילא כותב קובץ שלם לדיסק ומצפין אותו. מסלול
    ההורדה משתמש בגנרטור ולא בזה.
    """
    parts = [chunk async for chunk in stream_archive()]
    return b"".join(parts)
