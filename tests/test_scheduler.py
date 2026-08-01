"""מדד הקבלה של הרוטציה: אחרי 31 גיבויים, הישן ביותר נמחק.

ובנוסף — ההצפנה של העותק שיוצא החוצה.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app import scheduler
from app.crypto import CryptoError, decrypt, encrypt
from tests.conftest import (  # noqa: F401
    EMAIL,
    ORIGIN,
    PASSWORD,
    WRITE,
    anon,
    clean_projects,
    make_document,
    make_project,
    owner,
    seeded_admin,
)


def _seed(directory, count: int, start: datetime | None = None) -> list[str]:
    """יוצר קבצי גיבוי מדומים, אחד לכל יום."""
    base = start or datetime(2026, 1, 1, tzinfo=UTC)
    names = []
    for i in range(count):
        stamp = (base + timedelta(days=i)).strftime(scheduler.STAMP_FORMAT)
        name = f"{scheduler.PREFIX}{stamp}{scheduler.SUFFIX}"
        (directory / name).write_bytes(b"PK\x05\x06" + b"\x00" * 18)
        names.append(name)
    return names


# ── מדד: אחרי 31 גיבויים, הישן ביותר נמחק ────────────────────────────


def test_the_oldest_backup_is_removed_past_the_limit(tmp_path):
    names = _seed(tmp_path, 31)
    assert len(list(tmp_path.iterdir())) == 31

    scheduler.prune(30, tmp_path)

    left = sorted(p.name for p in tmp_path.iterdir())
    assert len(left) == 30
    assert names[0] not in left, "הישן ביותר לא נמחק"
    assert names[-1] in left, "החדש ביותר נמחק"


def test_pruning_keeps_the_newest_and_is_stable(tmp_path):
    _seed(tmp_path, 45)
    scheduler.prune(30, tmp_path)
    remaining = scheduler.existing_backups(tmp_path)
    assert len(remaining) == 30

    # הרצה חוזרת על מצב שכבר גזום לא מוחקת עוד
    assert scheduler.prune(30, tmp_path) == []
    assert len(scheduler.existing_backups(tmp_path)) == 30


def test_pruning_ignores_files_that_are_not_backups(tmp_path):
    """קובץ זר בתיקייה לא נמחק ולא נספר."""
    _seed(tmp_path, 31)
    (tmp_path / "README.txt").write_text("לא גיבוי")
    (tmp_path / ".last-offsite").write_text("2026-01-01T00:00:00+00:00")
    (tmp_path / "backup-לא-תאריך.zip").write_bytes(b"x")

    scheduler.prune(30, tmp_path)

    assert (tmp_path / "README.txt").exists()
    assert (tmp_path / ".last-offsite").exists()
    assert (tmp_path / "backup-לא-תאריך.zip").exists()
    assert len(scheduler.existing_backups(tmp_path)) == 30


def test_order_comes_from_the_name_not_the_file_time(tmp_path):
    """זמן הקובץ משתנה בהעתקה ובשחזור; השם לא."""
    names = _seed(tmp_path, 5)
    # הופכים את זמני הקבצים — הישן ביותר מקבל את הזמן החדש ביותר
    import os

    for i, name in enumerate(names):
        os.utime(tmp_path / name, (1_000_000 + (len(names) - i) * 100,) * 2)

    newest = scheduler.existing_backups(tmp_path)[0].name
    assert newest == names[-1], "הסדר נגזר מזמן הקובץ במקום מהשם"


def test_missing_directory_is_not_an_error(tmp_path):
    absent = tmp_path / "אין-כזו"
    assert scheduler.existing_backups(absent) == []
    assert scheduler.prune(30, absent) == []


# ── כתיבה בפועל ───────────────────────────────────────────────────────


async def test_write_backup_produces_a_readable_archive(owner, tmp_path):
    import io
    import zipfile

    await make_project(owner, slug="docs")
    await make_document(owner, slug="install", content="תוכן הגיבוי")

    path = await scheduler.write_backup(tmp_path)
    assert path.exists()
    assert not list(tmp_path.glob("*.part")), "נשאר קובץ חלקי"

    archive = zipfile.ZipFile(io.BytesIO(path.read_bytes()))
    assert archive.testzip() is None
    assert archive.read("docs/install.md").decode("utf-8") == "תוכן הגיבוי"


async def test_write_backup_prunes_as_it_goes(owner, tmp_path, monkeypatch):
    await make_project(owner, slug="docs")
    await make_document(owner)

    _seed(tmp_path, 30)
    monkeypatch.setattr(scheduler.get_settings(), "backup_keep", 30, raising=False)

    await scheduler.write_backup(tmp_path)
    assert len(scheduler.existing_backups(tmp_path)) == 30, "הגיזום לא רץ אחרי הכתיבה"


# ── ההצפנה של העותק שיוצא ─────────────────────────────────────────────


def test_encrypted_backup_round_trips():
    data = b"PK\x03\x04" + "תוכן עברי בתוך ארכיון".encode("utf-8") * 100
    sealed = encrypt(data, "סיסמה-ארוכה-מספיק")
    assert sealed != data
    assert data not in sealed, "התוכן המקורי מופיע כפי שהוא בקובץ המוצפן"
    assert decrypt(sealed, "סיסמה-ארוכה-מספיק") == data


def test_wrong_passphrase_is_rejected():
    sealed = encrypt("סוד".encode("utf-8"), "הנכונה")
    with pytest.raises(CryptoError):
        decrypt(sealed, "השגויה")


@pytest.mark.parametrize("flip", [0, 3, 20, -1])
def test_tampering_is_detected(flip):
    """GCM מאמת. קובץ ששונה בדרך נכשל במקום להתפענח לזבל."""
    sealed = bytearray(encrypt("תוכן חשוב".encode("utf-8"), "סיסמה"))
    sealed[flip] ^= 0x01
    with pytest.raises(CryptoError):
        decrypt(bytes(sealed), "סיסמה")


def test_each_encryption_differs_even_for_identical_input():
    """salt ו-nonce אקראיים — שני גיבויים זהים אינם נראים זהים."""
    first = encrypt("אותו תוכן".encode("utf-8"), "סיסמה")
    second = encrypt("אותו תוכן".encode("utf-8"), "סיסמה")
    assert first != second
    assert decrypt(first, "סיסמה") == decrypt(second, "סיסמה")


def test_empty_passphrase_is_refused():
    with pytest.raises(CryptoError):
        encrypt("תוכן".encode("utf-8"), "")


def test_garbage_input_is_refused():
    with pytest.raises(CryptoError):
        decrypt(b"not an archive at all", "סיסמה")


# ── היציאה החוצה כבויה כברירת מחדל ────────────────────────────────────


async def test_offsite_is_off_unless_explicitly_enabled():
    """דגל כבוי פירושו שלא נשלח כלום, גם כשהכול מוגדר."""
    assert await scheduler.send_offsite(b"data", "backup-x") is False


async def test_offsite_refuses_when_secrets_are_missing(monkeypatch):
    """מופעל אך בלי סוד — לא נשלח, ומדווח. גיבוי לא מוצפן לא יוצא."""
    settings = scheduler.get_settings()
    monkeypatch.setattr(settings, "backup_telegram_enabled", True, raising=False)
    monkeypatch.setattr(settings, "backup_passphrase", None, raising=False)
    assert await scheduler.send_offsite(b"data", "backup-x") is False


# ── התזמון ────────────────────────────────────────────────────────────


def test_due_is_based_on_the_last_backup_not_on_uptime():
    """הפעלה מחדש באמצע היום לא מדלגת ולא מכפילה."""
    now = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    assert scheduler._due(now, None, 24) is True
    assert scheduler._due(now, now - timedelta(hours=25), 24) is True
    assert scheduler._due(now, now - timedelta(hours=23), 24) is False
    assert scheduler._due(now, now - timedelta(minutes=1), 24) is False


async def test_run_once_skips_when_a_recent_backup_exists(owner, tmp_path, monkeypatch):
    await make_project(owner, slug="docs")
    await make_document(owner)
    monkeypatch.setattr(scheduler, "backup_dir", lambda: tmp_path)

    first = await scheduler.run_once()
    assert first is not None

    second = await scheduler.run_once()
    assert second is None, "גיבוי שני רץ למרות שהראשון נכתב הרגע"
