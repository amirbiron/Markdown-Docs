"""אחסון מצב ה-OAuth: לקוחות, קודים וטוקנים.

השכבה הזו יודעת רק לקרוא ולכתוב. כל ההיגיון של הזרימה — מה תקף, מה
פג, מה מותר להחליף במה — יושב ב-oauth_provider.py, כדי שיהיה מקום אחד
שאפשר לקרוא ולהבין ממנו את הפרוטוקול.

**סודות המשתמש אינם נשמרים בטקסט גלוי.** קודים וטוקנים נשמרים
כ-hash בלבד, כך שגיבוי שדלף אינו מקנה גישה לתוכן.

היוצא מן הכלל הוא ``client_secret``, שנשמר גלוי בתוך מסמך הרישום —
ה-SDK משווה מולו ישירות ואין נקודת הרחבה ל-hash. הנימוק המלא, ולמה
זה מקובל דווקא שם, נמצא ב-``MCPOAuthClient.registration``.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MCPOAuthClient, MCPOAuthCode, MCPOAuthToken

# אורכי חיים. הקוד קצר בכוונה — הוא נוסע ב-URL של ההפניה חזרה ללקוח,
# כלומר עובר דרך שורת הכתובת ודרך היסטוריית הדפדפן.
CODE_TTL = timedelta(minutes=5)
ACCESS_TTL = timedelta(hours=1)
REFRESH_TTL = timedelta(days=30)

TOKEN_ACCESS = "access"
TOKEN_REFRESH = "refresh"


def now() -> datetime:
    return datetime.now(timezone.utc)


def new_secret() -> str:
    """סוד אקראי חדש. 256 ביט של אנטרופיה, בקידוד URL-safe."""
    return secrets.token_urlsafe(32)


def token_hash(value: str) -> str:
    """ה-hash שנשמר במקום הערך עצמו.

    sha256 ולא bcrypt, במכוון: access token נשלף בכל בקשה, ו-bcrypt
    מתוכנן להיות איטי. ההאטה נועדה להגן על סיסמאות שבני אדם בוחרים
    ושאפשר לנחש; כאן הערך אקראי לחלוטין, אין מה לנחש, ואין מה להאט.
    """
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# ── לקוחות ────────────────────────────────────────────────────────────


async def save_client(
    session: AsyncSession,
    *,
    client_id: str,
    registration: dict,
    expires_at: datetime | None = None,
) -> None:
    session.add(
        MCPOAuthClient(
            client_id=client_id,
            registration=registration,
            expires_at=expires_at,
        )
    )
    await session.commit()


async def load_client(session: AsyncSession, client_id: str) -> MCPOAuthClient | None:
    client = await session.get(MCPOAuthClient, client_id)
    if client is None:
        return None
    if client.expires_at is not None and client.expires_at <= now():
        return None
    return client


# ── authorization codes ───────────────────────────────────────────────


async def create_code(
    session: AsyncSession,
    *,
    client_id: str,
    user_id: uuid.UUID,
    redirect_uri: str,
    redirect_uri_provided_explicitly: bool,
    code_challenge: str,
    scopes: list[str],
    resource: str | None,
) -> str:
    """מייצר קוד, שומר את ה-hash שלו, ומחזיר את הקוד עצמו.

    זו הפעם היחידה שהערך הגלוי קיים. מכאן והלאה יש רק hash.
    """
    code = new_secret()
    session.add(
        MCPOAuthCode(
            code_hash=token_hash(code),
            client_id=client_id,
            user_id=user_id,
            redirect_uri=redirect_uri,
            redirect_uri_provided_explicitly=redirect_uri_provided_explicitly,
            code_challenge=code_challenge,
            scopes=list(scopes),
            resource=resource,
            expires_at=now() + CODE_TTL,
        )
    )
    await session.commit()
    return code


async def load_code(session: AsyncSession, code: str) -> MCPOAuthCode | None:
    """קריאה בלבד. **אינה** מספיקה כדי לצרוך את הקוד — ראו consume_code."""
    row = await session.get(MCPOAuthCode, token_hash(code))
    if row is None or row.expires_at <= now():
        return None
    return row


async def consume_code(session: AsyncSession, code: str) -> MCPOAuthCode | None:
    """מוחק את הקוד ומחזיר אותו — בהצהרה אחת, ורק למי שהספיק ראשון.

    ‏DELETE ... RETURNING ולא load-then-delete: שתי הצהרות נפרדות הן
    TOCTOU. שתי בקשות /token מקבילות עם אותו קוד היו שתיהן קוראות אותו
    לפני שהמחיקה הראשונה נסגרה, ושתיהן היו מנפיקות טוקנים — כלומר
    אישור אחד של המשתמש היה מייצר שתי הענקות תקפות.

    כאן Postgres נועל את השורה, והמפסיד מקבל rowcount=0 ו-None.
    התפוגה נבדקת באותה הצהרה, כדי שגם היא לא תהיה בדיקה נפרדת.
    """
    result = await session.execute(
        delete(MCPOAuthCode)
        .where(MCPOAuthCode.code_hash == token_hash(code), MCPOAuthCode.expires_at > now())
        .returning(MCPOAuthCode)
    )
    row = result.scalar_one_or_none()
    await session.commit()
    return row


# ── טוקנים ────────────────────────────────────────────────────────────


async def issue_pair(
    session: AsyncSession,
    *,
    client_id: str,
    user_id: uuid.UUID,
    scopes: list[str],
) -> tuple[str, str, int]:
    """מנפיק access + refresh באותה הענקה, ומחזיר (access, refresh, שניות).

    grant_id משותף מקשר ביניהם, כדי שביטול של אחד יבטל גם את השני.
    טוקן גישה ששרד ביטול של ה-refresh הוא בדיוק מה שביטול אמור למנוע.
    """
    access, refresh = new_secret(), new_secret()
    grant_id = uuid.uuid4()
    moment = now()

    session.add_all(
        [
            MCPOAuthToken(
                token_hash=token_hash(access),
                kind=TOKEN_ACCESS,
                client_id=client_id,
                user_id=user_id,
                scopes=list(scopes),
                grant_id=grant_id,
                expires_at=moment + ACCESS_TTL,
            ),
            MCPOAuthToken(
                token_hash=token_hash(refresh),
                kind=TOKEN_REFRESH,
                client_id=client_id,
                user_id=user_id,
                scopes=list(scopes),
                grant_id=grant_id,
                expires_at=moment + REFRESH_TTL,
            ),
        ]
    )
    await session.commit()
    return access, refresh, int(ACCESS_TTL.total_seconds())


async def load_token(session: AsyncSession, token: str, kind: str) -> MCPOAuthToken | None:
    row = await session.get(MCPOAuthToken, token_hash(token))
    if row is None or row.kind != kind:
        return None
    if row.expires_at is not None and row.expires_at <= now():
        return None
    return row


async def consume_refresh(session: AsyncSession, token: str) -> MCPOAuthToken | None:
    """צורך טוקן רענון ומבטל את כל ההענקה — אטומית, ורק לראשון.

    אותה מלכודת של consume_code, וכאן היא חמורה יותר: רוטציה שאינה
    אטומית פשוט אינה רוטציה. שתי בקשות רענון מקבילות היו שתיהן מצליחות
    ומייצרות שתי הענקות חדשות, וטוקן שנחשב "חד-פעמי" היה משמש פעמיים —
    בדיוק התרחיש שהרוטציה נועדה למנוע.

    המחיקה מוחקת את שתי השורות של ההענקה (גם טוקן הגישה), ומחזירה את
    שורת הרענון בלבד. הזוכה הוא מי שקיבל שורה.
    """
    moment = now()
    claimed = await session.execute(
        delete(MCPOAuthToken)
        .where(
            MCPOAuthToken.token_hash == token_hash(token),
            MCPOAuthToken.kind == TOKEN_REFRESH,
            or_(MCPOAuthToken.expires_at.is_(None), MCPOAuthToken.expires_at > moment),
        )
        .returning(MCPOAuthToken)
    )
    row = claimed.scalar_one_or_none()
    if row is None:
        await session.rollback()
        return None

    # אותה טרנזקציה: טוקן גישה ששרד רוטציה הוא בדיוק מה שהיא מונעת.
    await session.execute(delete(MCPOAuthToken).where(MCPOAuthToken.grant_id == row.grant_id))
    await session.commit()
    return row


async def revoke_grant(session: AsyncSession, grant_id: uuid.UUID) -> None:
    """מבטל את שני הטוקנים של אותה הענקה."""
    await session.execute(delete(MCPOAuthToken).where(MCPOAuthToken.grant_id == grant_id))
    await session.commit()


# ── ניקוי ─────────────────────────────────────────────────────────────


async def purge_expired(session: AsyncSession) -> int:
    """מוחק קודים וטוקנים שפג תוקפם.

    בלי זה הטבלאות גדלות לנצח: כל רענון טוקן מוסיף שתי שורות שאף אחת
    מהן לא תישלף שוב. הניקוי רץ מהמתזמן הקיים.
    """
    moment = now()
    codes = await session.execute(delete(MCPOAuthCode).where(MCPOAuthCode.expires_at <= moment))
    tokens = await session.execute(
        delete(MCPOAuthToken).where(
            MCPOAuthToken.expires_at.is_not(None), MCPOAuthToken.expires_at <= moment
        )
    )
    clients = await session.execute(
        delete(MCPOAuthClient).where(
            MCPOAuthClient.expires_at.is_not(None), MCPOAuthClient.expires_at <= moment
        )
    )
    await session.commit()
    return (codes.rowcount or 0) + (tokens.rowcount or 0) + (clients.rowcount or 0)


async def list_grants(session: AsyncSession, user_id: uuid.UUID) -> list[MCPOAuthToken]:
    """ההרשאות הפעילות של המשתמש, טוקן רענון אחד לכל חיבור.

    משמש את מסך הניהול: בלי רשימה, חיבור שאושר פעם אחת אינו ניתן
    לביטול בלי לגשת ל-DB.
    """
    moment = now()
    rows = await session.execute(
        select(MCPOAuthToken)
        .where(
            MCPOAuthToken.user_id == user_id,
            MCPOAuthToken.kind == TOKEN_REFRESH,
            or_(MCPOAuthToken.expires_at.is_(None), MCPOAuthToken.expires_at > moment),
        )
        .order_by(MCPOAuthToken.created_at.desc())
    )
    return list(rows.scalars().all())
