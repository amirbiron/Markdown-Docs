"""הגיבוי המתוזמן.

רץ בתוך שירות ה-web ולא כ-Cron Job נפרד, וזו לא בחירת נוחות: התיעוד של
Render קובע ש-"Cron jobs can't provision or access a persistent disk",
ושדיסק "is accessible by only a single service instance". כלומר שירות
נפרד לא יכול לכתוב לדיסק הזה בכלל.

התזמון לא נספר מרגע העלייה. הוא נשען על זמן הקובץ האחרון שנכתב, ולכן
דיפלוי באמצע היום לא מדלג על הגיבוי ולא מריץ אותו פעמיים.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.backup import archive_bytes
from app.config import get_settings
from app.crypto import encrypt

logger = logging.getLogger("markdown_docs.scheduler")

# כל כמה זמן נבדק "האם הגיע הזמן". לא תדירות הגיבוי — זו רק הדופק.
TICK_SECONDS = 3600

SUFFIX = ".zip"
PREFIX = "backup-"
STAMP_FORMAT = "%Y%m%d-%H%M%S"


def backup_dir() -> Path:
    return Path(get_settings().backup_dir)


def _stamp_of(path: Path) -> datetime | None:
    """קורא את הזמן מתוך שם הקובץ.

    מ-mtime ולא משם הקובץ היה נשבר בהעתקה או בשחזור של הדיסק, ששניהם
    מעדכנים את זמן הקובץ ומאפסים את ההיסטוריה בלי לגעת בתוכן.
    """
    name = path.name
    if not name.startswith(PREFIX) or not name.endswith(SUFFIX):
        return None
    core = name[len(PREFIX) : -len(SUFFIX)]
    try:
        return datetime.strptime(core, STAMP_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None


def existing_backups(directory: Path | None = None) -> list[Path]:
    """הגיבויים שעל הדיסק, מהחדש לישן."""
    target = directory or backup_dir()
    if not target.is_dir():
        return []
    dated = [(stamp, path) for path in target.iterdir() if (stamp := _stamp_of(path)) is not None]
    dated.sort(key=lambda pair: (pair[0], pair[1].name), reverse=True)
    return [path for _, path in dated]


def prune(keep: int, directory: Path | None = None) -> list[Path]:
    """מוחק את העודפים ומחזיר את מה שנמחק.

    בלי זה הדיסק מתמלא, והכתיבה מתחילה להיכשל — בשקט, וכמו תמיד בדיוק
    כשצריך את הגיבוי. זו לא אופטימיזציה עתידית אלא חלק מהמשימה.
    """
    removed = []
    for path in existing_backups(directory)[keep:]:
        try:
            path.unlink()
            removed.append(path)
        except OSError:
            logger.exception("מחיקת גיבוי ישן נכשלה: %s", path)
    if removed:
        logger.info("נמחקו %d גיבויים ישנים", len(removed))
    return removed


async def write_backup(directory: Path | None = None) -> Path:
    """בונה גיבוי וכותב אותו לדיסק, ואז מגזם."""
    settings = get_settings()
    target = directory or backup_dir()
    target.mkdir(parents=True, exist_ok=True)

    data = await archive_bytes()
    stamp = datetime.now(UTC).strftime(STAMP_FORMAT)
    path = target / f"{PREFIX}{stamp}{SUFFIX}"

    # כתיבה לקובץ זמני והחלפה אטומית. קריסה באמצע כתיבה ישירה משאירה
    # ארכיון חתוך ששום דבר לא מסמן אותו כפגום.
    temp = path.with_suffix(".part")
    temp.write_bytes(data)
    temp.replace(path)

    logger.info("גיבוי נכתב: %s (%d בתים)", path.name, len(data))
    prune(settings.backup_keep, target)
    return path


async def send_offsite(data: bytes, name: str) -> bool:
    """שולח עותק מוצפן החוצה.

    הדיסק מגן מפני דיפלוי, לא מפני אובדן השירות — אם השירות נמחק, הדיסק
    הולך איתו. זה העותק שנשאר.

    מוצפן לפני היציאה: הארכיון הוא כל התוכן של המערכת, וטלגרם מחזיק אותו
    בשרתים של צד שלישי בלי הגבלת זמן. המפתח יושב במשתנה סביבה ולא נשלח
    באותו ערוץ — גיבוי מוצפן שהמפתח שלו נשלח לצידו אינו מוצפן.
    """
    settings = get_settings()
    if not settings.backup_telegram_enabled:
        logger.info("שליחת גיבוי החוצה כבויה — נשמר לדיסק בלבד")
        return False
    if not (settings.backup_passphrase and settings.telegram_bot_token and settings.telegram_chat_id):
        logger.warning("שליחת גיבוי החוצה מופעלת אך חסרים סוד או פרטי בוט — דילוג")
        return False

    sealed = encrypt(data, settings.backup_passphrase)

    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendDocument"
    files = {"document": (name + ".enc", sealed, "application/octet-stream")}
    try:
        import httpx

        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                url, data={"chat_id": settings.telegram_chat_id}, files=files
            )
        if response.status_code != 200:
            logger.error("שליחת הגיבוי נכשלה: %s", response.status_code)
            return False
    except Exception:
        logger.exception("שליחת הגיבוי נכשלה")
        return False

    logger.info("גיבוי מוצפן נשלח החוצה (%d בתים)", len(sealed))
    return True


def _due(now: datetime, last: datetime | None, every_hours: int) -> bool:
    return last is None or now - last >= timedelta(hours=every_hours)


async def run_once() -> Path | None:
    """סבב אחד: כותב אם הגיע הזמן, ושולח החוצה אם הגיע הזמן לזה."""
    settings = get_settings()
    now = datetime.now(UTC)

    latest = existing_backups()
    last = _stamp_of(latest[0]) if latest else None
    if not _due(now, last, settings.backup_every_hours):
        return None

    path = await write_backup()

    # העותק החיצוני יוצא בקצב נפרד ואיטי יותר.
    offsite_marker = backup_dir() / ".last-offsite"
    last_offsite = None
    if offsite_marker.exists():
        try:
            last_offsite = datetime.fromisoformat(offsite_marker.read_text().strip())
        except ValueError:
            last_offsite = None

    if _due(now, last_offsite, settings.backup_offsite_every_hours):
        if await send_offsite(path.read_bytes(), path.stem):
            offsite_marker.write_text(now.isoformat())

    return path


async def loop() -> None:
    """הדופק. נעצר רק כשהמשימה מבוטלת."""
    settings = get_settings()
    logger.info(
        "מתזמן הגיבוי פעיל: כל %d שעות, שמירת %d אחרונים, יעד %s",
        settings.backup_every_hours,
        settings.backup_keep,
        settings.backup_dir,
    )
    while True:
        try:
            await run_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            # תקלה בגיבוי לא מפילה את השרת ולא עוצרת את הסבב הבא.
            logger.exception("סבב הגיבוי נכשל")
        await asyncio.sleep(TICK_SECONDS)
