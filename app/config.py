"""הגדרות שנטענות ממשתני סביבה."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Render מזריק DATABASE_URL בסכמה postgres:// או postgresql://.
    # SQLAlchemy async דורש דרייבר מפורש, ולכן מנרמלים ב-database_url למטה.
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/markdown_docs"

    # סוד חתימת ה-session. החלפתו מבטלת את כל ה-cookies הקיימים — זה
    # מנגנון החירום; לשגרה יש את users.session_version.
    session_secret: str = "dev-only-not-for-production"

    # אזור הזמן שנקבע על כל חיבור. העמודות הן timestamptz ולכן האחסון הוא
    # UTC בכל מקרה; ההגדרה הזו משפיעה על הצגה והמרות בלבד.
    timezone: str = "Asia/Jerusalem"

    environment: str = "development"

    @property
    def async_database_url(self) -> str:
        """מנרמל את ה-URL של Render לדרייבר asyncpg."""
        url = self.database_url
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://") :]
        if url.startswith("postgresql://"):
            url = "postgresql+asyncpg://" + url[len("postgresql://") :]
        return url

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
