"""יצירת המשתמש היחיד בעלייה.

אין הרשמה, ולכן המשתמש נוצר מ-ADMIN_EMAIL ו-ADMIN_PASSWORD. הפעולה
idempotent: הרצה שנייה לא משנה כלום, ולכן אפשר להריץ אותה בכל דיפלוי.
"""

from __future__ import annotations

import logging

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import User
from app.security import hash_password

logger = logging.getLogger("markdown_docs.seed")


async def seed_admin(session: AsyncSession) -> None:
    settings = get_settings()
    email = (settings.admin_email or "").strip().lower()
    password = settings.admin_password or ""

    if not email or not password:
        message = "ADMIN_EMAIL או ADMIN_PASSWORD לא הוגדרו — לא נוצר משתמש, והתחברות תיכשל"
        if settings.is_production:
            logger.error(message)
        else:
            logger.warning(message)
        return

    # hash_password מאמת את המדיניות וזורק PasswordPolicyError. השגיאה
    # מפילה את העלייה בכוונה — עדיף להיכשל ברעש מאשר ליצור חשבון חלש.
    password_hash = hash_password(password)

    # check-then-act חייב להיות אטומי (כלל 2). SELECT ואז INSERT היו
    # מאפשרים לשני workers שעולים יחד ליצור את אותו משתמש פעמיים, או
    # להיכשל על הפרת האילוץ. ON CONFLICT עושה את זה בפעולה אחת.
    statement = (
        insert(User)
        .values(email=email, password_hash=password_hash)
        .on_conflict_do_nothing(index_elements=[User.email])
    )
    result = await session.execute(statement)
    await session.commit()

    # לוג בלי הסיסמה ובלי ה-hash — לא בהודעה, לא בשגיאה, לא ב-repr.
    if result.rowcount:
        logger.info("נוצר משתמש ראשוני (%s)", email)
    else:
        logger.info("משתמש ראשוני כבר קיים (%s) — לא בוצע שינוי", email)
