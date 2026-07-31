"""בדיקות יחידה לחלקים שקשה לראות דרך ה-API."""

from __future__ import annotations

import time

import pytest

from app.config import Settings
from app.security import (
    BCRYPT_MAX_BYTES,
    LoginRateLimiter,
    PasswordPolicyError,
    hash_password,
    issue_token,
    read_token,
    validate_password_policy,
    verify_password,
)


# ── כתובת הלקוח מאחורי פרוקסי ─────────────────────────────────────────


def _client_ip(headers, peer, hops):
    """קורא ל-client_ip עם מספר hops נתון, בלי לגעת בהגדרות הגלובליות."""
    import app.security as security

    original = security.settings
    security.settings = Settings(trusted_proxy_hops=hops)
    try:
        return security.client_ip(headers, peer)
    finally:
        security.settings = original


def test_forwarded_for_is_read_from_the_right_end():
    """הפרוקסי מוסיף בסוף — ולכן הכתובת האמיתית היא האחרונה.

    זו הנקודה שהופכת את הגבלת הקצב לאמיתית או לקישוט: תוקף ששולח
    X-Forwarded-For משלו שולט לגמרי באיבר הראשון.
    """
    spoofed = {"x-forwarded-for": "1.2.3.4, 203.0.113.9"}
    assert _client_ip(spoofed, "10.0.0.1", hops=1) == "203.0.113.9"


def test_spoofing_forwarded_for_cannot_change_the_key():
    """שתי בקשות עם ערך מזויף שונה חייבות להיספר כאותו לקוח."""
    first = _client_ip({"x-forwarded-for": "9.9.9.9, 203.0.113.9"}, "10.0.0.1", hops=1)
    second = _client_ip({"x-forwarded-for": "8.8.8.8, 203.0.113.9"}, "10.0.0.1", hops=1)
    assert first == second == "203.0.113.9"


def test_without_trusted_proxy_the_header_is_ignored():
    """בפיתוח אין פרוקסי, ולכן הכותרת לא אמינה בכלל."""
    assert _client_ip({"x-forwarded-for": "1.2.3.4"}, "127.0.0.1", hops=0) == "127.0.0.1"


def test_short_forwarded_chain_falls_back_to_peer():
    assert _client_ip({"x-forwarded-for": "1.2.3.4"}, "10.0.0.1", hops=2) == "10.0.0.1"


# ── מדיניות סיסמה ─────────────────────────────────────────────────────


def test_password_too_short_is_rejected():
    with pytest.raises(PasswordPolicyError):
        validate_password_policy("short")


def test_hebrew_password_is_measured_in_bytes():
    """תו עברי הוא שני בתים, ו-bcrypt מתעלם מעבר ל-72."""
    too_long = "סיסמה" * 10  # 50 תווים, 100 בתים
    assert len(too_long) < BCRYPT_MAX_BYTES < len(too_long.encode("utf-8"))
    with pytest.raises(PasswordPolicyError):
        validate_password_policy(too_long)


def test_hebrew_password_within_the_byte_limit_is_accepted():
    ok = "סיסמה-חזקה-מאוד"
    assert len(ok.encode("utf-8")) <= BCRYPT_MAX_BYTES
    validate_password_policy(ok)
    assert verify_password(ok, hash_password(ok))


def test_policy_error_never_contains_the_password():
    secret = "פ" * 100
    try:
        validate_password_policy(secret)
    except PasswordPolicyError as error:
        assert secret not in str(error)
        assert "פ" * 10 not in str(error)
    else:
        pytest.fail("סיסמה ארוכה מדי לא נדחתה")


def test_wrong_password_fails_and_missing_hash_does_not_crash():
    stored = hash_password("correct-horse-battery")
    assert not verify_password("wrong-password-here", stored)
    assert not verify_password("anything", None)


# ── טוקנים ────────────────────────────────────────────────────────────


def test_token_roundtrip():
    token = issue_token("11111111-1111-1111-1111-111111111111", 7)
    payload = read_token(token)
    assert payload["uid"] == "11111111-1111-1111-1111-111111111111"
    assert payload["sv"] == 7


def test_expired_token_is_rejected():
    token = issue_token("11111111-1111-1111-1111-111111111111", 1, now=time.time() - 40 * 86400)
    assert read_token(token) is None


def test_tampered_token_is_rejected():
    """משנים את ה-payload, לא את התו האחרון של החתימה.

    היפוך התו האחרון נראה כמו בדיקה טובה אבל הוא לא יציב: בקידוד
    base64url התו האחרון יכול לשאת ביטים שאינם בשימוש, ואז כמה תווים
    שונים מפוענחים לאותם בתים בדיוק והחתימה נשארת תקפה. הטסט היה עובר
    או נכשל לפי ה-exp שהוגרל באותה שנייה.
    """
    token = issue_token("11111111-1111-1111-1111-111111111111", 1)
    payload, _, signature = token.rpartition(".")
    assert payload and signature, "מבנה הטוקן השתנה"

    flipped = ("B" if payload[0] != "B" else "C") + payload[1:]
    assert read_token(f"{flipped}.{signature}") is None


