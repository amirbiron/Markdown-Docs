"""מודלים של בסיס הנתונים.

הערה על שמות אילוצים: ה-naming_convention למטה הוא מה שמבטיח שהשם שנוצר
במודל יהיה זהה לשם שנוצר במיגרציה. בלעדיו Alembic ממציא שמות, autogenerate
מדווח על הבדלים מדומים, ובסופו של דבר DB טרי ו-DB ממוגרר מתפצלים (כלל 11).
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Computed,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )


# timestamptz ולא timestamp: האחסון תמיד UTC, וההמרה לאזור הזמן קורית בהצגה.
_TS = DateTime(timezone=True)

# ביטוי וקטור החיפוש. מוגדר כאן פעם אחת ומשוכפל מילה במילה במיגרציה —
# הבדל של רווח אחד גורם ל-autogenerate לדווח על drift בכל הרצה (כלל 11).
#
# 'simple' ולא 'english': אין מילון עברי לפוסטגרס, וה-stemming האנגלי
# מניח מורפולוגיה שלא קיימת בעברית. simple מפרק למילים בלי stemming —
# פחות חכם, אבל עובד על שתי השפות באותה מידה.
#
# strip_nikud כי unaccent מסיר דיאקריטיקה לטינית בלבד. מי שכתב מנוקד
# ומחפש בלי ניקוד לא היה מקבל תוצאות.
#
# setweight נותן לכותרת משקל A ולתוכן B, כדי שמסמך שהמילה בכותרתו יצוף
# מעל מסמך שהיא מוזכרת בו באמצע פסקה.
SEARCH_VECTOR_SQL = (
    "setweight(to_tsvector('simple', strip_nikud(coalesce(title, ''))), 'A') || "
    "setweight(to_tsvector('simple', strip_nikud(coalesce(content, ''))), 'B')"
)


class Visibility(enum.StrEnum):
    PRIVATE = "private"
    PUBLIC = "public"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    # העלאת המספר מבטלת כל cookie קיים — "התנתקות מכל המכשירים" בלי
    # טבלת sessions ובלי שאילתה נוספת, כי המשתמש נטען ממילא.
    session_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    created_at: Mapped[datetime] = mapped_column(_TS, nullable=False, server_default=func.now())

    projects: Mapped[list[Project]] = relationship(back_populates="owner", cascade="all, delete-orphan")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = _uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    # ייחודי גלובלית, לא לכל בעלים. הנתיב הוא /api/projects/:slug ואין בו
    # רכיב שמזהה בעלים — slug שאינו ייחודי גלובלית היה הופך את החיפוש
    # ללא חד-משמעי.
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    visibility: Mapped[Visibility] = mapped_column(
        Enum(Visibility, name="visibility", native_enum=True, values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        server_default=Visibility.PRIVATE.value,
    )

    created_at: Mapped[datetime] = mapped_column(_TS, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        _TS, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    owner: Mapped[User] = relationship(back_populates="projects")
    documents: Mapped[list[Document]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    links: Mapped[list[ProjectLink]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_projects_owner_id_id", "owner_id", "id"),
        # תואם ל-ORDER BY של list_projects. אינדקס שלא תואם למיון בפועל
        # אינו משמש אותו כלל, וה-DB חוזר למיון בזיכרון.
        Index("ix_projects_name_id", "name", "id"),
    )


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )

    # כאן הייחודיות היא בתוך הפרויקט בלבד — לשני פרויקטים מותר שיהיה
    # מסמך installation משלהם.
    slug: Mapped[str] = mapped_column(String(100), nullable=False)

    title: Mapped[str] = mapped_column(String(300), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    # סדר הכתיבות. סדר ההגעה של בקשות לשרת אינו סדר השליחה — שמירה
    # אוטומטית שנתקעה ברשת יכולה לנחות אחרי שמירה חדשה יותר ולהחזיר את
    # המסמך אחורה בזמן.
    #
    # המונה משויך למזהה העורך ולא למסמך, וזה ההבדל שקובע. מונה גלובלי
    # למסמך נשמע פשוט יותר, אבל הוא נועל טאב שני לחלוטין: אם טאב א' הגיע
    # ל-50 וטאב ב' נטען מחדש והתחיל מ-1, אף כתיבה של ב' לא תתקבל לעולם.
    # עם שיוך לעורך, ההגנה חלה בתוך אותו עורך, ובין עורכים שונים חוזרים
    # לכלל המוצהר — האחרון ששומר מנצח.
    last_editor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_client_seq: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")

    created_at: Mapped[datetime] = mapped_column(_TS, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        _TS, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # עמודה מחושבת ומאוחסנת: הערך מתעדכן לבד בכל כתיבה, ואי אפשר לשכוח
    # לרענן אותו. טריגר היה נותן את אותו דבר עם עוד מקום להישבר בו.
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR, Computed(SEARCH_VECTOR_SQL, persisted=True), nullable=False
    )

    project: Mapped[Project] = relationship(back_populates="documents")
    versions: Mapped[list[DocumentVersion]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("project_id", "slug", name="uq_documents_project_id_slug"),
        # ה-id בסוף הוא ה-tiebreaker. מסמכים חדשים מקבלים position=0, ולכן
        # מיון לפי position בלבד היה מחזיר סדר שרירותי שמשתנה בין טעינות
        # (כלל 8). האינדקס תואם בדיוק ל-ORDER BY שהקוד משתמש בו.
        Index("ix_documents_project_id_position_id", "project_id", "position", "id"),
        # GIN הוא האינדקס לחיפוש טקסט מלא. בלעדיו כל שאילתת חיפוש סורקת
        # את כל הטבלה ומחשבת מחדש את הדירוג לכל שורה.
        Index("ix_documents_search_vector", "search_vector", postgresql_using="gin"),
        # trigram על הכותרת, לחיפוש סובלני לשגיאות כתיב כשה-FTS לא מצא
        # כלום. gin_trgm_ops דורש את התוסף pg_trgm.
        Index(
            "ix_documents_title_trgm",
            "title",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        ),
    )


class DocumentVersion(Base):
    __tablename__ = "document_versions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(_TS, nullable=False, server_default=func.now())

    document: Mapped[Document] = relationship(back_populates="versions")

    # שליפת ההיסטוריה היא "הגרסאות של המסמך הזה, מהחדשה לישנה". ה-id
    # בסוף מבטיח סדר יציב כששתי גרסאות נשמרו באותה מילישנייה.
    __table_args__ = (Index("ix_document_versions_document_id_created_at_id", "document_id", "created_at", "id"),)


class MCPOAuthClient(Base):
    """לקוח MCP שנרשם דרך Dynamic Client Registration (RFC 7591).

    claude.ai אינו מקבל client_id ידני — הוא מצפה להירשם בעצמו ולקבל
    מזהה משלו. לכן הטבלה הזו, ולא ערך בקונפיג.

    client_id אינו UUID אלא מחרוזת שה-SDK מייצר, ולכן הוא המפתח הראשי
    כמו שהוא: חיפוש לפיו קורה בכל בקשת /token, ומפתח נוסף היה מוסיף
    אינדקס בלי להוסיף דבר.
    """

    __tablename__ = "mcp_oauth_clients"

    client_id: Mapped[str] = mapped_column(String(255), primary_key=True)

    # מסמך הרישום המלא כפי שה-SDK מצפה לקבל אותו חזרה. נשמר כ-JSON ולא
    # מפורק לעמודות: המבנה מוגדר ב-RFC וב-SDK, וכל שדה שנפרק לעמודה היה
    # צריך להתעדכן בכל שינוי במפרט.
    #
    # **המסמך כולל את client_secret בטקסט גלוי, וזו החלטה מודעת.**
    # ClientAuthenticator של ה-SDK משווה hmac.compare_digest מול
    # client.client_secret כפי ש-get_client החזיר אותו — אין נקודת
    # הרחבה שמקבלת hash, ולכן שמירה מוצפנת הייתה מחייבת להחליף את
    # שכבת אימות הלקוחות כולה.
    #
    # למה זה מקובל דווקא כאן: הסוד מזהה **תוכנה**, לא משתמש, ורישום
    # לקוחות פתוח לכל דורש — כך claude.ai מתחבר. מי שקרא את ה-DB ורוצה
    # להתחזות ללקוח יכול פשוט להירשם בעצמו ולקבל סוד חדש; הסוד הגנוב
    # אינו נותן לו דבר. ההגנה האמיתית על הזרימה היא PKCE ואישור מפורש
    # של המשתמש, ושניהם אינם נגזרים מהסוד.
    #
    # עמודת hash נפרדת שקדמה לשורות האלה הוסרה במכוון: היא לא נבדקה
    # באף מסלול, ויצרה מראה של הגנה שלא הייתה קיימת. סודות **המשתמש** —
    # קודים וטוקנים — כן נשמרים hashed. ראו MCPOAuthToken.
    registration: Mapped[dict] = mapped_column(JSONB, nullable=False)

    created_at: Mapped[datetime] = mapped_column(_TS, nullable=False, server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)


class MCPOAuthCode(Base):
    """authorization code — חד-פעמי וקצר מועד.

    נשמר hashed מאותה סיבה שהטוקנים נשמרים hashed. הקוד חי דקות ספורות,
    אבל חלון של דקה מספיק כדי להשלים החלפה לטוקן.
    """

    __tablename__ = "mcp_oauth_codes"

    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("mcp_oauth_clients.client_id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    redirect_uri: Mapped[str] = mapped_column(Text, nullable=False)

    # ה-SDK מבחין בין redirect_uri שנשלח במפורש לבין כזה שנגזר מהרישום,
    # ובודק את ההתאמה בהחלפה. השדה חייב לשרוד את הסיבוב.
    redirect_uri_provided_explicitly: Mapped[bool] = mapped_column(Boolean, nullable=False)

    code_challenge: Mapped[str] = mapped_column(String(255), nullable=False)
    scopes: Mapped[list] = mapped_column(JSONB, nullable=False)
    resource: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(_TS, nullable=False)

    __table_args__ = (Index("ix_mcp_oauth_codes_expires_at", "expires_at"),)


class MCPOAuthToken(Base):
    """access token או refresh token.

    שניהם באותה טבלה עם עמודת kind, כי המחזור שלהם זהה: שניהם נוצרים
    בהחלפה אחת, מתבטלים יחד, ונשלפים לפי hash. טבלאות נפרדות היו מכפילות
    את קוד הניקוי ואת קוד הביטול בלי הבדל מהותי.

    ה-token עצמו לעולם אינו נשמר — רק ה-hash שלו. טוקן הגישה נשלף בכל
    בקשה, ולכן ה-hash הוא sha256 ולא bcrypt: bcrypt מכוון להיות איטי, וזה
    בדיוק מה שאסור בנתיב החם. הערך אקראי ובעל אנטרופיה מלאה, ולכן אין
    כאן מה להאט — התקפת מילון אינה רלוונטית למחרוזת אקראית.
    """

    __tablename__ = "mcp_oauth_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    client_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("mcp_oauth_clients.client_id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    scopes: Mapped[list] = mapped_column(JSONB, nullable=False)

    # מקשר access ל-refresh שהונפק איתו, כדי שביטול אחד יבטל את השני.
    # טוקן גישה שנשאר בחיים אחרי שה-refresh בוטל הוא בדיוק מה שביטול
    # אמור למנוע.
    grant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)

    expires_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TS, nullable=False, server_default=func.now())

    __table_args__ = (
        Index("ix_mcp_oauth_tokens_grant_id", "grant_id"),
        Index("ix_mcp_oauth_tokens_expires_at", "expires_at"),
    )


class ProjectLink(Base):
    __tablename__ = "project_links"

    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)

    # הוולידציה שמותר רק http/https היא בשכבת האפליקציה (שלב 4), כי היא
    # דורשת פענוח URL שאין ל-Postgres.
    url: Mapped[str] = mapped_column(Text, nullable=False)

    position: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(_TS, nullable=False, server_default=func.now())

    project: Mapped[Project] = relationship(back_populates="links")

    __table_args__ = (Index("ix_project_links_project_id_position_id", "project_id", "position", "id"),)
