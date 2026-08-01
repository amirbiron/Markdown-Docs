"""הורדת גיבוי מלא, ומצב מנגנון הגיבוי."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse, StreamingResponse

from app import scheduler
from app.backup import stream_archive
from app.config import get_settings
from app.deps import require_user
from app.models import User

router = APIRouter(prefix="/backup", tags=["backup"])


@router.get(".zip")
async def download_backup(user: User = Depends(require_user)) -> StreamingResponse:
    """הארכיון המלא, בזרימה.

    require_user ולא optional_user: הארכיון כולל גם פרויקטים פרטיים,
    ולכן הוא לעולם לא ציבורי — גם כשחלק מהתוכן שבתוכו כן.
    """
    name = "markdown-docs-" + datetime.now(UTC).strftime("%Y%m%d-%H%M") + ".zip"
    return StreamingResponse(
        stream_archive(),
        media_type="application/zip",
        # ASCII בלבד בשם: כותרת עם תווים עבריים שוברת חלק מהלקוחות.
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.get("")
async def backup_status(user: User = Depends(require_user)) -> JSONResponse:
    """מצב מנגנון הגיבוי.

    שלוש שאלות, ובכוונה בראש התשובה: האם המנגנון פעיל, מתי רץ לאחרונה,
    ומתי הבא. גיבוי שאף אחד לא רואה הוא גיבוי שאף אחד לא מגלה שהפסיק
    לעבוד — וה-log של Render אינו מקום שבודקים בו כל יום.
    """
    settings = get_settings()

    # מעבר אחד ו-stat אחד לכל קובץ. שני מעברים הרחיבו את החלון שבו קובץ
    # נמחק בין אחד לשני — גיזום, שחזור, או ניקוי ידני — ואז הקריאה
    # השנייה זורקת והמסך שאמור לדווח על מצב הגיבוי מחזיר 500.
    entries = []
    total = 0
    for path in scheduler.existing_backups():
        try:
            size = path.stat().st_size
        except OSError:
            continue
        total += size
        stamp = scheduler.stamp_of(path)
        entries.append(
            {"name": path.name, "bytes": size, "at": stamp.isoformat() if stamp else None}
        )

    return JSONResponse(
        {
            "enabled": settings.backup_enabled,
            "running": scheduler.is_running(),
            "every_hours": settings.backup_every_hours,
            "keep": settings.backup_keep,
            "offsite_enabled": settings.backup_telegram_enabled,
            "last_run": scheduler.last_run(),
            "next_run_at": scheduler.next_run_at(),
            "total_bytes": total,
            "backups": entries,
        }
    )


@router.post("/run", status_code=status.HTTP_200_OK)
async def run_backup_now(user: User = Depends(require_user)) -> JSONResponse:
    """מפעיל גיבוי עכשיו, בלי להמתין לתזמון.

    force=True מדלג על בדיקת "האם עברו 24 שעות" — מי שלחץ על הכפתור
    התכוון לגבות עכשיו. הוא אינו מדלג על הנעילה.
    """
    result = await scheduler.run_guarded(force=True)

    if not result["ok"]:
        # 409 ולא 500: תפוס אינו שבור. בלי ההבחנה הזאת שני המצבים
        # נראים למשתמש אותו דבר, והוא לא יודע אם לנסות שוב או לחקור.
        if result.get("error") == scheduler.ALREADY_RUNNING:
            raise HTTPException(status.HTTP_409_CONFLICT, scheduler.ALREADY_RUNNING)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "הגיבוי נכשל")

    return JSONResponse({"file": result["file"], "skipped": result["skipped"]})