def test_tampering_is_rejected_for_many_tokens():
    """מריצים על טוקנים רבים כדי שהתוצאה לא תהיה תלויה בהגרלה."""
    for offset in range(40):
        token = issue_token("11111111-1111-1111-1111-111111111111", 1, now=time.time() + offset)
        payload, _, signature = token.rpartition(".")
        flipped = payload[:-1] + ("B" if payload[-1] != "B" else "C")
        assert read_token(f"{flipped}.{signature}") is None, f"טוקן {offset} לא נדחה"


def test_token_signed_with_another_secret_is_rejected():
    import app.security as security

    foreign = security.URLSafeSerializer("a-different-secret", salt="mdocs-session-v1")
    forged = foreign.dumps({"uid": "x", "sv": 1, "exp": int(time.time() + 3600)})
    assert read_token(forged) is None


# ── הגבלת קצב ─────────────────────────────────────────────────────────


def test_backoff_grows_and_is_capped():
    limiter = LoginRateLimiter(free_attempts=5, base_seconds=2.0, cap_seconds=300.0)
    keys = ["ip:test"]
    now = 1000.0

    for _ in range(4):
        limiter.register_failure(keys, now=now)
    assert limiter.retry_after(keys, now=now) == 0, "נחסם לפני חמישה כישלונות"

    limiter.register_failure(keys, now=now)
    assert limiter.retry_after(keys, now=now) == pytest.approx(2.0)

    limiter.register_failure(keys, now=now)
    assert limiter.retry_after(keys, now=now) == pytest.approx(4.0)

    for _ in range(20):
        limiter.register_failure(keys, now=now)
    assert limiter.retry_after(keys, now=now) == pytest.approx(300.0), "התקרה לא נאכפה"


def test_lock_expires():
    limiter = LoginRateLimiter(free_attempts=1, base_seconds=10.0)
    limiter.register_failure(["k"], now=1000.0)
    assert limiter.retry_after(["k"], now=1005.0) > 0
    assert limiter.retry_after(["k"], now=1011.0) == 0


def test_success_clears_the_counter():
    limiter = LoginRateLimiter(free_attempts=5)
    for _ in range(4):
        limiter.register_failure(["k"], now=1000.0)
    limiter.register_success(["k"])
    limiter.register_failure(["k"], now=1000.0)
    assert limiter.retry_after(["k"], now=1000.0) == 0


def test_account_key_and_ip_key_are_independent():
    """נעילה של חשבון אחד לא נועלת כתובת שלא נכשלה בו, ולהפך."""
    limiter = LoginRateLimiter(free_attempts=1, base_seconds=10.0)
    limiter.register_failure(["ip:1.1.1.1", "user:a@b.c"], now=1000.0)
    assert limiter.retry_after(["ip:1.1.1.1"], now=1000.0) > 0
    assert limiter.retry_after(["ip:2.2.2.2"], now=1000.0) == 0
    assert limiter.retry_after(["user:a@b.c"], now=1000.0) > 0
    assert limiter.retry_after(["user:other@b.c"], now=1000.0) == 0


def test_pruning_bounds_memory():
    """רשומות ישנות נמחקות עד שחוזרים לגבול."""
    limiter = LoginRateLimiter(free_attempts=1, cap_seconds=10.0, max_keys=50)
    for i in range(200):
        limiter.register_failure([f"ip:{i}"], now=1000.0)
    limiter.register_failure(["ip:fresh"], now=2000.0)
    assert list(limiter._buckets) == ["ip:fresh"], f"נשארו {len(limiter._buckets)} רשומות"


def test_pruning_evicts_oldest_when_everything_is_fresh():
    """תוקף שמסובב כתובות מייצר רשומות שכולן טריות.

    ניקוי לפי גיל בלבד לא מוחק אף אחת מהן, והמילון ממשיך לגדול. הפינוי
    חייב ליפול חזרה על הוותיקות ביותר.
    """
    limiter = LoginRateLimiter(free_attempts=1, cap_seconds=10.0, max_keys=20)
    for i in range(60):
        limiter.register_failure([f"ip:{i}"], now=1000.0 + i * 0.001)
    assert len(limiter._buckets) <= 21, f"המילון הגיע ל-{len(limiter._buckets)}"
    assert "ip:59" in limiter._buckets, "דווקא החדשה ביותר פונתה"
