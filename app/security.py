"""סיסמאות, טוקני session, זיהוי כתובת הלקוח והגבלת קצב."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import bcrypt
from itsdangerous import BadData, URLSafeSerializer

from app.config import get_settings

logger = logging.getLogger("markdown_docs.security")
settings = get_settings()

COOKIE_NAME = "mdocs_session"

# bcrypt מתעלם מכל מה שמעבר ל-72 בתים של הקלט, בשקט. סיסמה עברית של 40
# תווים היא 80 בתים — כלומר 8 התווים האחרונים לא היו נספרים בכלל, ושתי
# סיסמאות שנבדלות רק בהם היו מתקבלות שתיהן. חוסמים במפורש במקום לחתוך.
BCRYPT_MAX_BYTES = 72
PASSWORD_MIN_BYTES = 12

# hash דמה שמריצים מולו כשהמשתמש לא קיים, כדי שזמן התגובה לא יסגיר
# אם המייל רשום. נוצר פעם אחת בטעינת המודול.
_DUMMY_HASH = bcrypt.hashpw(b"dummy-password-for-constant-time", bcrypt.gensalt())

_serializer = URLSafeSerializer(settings.session_secret, salt="mdocs-session-v1")


class PasswordPolicyError(ValueError):
    """הסיסמה לא עומדת במדיניות. ההודעה לעולם לא מכילה את הסיסמה עצמה."""


def validate_password_policy(password: str) -> None:
    size = len(password.encode("utf-8"))
    if size < PASSWORD_MIN_BYTES:
        raise PasswordPolicyError(
            f"הסיסמה קצרה מדי — נדרשים לפחות {PASSWORD_MIN_BYTES} בתים ב-UTF-8, התקבלו {size}"
        )
    if size > BCRYPT_MAX_BYTES:
        raise PasswordPolicyError(
            f"הסיסמה ארוכה מדי — bcrypt מתעלם מעבר ל-{BCRYPT_MAX_BYTES} בתים, התקבלו {size}. "
            "שים לב שתו עברי הוא שני בתים."
        )


def hash_password(password: str) -> str:
    validate_password_policy(password)
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str | None) -> bool:
    """בדיקה בזמן קבוע מול קיום המשתמש.

    כשאין hash (משתמש לא קיים) עדיין מריצים bcrypt מול hash דמה, כדי
    שהתשובה "לא נמצא" לא תחזור מהר יותר מ"סיסמה שגויה".
    """
    candidate = password.encode("utf-8")
    if password_hash is None:
        bcrypt.checkpw(candidate, _DUMMY_HASH)
        return False
    try:
        return bcrypt.checkpw(candidate, password_hash.encode("ascii"))
    except ValueError:
        # hash פגום ב-DB — נכשל כמו סיסמה שגויה, ומדווח ללוג בלי הערך עצמו
        logger.error("password_hash פגום נמצא ב-DB")
        return False


# ─────────────────────────── טוקן ה-session ───────────────────────────


def issue_token(user_id: str, session_version: int, now: float | None = None) -> str:
    """יוצר טוקן חתום שנושא את מועד התפוגה בתוך החתימה."""
    issued = time.time() if now is None else now
    payload = {
        "uid": str(user_id),
        "sv": int(session_version),
        "exp": int(issued + settings.session_ttl_days * 86400),
    }
    return _serializer.dumps(payload)


def read_token(token: str, now: float | None = None) -> dict | None:
    """מאמת חתימה ותפוגה. מחזיר None על כל כשל, בלי להבחין ביניהם.

    התפוגה נאכפת כאן, בשרת, ולא דרך Max-Age של ה-cookie. Max-Age הוא
    הוראה לדפדפן — מי שהעתיק את ערך ה-cookie ושולח אותו ב-curl לא מושפע
    ממנו כלל, והחתימה נשארת תקפה לנצח.
    """
    try:
        payload = _serializer.loads(token)
    except BadData:
        # BadData היא השורש של כל שגיאות itsdangerous — חתימה פגומה, אבל
        # גם payload שלא ניתן לפענוח וכותרת שבורה. תפיסת BadSignature
        # בלבד הייתה מפילה 500 על טוקן מעוות במקום להחזיר 401.
        return None
    if not isinstance(payload, dict):
        return None
    exp = payload.get("exp")
    if not isinstance(exp, int):
        return None
    if (time.time() if now is None else now) >= exp:
        return None
    if not isinstance(payload.get("uid"), str) or not isinstance(payload.get("sv"), int):
        return None
    return payload


# ─────────────────────────── כתובת הלקוח ───────────────────────────


def client_ip(headers: dict[str, str], peer: str | None) -> str:
    """מחלץ את כתובת הלקוח מתוך X-Forwarded-For, מהצד הנכון.

    זו נקודה שקל מאוד לטעות בה. פרוקסי מוסיף את הכתובת שממנה *הוא* קיבל
    את הבקשה בסוף הרשימה. אם הלקוח שולח X-Forwarded-For משלו, הכותרת
    שמגיעה אלינו היא "<מה שהלקוח המציא>, <הכתובת האמיתית>" — ולכן האיבר
    ה*ראשון* נשלט לגמרי על ידי התוקף. מי שסופר לפי האיבר הראשון מקבל
    הגבלת קצב שאפשר לעקוף בהחלפת מחרוזת.

    סופרים מהסוף, לפי מספר הפרוקסים המהימנים שבדרך.
    """
    hops = settings.trusted_proxy_hops
    if hops > 0:
        raw = headers.get("x-forwarded-for", "")
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        if len(parts) >= hops:
            return parts[-hops]
    return peer or "unknown"


# ─────────────────────────── הגבלת קצב ───────────────────────────


@dataclass
class _Bucket:
    failures: int = 0
    locked_until: float = 0.0
    last_seen: float = field(default_factory=time.time)


class LoginRateLimiter:
    """Backoff מעריכי אחרי כישלונות רצופים, לפי מפתח.

    בזיכרון התהליך. זה נכון כאן כי השירות רץ במופע יחיד — דיסק קבוע
    ב-Render מונע התרחבות לרוחב ממילא. אם אי פעם יהיה יותר ממופע אחד,
    המונה חייב לעבור ל-DB או ל-Redis, אחרת כל מופע סופר בנפרד והמכפלה
    היא מספר המופעים.
    """

    def __init__(
        self,
        free_attempts: int = 5,
        base_seconds: float = 2.0,
        cap_seconds: float = 300.0,
        max_keys: int = 10_000,
    ) -> None:
        self.free_attempts = free_attempts
        self.base_seconds = base_seconds
        self.cap_seconds = cap_seconds
        self.max_keys = max_keys
        self._buckets: dict[str, _Bucket] = {}

    def _prune(self, now: float) -> None:
        """מונע גדילה בלי גבול כשתוקף מסובב כתובות."""
        if len(self._buckets) <= self.max_keys:
            return

        stale = now - self.cap_seconds * 2
        for key in [k for k, b in self._buckets.items() if b.last_seen < stale]:
            del self._buckets[key]

        # ניקוי הישנים לבדו אינו מספיק: תוקף ששולח מכתובות חדשות בקצב
        # מהיר מייצר רשומות שכולן טריות, ואז אין מה למחוק והמילון ממשיך
        # לגדול. מפנים את הוותיקות ביותר עד שחוזרים לגבול.
        if len(self._buckets) <= self.max_keys:
            return
        ordered = sorted(self._buckets.items(), key=lambda item: item[1].last_seen)
        for key, _ in ordered[: len(self._buckets) - self.max_keys]:
            del self._buckets[key]

    def retry_after(self, keys: list[str], now: float | None = None) -> float:
        """כמה שניות נותרו לחסימה. 0 אם פתוח."""
        moment = time.time() if now is None else now
        remaining = 0.0
        for key in keys:
            bucket = self._buckets.get(key)
            if bucket is not None:
                remaining = max(remaining, bucket.locked_until - moment)
        return max(0.0, remaining)

    def register_failure(self, keys: list[str], now: float | None = None) -> None:
        moment = time.time() if now is None else now
        self._prune(moment)
        for key in keys:
            bucket = self._buckets.setdefault(key, _Bucket())
            bucket.failures += 1
            bucket.last_seen = moment
            if bucket.failures >= self.free_attempts:
                exponent = bucket.failures - self.free_attempts
                delay = min(self.base_seconds * (2**exponent), self.cap_seconds)
                bucket.locked_until = moment + delay

    def register_success(self, keys: list[str]) -> None:
        for key in keys:
            self._buckets.pop(key, None)


login_limiter = LoginRateLimiter()
