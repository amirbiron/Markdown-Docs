"""כניסה ויציאה."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db import get_session
from app.deps import optional_user
from app.models import User
from app.security import COOKIE_NAME, client_ip, issue_token, login_limiter, verify_password

logger = logging.getLogger("markdown_docs.auth")
settings = get_settings()

router = APIRouter(prefix="/auth", tags=["auth"])

# תשובה אחת לכל כישלון. לא "המייל לא נמצא", לא "הסיסמה שגויה", ולא
# "החשבון חסום כרגע" — כל אחת מהן מוסרת מידע למי שמנחש.
GENERIC_FAILURE = {"detail": "הפרטים שגויים או שהכניסה חסומה זמנית"}


class LoginRequest(BaseModel):
    email: EmailStr
    # אין מגבלת אורך עליונה כאן בכוונה — מדיניות הסיסמה נאכפת ביצירה,
    # ובכניסה סיסמה ארוכה מדי פשוט לא תתאים.
    password: str = Field(min_length=1)


def _rate_keys(request: Request, email: str) -> list[str]:
    """מפתחות הגבלה: גם לפי כתובת וגם לפי חשבון.

    לפי כתובת בלבד — תוקף עם כמה כתובות עוקף. לפי חשבון בלבד — אפשר
    לנעול את בעל החשבון מרחוק. שניהם יחד סוגרים את שתי הפרצות.
    """
    ip = client_ip(request.headers, request.client.host if request.client else None)
    return [f"ip:{ip}", f"user:{email}"]


def mask_email(email: str) -> str:
    """מזהה יציב ללוג בלי לרשום את הכתובת עצמה.

    לוג הוא לא רק לנו: הוא נשמר, נגרר לשירותי ניטור, ולפעמים משותף. כתובת
    מייל היא מידע אישי, ואין סיבה שתשב שם רק כדי לזהות ניסיון כושל.
    """
    local, _, domain = email.partition("@")
    head = local[:2] if len(local) > 2 else local[:1]
    return f"{head}***@{domain}" if domain else f"{head}***"


@router.post("/login")
async def login(
    payload: LoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Response:
    email = payload.email.strip().lower()
    keys = _rate_keys(request, email)

    # ההגבלה נבדקת *לפני* האימות, משתי סיבות. האחת היא brute force.
    # השנייה פחות מובנת מאליה: bcrypt יקר בכוונה, ולכן קריאה לו על כל
    # ניסיון כושל היא בעצמה וקטור להעמסת השרת.
    remaining = login_limiter.retry_after(keys)
    if remaining > 0:
        logger.warning("ניסיון כניסה נחסם (%s), נותרו %.1f שניות", keys[0], remaining)
        return JSONResponse(GENERIC_FAILURE, status_code=status.HTTP_401_UNAUTHORIZED)

    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()

    # verify_password מריץ bcrypt גם כשהמשתמש לא קיים, מול hash דמה,
    # כדי שזמן התגובה לא יסגיר אילו חשבונות רשומים.
    if user is None or not verify_password(payload.password, user.password_hash):
        login_limiter.register_failure(keys)
        logger.warning("כניסה נכשלה (%s)", mask_email(email))
        return JSONResponse(GENERIC_FAILURE, status_code=status.HTTP_401_UNAUTHORIZED)

    login_limiter.register_success(keys)

    # מחלצים ערכים פרימיטיביים לפני שהאובייקט עלול להתיישן (כלל 5).
    token = issue_token(str(user.id), user.session_version)

    response = JSONResponse({"email": user.email})
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=settings.is_production,  # ב-HTTP מקומי דפדפן לא ישמור cookie מסומן Secure
        samesite="lax",
        max_age=settings.session_ttl_days * 86400,
        path="/",
    )
    logger.info("כניסה הצליחה (%s)", mask_email(email))
    return response


@router.post("/logout")
async def logout() -> Response:
    """מנקה את ה-cookie בדפדפן הזה.

    זו לא ביטול של הטוקן — מי שהעתיק את הערך עדיין מחזיק טוקן חתום
    ותקף. לניתוק אמיתי מכל המכשירים יש את session_version.
    """
    response = JSONResponse({"detail": "התנתקת"})
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


@router.get("/me")
async def me(user=Depends(optional_user)) -> Response:
    """מי מחובר. אנונימי מקבל 200 עם authenticated=false ולא 401.

    "אף אחד" הוא תשובה תקינה לשאלה הזו, לא שגיאה. 401 כאן היה מייצר
    שגיאה בקונסולה ובלוג בכל טעינת דף של מבקר אנונימי — רעש שמסתיר
    שגיאות אמיתיות. הנתיבים המוגנים עצמם כמובן ממשיכים להחזיר 401.
    """
    if user is None:
        return JSONResponse({"authenticated": False})
    return JSONResponse({"authenticated": True, "email": user.email})
