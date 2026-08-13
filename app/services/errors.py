"""שגיאות דומיין של שכבת ה-service.

הן מכוונות, ולא נושאות טקסט שמוצג למשתמש: ניסוח ההודעה הוא החלטה של
שכבת ההצגה. הראוטרים ממפים אותן להודעות עברית ב-HTTPException, ושרת
ה-MCP ממפה אותן לקודי שגיאה קצרים שהמודל יכול לפעול לפיהם.
"""

from __future__ import annotations


class ServiceError(Exception):
    """בסיס לכל שגיאות הדומיין."""


class NotFound(ServiceError):
    """המשאב אינו קיים, או שאינו גלוי למי שמבקש.

    שני המקרים מאוחדים בכוונה. הפרדה ביניהם הייתה מאשרת לצד לא מורשה
    שהמשאב קיים, וזה מספיק כדי למפות את המערכת (כלל 3).
    """

    def __init__(self, resource: str = "resource") -> None:
        super().__init__(resource)
        self.resource = resource


class Conflict(ServiceError):
    """התנגשות עם מצב קיים, למשל slug תפוס באותו פרויקט."""

    def __init__(self, resource: str = "resource") -> None:
        super().__init__(resource)
        self.resource = resource


class InvalidInput(ServiceError):
    """הקלט פסול. נושא הודעה, כי היא מסבירה מה בדיוק פסול בקלט."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message
