"""הגדרות שנטענות ממשתני סביבה."""

from functools import lru_cache
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ערכים שמופיעים בקוד, בתיעוד וב-.env.example. אם אחד מהם שרד עד
# פרודקשן, אף אחד לא הגדיר סוד אמיתי — וכל ה-cookies ניתנים לזיוף.
PLACEHOLDER_SECRETS = frozenset(
    {
        "dev-only-not-for-production",
        "change-me",
        "changeme",
        "secret",
        "",
    }
)
MIN_SECRET_LENGTH = 32


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

    # Literal ולא str: טעות כתיב כמו "prodution" הייתה נקראת כ"לא
    # פרודקשן", וזה משתיק גם את בדיקת הסוד וגם את דגל ה-Secure בעוגייה.
    # כשל שקט מהסוג הגרוע — הכול נראה עובד.
    environment: Literal["development", "production"] = "development"

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

    # כמה גרסאות נשמרות לכל מסמך. שמירה אוטומטית כל כמה שניות מייצרת
    # היסטוריה שגדלה בלי גבול, ולכן הישנות נמחקות.
    document_versions_kept: int = 50

    # ── שרת MCP ──────────────────────────────────────────────────────
    # הטוקן שמאפשר לסוכן להתחבר. ריק => הנתיב /mcp כלל אינו נרשם.
    # נעילה במפתח ולא בשומר: אין נתיב, ולא רק "אין הרשאה".
    mcp_token: str | None = None

    # ההרשאות של הטוקן, מופרדות בפסיק. המערכת חד-משתמשית ולכן יש טוקן
    # אחד, אבל המבנה קיים מראש בכוונה: שינוי scopes אחרי שלקוח כבר
    # נרשם מחייב אותו להירשם מחדש, ואי אפשר לכפות את זה מהשרת.
    mcp_token_scopes: str = "read,write"

    # זרימת OAuth ללקוחות שאינם יודעים לשלוח טוקן סטטי — claude.ai הוא
    # כזה: המסך שלו מבקש OAuth Client ID, ואין בו שדה לטוקן.
    #
    # דורש RENDER_EXTERNAL_URL, כי ה-issuer חייב להיות כתובת מוחלטת
    # שנכתבת לתוך מסמכי המטא-דאטה. ראו mcp_oauth_enabled.
    mcp_oauth: bool = True

    # ── גיבויים ──────────────────────────────────────────────────────
    # היעד הוא הדיסק הקבוע ב-Render. ברירת המחדל מקומית, כדי שפיתוח
    # ובדיקות לא ידרשו הרשאות כתיבה מחוץ לפרויקט.
    backup_dir: str = "./var/backups"
    backup_enabled: bool = True
    backup_every_hours: int = 24
    backup_keep: int = 30

    # העותק שיוצא החוצה. כבוי כברירת מחדל בכוונה: מי שמפעיל אותו עושה
    # זאת ביודעין, אחרי שהגדיר סוד הצפנה ויעד.
    backup_telegram_enabled: bool = False
    backup_offsite_every_hours: int = 168  # שבוע
    backup_passphrase: str | None = None
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None

    @property
    def async_database_url(self) -> str:
        """מנרמל את ה-URL של Render לדרייבר asyncpg."""
        url = self.database_url
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://") :]
        if url.startswith("postgresql://"):
            url = "postgresql+asyncpg://" + url[len("postgresql://") :]
        return url

    @model_validator(mode="after")
    def _reject_weak_secret_in_production(self) -> "Settings":
        """סוד ברירת מחדל בפרודקשן הוא כשל שקט מהסוג הגרוע ביותר.

        הכול נראה עובד — הכניסה מצליחה, ה-cookie נחתם — פשוט כל אחד יכול
        לחתום cookie משלו, כי הסוד כתוב בקוד המקור. מפילים את העלייה.
        """
        if self.environment != "production":
            return self
        secret = (self.session_secret or "").strip()
        if secret in PLACEHOLDER_SECRETS:
            raise ValueError("SESSION_SECRET לא הוגדר בפרודקשן — נשאר ערך ברירת המחדל")
        if len(secret) < MIN_SECRET_LENGTH:
            raise ValueError(
                f"SESSION_SECRET קצר מדי בפרודקשן — נדרשים לפחות {MIN_SECRET_LENGTH} תווים"
            )
        return self

    @model_validator(mode="after")
    def _reject_weak_mcp_token_in_production(self) -> "Settings":
        """אותו היגיון של SESSION_SECRET, על טוקן ה-MCP.

        טוקן חלש כאן חמור יותר מ-cookie מזויף: הוא נותן קריאה וכתיבה
        לכל המסמכים דרך נתיב שאינו מוגן ב-OriginGuard. טוקן ריק אינו
        שגיאה — הוא פשוט מכבה את השרת — אבל טוקן קצר או placeholder
        הוא כוונה להפעיל אותו בלי להגן עליו, ולכן מפילים את העלייה.
        """
        if self.environment != "production" or self.mcp_token is None:
            return self
        token = self.mcp_token.strip()
        if not token:
            return self
        if token in PLACEHOLDER_SECRETS:
            raise ValueError("MCP_TOKEN נשאר ערך ברירת מחדל בפרודקשן")
        if len(token) < MIN_SECRET_LENGTH:
            raise ValueError(
                f"MCP_TOKEN קצר מדי בפרודקשן — נדרשים לפחות {MIN_SECRET_LENGTH} תווים"
            )
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def mcp_enabled(self) -> bool:
        """השרת נרשם רק כשיש טוקן. ראו mcp_token."""
        return bool((self.mcp_token or "").strip())

    @property
    def mcp_oauth_enabled(self) -> bool:
        """OAuth נדלק רק כששרת ה-MCP פעיל ויש כתובת חיצונית ידועה.

        התלות ב-render_external_url אינה טכנית בלבד: ה-issuer נכתב לתוך
        המטא-דאטה שהלקוח מוריד ולתוך ההפניה חזרה אליו. כתובת שנגזרת
        מכותרת Host של הבקשה הייתה מאפשרת למי ששולט בכותרת להסיט את
        הזרימה, ולכן עדיף לא להדליק מאשר לנחש.
        """
        return (
            self.mcp_oauth
            and self.mcp_enabled
            and bool((self.render_external_url or "").strip())
        )

    @property
    def mcp_scopes(self) -> frozenset[str]:
        """ההרשאות של הטוקן היחיד.

        ערך לא מוכר מושמט ולא מתפרש כהרשאה: scope שנכתב בטעות חייב
        להצטמצם להרשאות, לא להרחיב אותן.
        """
        known = {"read", "write"}
        parsed = {s.strip().lower() for s in self.mcp_token_scopes.split(",") if s.strip()}
        return frozenset(parsed & known)

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
        return frozenset()

    @property
    def allow_loopback_origins(self) -> bool:
        """בפיתוח מאשרים כל מקור מקומי, בלי קשר לפורט.

        כאן הייתה רשימת פורטים קבועה (8000, 8010, 8080, 8899), ומי שהריץ
        על פורט אחר קיבל 403 בלי שום קשר לקוד שכתב. הפורט אינו גבול
        אבטחה: מי שכבר מריץ קוד על loopback של המכונה הזאת אינו "מקור זר".
        הגבול האמיתי הוא ההפרדה מפרודקשן, ושם התכונה הזאת כבויה תמיד.
        """
        return not self.is_production


@lru_cache
def get_settings() -> Settings:
    return Settings()
