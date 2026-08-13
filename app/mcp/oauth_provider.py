"""מימוש ה-OAuth Authorization Server עבור שרת ה-MCP.

ה-SDK מספק את נתיבי הפרוטוקול עצמם — ‏``/.well-known/*``, ``/authorize``,
``/token``, ``/register``, ``/revoke`` — ואת אימות ה-PKCE. המחלקה כאן
מספקת רק את האחסון ואת החלטת הזהות.

**למה בכלל OAuth, אם כבר יש טוקן סטטי.** claude.ai אינו מציע שדה
להזנת טוקן: המסך מבקש ``OAuth Client ID`` ו-``Client Secret``, שניהם
אופציונליים, כי הוא מצפה להירשם בעצמו דרך Dynamic Client Registration.
טוקן סטטי פשוט אין לאן להזין. הוא נשאר תקף לשימוש ישיר — ראו
``load_access_token`` — ולכן זו הרחבה ולא החלפה.

**איפה זה שונה מ-CodeKeeper.** שם ``authorize`` נאלץ לקפוץ לאפליקציית
הווב כדי לזהות משתמש דרך טלגרם ולחזור עם ``user_id`` חתום, וזו טבלת
עסקאות שלמה. כאן כבר יש התחברות ו-cookie, ולכן העסקה היא מחרוזת חתומה
שחיה דקות — אין טבלה, ואין מצב שנשאר תלוי אם המשתמש סגר את הלשונית.
"""

from __future__ import annotations

import logging
import secrets
import time
import uuid
from urllib.parse import quote

from itsdangerous import BadData, URLSafeSerializer
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl

from app.config import get_settings
from app.db import SessionLocal
from app.mcp import oauth_store as store
from app.mcp.auth import SCOPE_READ, SCOPE_WRITE

logger = logging.getLogger("markdown_docs.mcp.oauth")

VALID_SCOPES = [SCOPE_READ, SCOPE_WRITE]

# הנתיב שאליו ``authorize`` מפנה. מוגש מחוץ לתת-אפליקציית ה-MCP, כי הוא
# דף HTML רגיל שצריך את ה-cookie של ההתחברות.
CONSENT_PATH = "/mcp-consent"

# מזהה הבקשה הממתינה לאישור. חתום ולא נשמר: הוא חי בין הצגת המסך לבין
# הלחיצה על "אשר", והוא כולל תפוגה משלו.
TXN_TTL_SECONDS = 600


def _serializer() -> URLSafeSerializer:
    # נבנה בכל קריאה ולא פעם אחת בטעינת המודול, כדי שהחלפת
    # SESSION_SECRET בבדיקות תיתפס. בפרודקשן הערך קבוע ממילא.
    return URLSafeSerializer(get_settings().session_secret, salt="mdocs-mcp-oauth-txn-v1")


def seal_txn(payload: dict) -> str:
    """אורז בקשת authorize ממתינה למחרוזת חתומה."""
    return _serializer().dumps({**payload, "exp": int(time.time()) + TXN_TTL_SECONDS})


def open_txn(token: str) -> dict | None:
    """פותח עסקה חתומה. מחזיר None על כל כשל, בלי להבחין ביניהם."""
    try:
        payload = _serializer().loads(token)
    except BadData:
        return None
    if not isinstance(payload, dict):
        return None
    exp = payload.get("exp")
    if not isinstance(exp, int) or time.time() >= exp:
        return None
    return payload


class MarkdownDocsOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    """מחבר את נתיבי ה-OAuth של ה-SDK לטבלאות ולזהות של האפליקציה."""

    # ── רישום לקוחות (DCR) ────────────────────────────────────────────

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        async with SessionLocal() as session:
            row = await store.load_client(session, client_id)
        if row is None:
            return None
        return OAuthClientInformationFull.model_validate(row.registration)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        async with SessionLocal() as session:
            await store.save_client(
                session,
                client_id=client_info.client_id,
                # mode="json" ולא ברירת המחדל: המסמך מכיל AnyUrl ו-datetime,
                # ו-JSONB אינו יודע לסדר אותם. בלי זה הרישום נופל על
                # שגיאת סריאליזציה רק כשלקוח אמיתי מנסה להירשם.
                #
                # client_secret נכלל, במכוון. ראו MCPOAuthClient.registration.
                registration=client_info.model_dump(mode="json", exclude_none=True),
            )
        logger.info("נרשם לקוח MCP חדש: %s", client_info.client_id)

    # ── authorize ─────────────────────────────────────────────────────

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """מחזיר את הכתובת שאליה המשתמש מופנה כדי לאשר.

        ההרשאה אינה ניתנת כאן — כאן רק נארזת הבקשה. המשתמש עוד לא זוהה
        בשלב הזה: הבקשה מגיעה מ-claude.ai, ולא בהכרח מדפדפן מחובר.
        """
        # claude.ai אינו שולח scope ב-authorize. במקרה כזה לוקחים את מה
        # שהלקוח רשום עליו, כדי שברירת המחדל של הרישום תהיה מקור אמת
        # אחד — ולא ברירת מחדל שנייה שנקבעת כאן ומצמצמת בשקט כל בקשה
        # חסרת scope לקריאה בלבד.
        scopes = list(params.scopes) if params.scopes else None
        if scopes is None:
            registered = (client.scope or "").split()
            scopes = registered or list(VALID_SCOPES)

        txn = seal_txn(
            {
                "client_id": client.client_id,
                "redirect_uri": str(params.redirect_uri),
                "explicit": bool(params.redirect_uri_provided_explicitly),
                "code_challenge": params.code_challenge,
                "scopes": scopes,
                "state": params.state,
                "resource": params.resource,
            }
        )
        return f"{CONSENT_PATH}?txn={quote(txn)}"

    # ── authorization codes ───────────────────────────────────────────

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        async with SessionLocal() as session:
            row = await store.load_code(session, authorization_code)
        if row is None or row.client_id != client.client_id:
            return None
        return AuthorizationCode(
            code=authorization_code,
            scopes=list(row.scopes),
            expires_at=row.expires_at.timestamp(),
            client_id=row.client_id,
            code_challenge=row.code_challenge,
            redirect_uri=AnyUrl(row.redirect_uri),
            redirect_uri_provided_explicitly=row.redirect_uri_provided_explicitly,
            resource=row.resource,
            subject=str(row.user_id),
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        """מחליף קוד בטוקנים. ה-SDK כבר אימת את ה-PKCE לפני הקריאה הזו.

        הצריכה קודמת להנפקה ובודקת שהיא הצליחה: ``consume_code`` מוחק
        ומחזיר בהצהרה אחת, כך ששתי החלפות מקבילות של אותו קוד מסתיימות
        בכך שרק אחת מנפיקה. בדיקה נפרדת לפני המחיקה הייתה TOCTOU.
        """
        async with SessionLocal() as session:
            row = await store.consume_code(session, authorization_code.code)
            if row is None or row.client_id != client.client_id:
                raise ValueError("authorization code לא תקף")
            access, refresh, expires_in = await store.issue_pair(
                session,
                client_id=client.client_id,
                user_id=row.user_id,
                scopes=list(authorization_code.scopes),
            )
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=expires_in,
            scope=" ".join(authorization_code.scopes),
            refresh_token=refresh,
        )

    # ── refresh ───────────────────────────────────────────────────────

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        async with SessionLocal() as session:
            row = await store.load_token(session, refresh_token, store.TOKEN_REFRESH)
        if row is None or row.client_id != client.client_id:
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=row.client_id,
            scopes=list(row.scopes),
            expires_at=int(row.expires_at.timestamp()) if row.expires_at else None,
            subject=str(row.user_id),
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        """מנפיק זוג חדש ומבטל את הישן.

        רוטציה ולא הארכה: refresh token שנשאר תקף אחרי שימוש הוא טוקן
        ארוך-טווח שדליפה שלו אינה ניתנת לזיהוי. אחרי הרוטציה, שימוש חוזר
        בישן פשוט נכשל.
        """
        granted = list(scopes) if scopes else list(refresh_token.scopes)
        # הרחבת scope דרך רענון אינה מותרת. ה-SDK בודק את זה, וגם כאן —
        # כדי שהבדיקה לא תלויה בגרסה.
        extra = set(granted) - set(refresh_token.scopes)
        if extra:
            raise ValueError(f"רענון אינו יכול להרחיב scope: {sorted(extra)}")

        async with SessionLocal() as session:
            # צריכה אטומית, ולא load-then-revoke: רוטציה שאפשר לרוץ
            # אותה פעמיים במקביל אינה רוטציה.
            row = await store.consume_refresh(session, refresh_token.token)
            if row is None or row.client_id != client.client_id:
                raise ValueError("refresh token לא תקף")
            access, refresh, expires_in = await store.issue_pair(
                session, client_id=client.client_id, user_id=row.user_id, scopes=granted
            )
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=expires_in,
            scope=" ".join(granted),
            refresh_token=refresh,
        )

    # ── אימות טוקן גישה ───────────────────────────────────────────────

    async def load_access_token(self, token: str) -> AccessToken | None:
        """הנתיב החם: נקרא בכל בקשת MCP.

        כאן גם נבלע הטוקן הסטטי. ``MCP_TOKEN`` נשאר תקף במלואו — הוא
        המסלול לשימוש ישיר מ-Claude Code, מסקריפטים ומ-curl, שם אין
        דפדפן שיעבור זרימת אישור. מסלול אימות שני לא נוצר כאן: שניהם
        יוצאים מהפונקציה הזו כ-AccessToken, ומשם והלאה הקוד זהה.
        """
        static = self._static_token()
        if static is not None and secrets.compare_digest(
            token.encode("utf-8"), static.encode("utf-8")
        ):
            return AccessToken(
                token=token,
                client_id="static",
                scopes=sorted(get_settings().mcp_scopes),
                expires_at=None,
                subject=None,
            )

        async with SessionLocal() as session:
            row = await store.load_token(session, token, store.TOKEN_ACCESS)
        if row is None:
            return None
        return AccessToken(
            token=token,
            client_id=row.client_id,
            scopes=list(row.scopes),
            expires_at=int(row.expires_at.timestamp()) if row.expires_at else None,
            subject=str(row.user_id),
        )

    @staticmethod
    def _static_token() -> str | None:
        raw = (get_settings().mcp_token or "").strip()
        return raw or None

    # ── ביטול ─────────────────────────────────────────────────────────

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        """מבטל את ההענקה כולה — גם הגישה וגם הרענון.

        ביטול שמשאיר את הצד השני בחיים אינו ביטול.
        """
        kind = store.TOKEN_REFRESH if isinstance(token, RefreshToken) else store.TOKEN_ACCESS
        async with SessionLocal() as session:
            row = await store.load_token(session, token.token, kind)
            if row is None:
                return
            await store.revoke_grant(session, row.grant_id)


async def mint_code(txn: dict, user_id: uuid.UUID) -> str:
    """מייצר authorization code אחרי שהמשתמש אישר.

    נמצא כאן ולא ב-``oauth_consent`` כי הוא חלק מהפרוטוקול: הקוד חייב
    לשאת בדיוק את מה ש-``load_authorization_code`` יחפש בהמשך.
    """
    async with SessionLocal() as session:
        return await store.create_code(
            session,
            client_id=txn["client_id"],
            user_id=user_id,
            redirect_uri=txn["redirect_uri"],
            redirect_uri_provided_explicitly=bool(txn.get("explicit", True)),
            code_challenge=txn["code_challenge"],
            scopes=list(txn["scopes"]),
            resource=txn.get("resource"),
        )
