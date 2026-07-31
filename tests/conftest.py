"""ערכים משותפים לכל הבדיקות."""

from __future__ import annotations

from app.config import get_settings

_allowlist = sorted(get_settings().origin_allowlist)
assert _allowlist, (
    "origin_allowlist ריק — הגדר ALLOWED_ORIGINS או RENDER_EXTERNAL_URL, "
    "או הרץ עם ENVIRONMENT=development"
)

# ה-Origin שהבדיקות שולחות בכל בקשה משנת מצב.
ORIGIN = _allowlist[0]
WRITE = {"Origin": ORIGIN}

EMAIL = "admin@example.com"
PASSWORD = "correct-horse-battery"
