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

    # תוקף ה-session. נאכף בשרת דרך exp שבתוך הטוקן החתום, לא דרך
    # Max-Age של ה-cookie.
    session_ttl_days: int = 30

    # המשתמש היחיד. ה-seed בעלייה יוצר אותו אם אינו קיים ולא נוגע בו אם כן.
    admin_email: str | None = None
    admin_password: str | None = None

    # מקורות מותרים לבקשות משנות מצב. ריק => נגזר מ-RENDER_EXTERNAL_URL,
    # ובפיתוח נופל ל-localhost.
    allowed_origins: str = ""
    render_external_url: str | None = None

    # כמה פרוקסים מהימנים יש בין הלקוח לשרת. ב-Render יש אחד. הערך הזה
    # קובע מאיזה סוף של X-Forwarded-For קוראים את כתובת הלקוח — קריאה
    # מהצד הלא נכון הופכת את הגבלת הקצב לעקיפה בשורה אחת.
    trusted_proxy_hops: int = 0

    # גבול גודל הגוף, בבתים של UTF-8. נאכף לפני קריאת הבקשה.
    max_body_bytes: int = 1_048_576

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

    @property
    def origin_allowlist(self) -> frozenset[str]:
        """המקורות שמהם מותר לשלוח בקשות משנות מצב.

        לא נגזר מכותרת ה-Host של הבקשה בכוונה — מקור שנקבע לפי הבקשה
        עצמה מאשר את כל הבקשות, כולל את אלה שרצינו לחסום.
        """
        explicit = [o.strip().rstrip("/") for o in self.allowed_origins.split(",") if o.strip()]
        if explicit:
            return frozenset(explicit)
        if self.render_external_url:
            return frozenset({self.render_external_url.strip().rstrip("/")})
        if not self.is_production:
            return frozenset(
                {f"http://{host}:{port}" for host in ("localhost", "127.0.0.1") for port in (8000, 8010, 8080, 8899)}
            )
        return frozenset()


@lru_cache
def get_settings() -> Settings:
    return Settings()
