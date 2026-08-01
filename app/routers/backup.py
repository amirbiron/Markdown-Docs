"""הורדת גיבוי מלא."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.backup import stream_archive
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
