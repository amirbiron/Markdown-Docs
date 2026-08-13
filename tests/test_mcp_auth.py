"""שכבת האימות של שרת ה-MCP."""

from __future__ import annotations

import pytest

from app.config import Settings, get_settings
from app.db import SessionLocal
from app.mcp import auth
from tests.conftest import (  # noqa: F401
    EMAIL,
    PASSWORD,
    clean_projects,
    owner,
    seeded_admin,
)

TOKEN = "a" * 40


class _Headers(dict):
    """מחקה מיפוי כותרות שאינו תלוי-רישיות."""


@pytest.fixture
def mcp_settings(monkeypatch):
    """מפעיל את ה-MCP עם טוקן ידוע, בלי לגעת בסביבה האמיתית."""

    def _apply(**overrides):
        base = get_settings().model_dump()
        base.update({"mcp_token": TOKEN, "mcp_token_scopes": "read,write"})
        base.update(overrides)
        patched = Settings(**base)
        monkeypatch.setattr("app.mcp.auth.get_settings", lambda: patched)
        return patched

    return _apply


# ── חילוץ הטוקן ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value",
    [
        "Bearer " + TOKEN,
        "bearer " + TOKEN,
        "BEARER  " + TOKEN,
    ],
)
def test_bearer_is_extracted_case_insensitively(value):
    assert auth.extract_bearer(_Headers({"authorization": value})) == TOKEN


@pytest.mark.parametrize(
    "value",
    [
        TOKEN,  # בלי הסכמה
        "Basic " + TOKEN,
        "Bearer",
        "Bearer   ",
        "",
    ],
)
def test_malformed_authorization_yields_nothing(value):
    assert auth.extract_bearer(_Headers({"authorization": value})) is None


def test_cookie_is_never_an_identity():
    """זו הבדיקה שסוגרת את חור ה-CSRF.

    /mcp אינו מתחיל ב-/api ולכן OriginGuard אינו חל עליו. אם היינו
    מקבלים cookie, כל דף חיצוני היה יכול לגרום לדפדפן לשלוח POST
    ל-/mcp עם ה-cookie של המשתמש.
    """
    headers = _Headers({"cookie": "mdocs_session=whatever"})
    assert auth.extract_bearer(headers) is None


# ── התאמת הטוקן ───────────────────────────────────────────────────────


def test_correct_token_matches(mcp_settings):
    mcp_settings()
    assert auth.token_matches(TOKEN) is True


@pytest.mark.parametrize("candidate", ["", "b" * 40, TOKEN[:-1], TOKEN + "x"])
def test_wrong_token_rejected(mcp_settings, candidate):
    mcp_settings()
    assert auth.token_matches(candidate) is False


def test_no_configured_token_rejects_everything(mcp_settings):
    """גם מחרוזת ריקה מהלקוח לא תתאים לטוקן ריק בהגדרות."""
    mcp_settings(mcp_token=None)
    assert auth.token_matches("") is False
    assert auth.token_matches(TOKEN) is False


# ── זהות מלאה ─────────────────────────────────────────────────────────


async def test_resolve_identity_returns_the_single_user(mcp_settings, seeded_admin):
    mcp_settings()
    async with SessionLocal() as session:
        identity = await auth.resolve_identity(
            session, _Headers({"authorization": "Bearer " + TOKEN})
        )
    assert identity.user.email == EMAIL
    assert identity.scopes == frozenset({"read", "write"})


async def test_resolve_identity_rejects_bad_token(mcp_settings, seeded_admin):
    mcp_settings()
    async with SessionLocal() as session:
        with pytest.raises(auth.AuthError):
            await auth.resolve_identity(
                session, _Headers({"authorization": "Bearer wrong"})
            )


async def test_resolve_identity_rejects_when_disabled(mcp_settings, seeded_admin):
    mcp_settings(mcp_token=None)
    async with SessionLocal() as session:
        with pytest.raises(auth.AuthError):
            await auth.resolve_identity(
                session, _Headers({"authorization": "Bearer " + TOKEN})
            )


# ── הרשאות ────────────────────────────────────────────────────────────


def test_require_write_passes_with_write_scope():
    identity = auth.Identity(user=None, scopes=frozenset({"read", "write"}))
    auth.require_write(identity)  # לא זורק


def test_require_write_blocks_read_only():
    identity = auth.Identity(user=None, scopes=frozenset({"read"}))
    with pytest.raises(auth.PermissionError_):
        auth.require_write(identity)


def test_unknown_scope_is_dropped_not_honoured(mcp_settings):
    """scope שנכתב בטעות מצמצם הרשאות, לא מרחיב."""
    patched = mcp_settings(mcp_token_scopes="read,wrtie,admin")
    assert patched.mcp_scopes == frozenset({"read"})


# ── הגדרות בפרודקשן ───────────────────────────────────────────────────

_PROD = {
    "environment": "production",
    "session_secret": "s" * 40,
    "database_url": "postgresql://user:pass@localhost/db",
}


def test_production_accepts_a_strong_token():
    settings = Settings(**_PROD, mcp_token="t" * 40)
    assert settings.mcp_enabled is True


@pytest.mark.parametrize("token", ["abc", "change-me", "secret"])
def test_production_refuses_to_boot_on_a_weak_token(token):
    """טוקן חלש הוא כוונה להפעיל את השרת בלי להגן עליו."""
    with pytest.raises(ValueError):
        Settings(**_PROD, mcp_token=token)


def test_empty_token_disables_instead_of_failing():
    """היעדר טוקן אינו שגיאה — הוא פשוט מכבה את השרת."""
    settings = Settings(**_PROD, mcp_token="")
    assert settings.mcp_enabled is False
