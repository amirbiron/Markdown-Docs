"""full text search

Revision ID: 0005_search
Revises: 0004_nameidx
Create Date: 2026-08-01

נכתב ביד ולא ב-autogenerate: Alembic לא מייצר תוספים ולא פונקציות, ורק
הן מאפשרות את העמודה המחושבת שבאה אחריהן.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TSVECTOR

revision: str = "0005_search"
down_revision: Union[str, None] = "0004_nameidx"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# חייב להיות זהה מילה במילה ל-SEARCH_VECTOR_SQL ב-app/models.py, אחרת
# autogenerate ידווח על drift בכל הרצה (כלל 11).
SEARCH_VECTOR_SQL = (
    "setweight(to_tsvector('simple', strip_nikud(coalesce(title, ''))), 'A') || "
    "setweight(to_tsvector('simple', strip_nikud(coalesce(content, ''))), 'B')"
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # מסיר ניקוד וטעמי מקרא, U+0591 עד U+05C7.
    #
    # התוסף unaccent *לא* עושה את זה. הכללים שלו מכסים דיאקריטיקה
    # לטינית ויוונית בלבד, ו-unaccent('שָׁלוֹם') מחזיר את המחרוזת ללא
    # שינוי. בלי הפונקציה הזו, מי שכתב מנוקד ומחפש בלי ניקוד פשוט לא
    # מקבל תוצאות — ואין שום שגיאה שמסגירה את זה.
    #
    # IMMUTABLE הוא תנאי הכרחי: עמודה מחושבת ואינדקס לא יכולים להישען
    # על פונקציה שאינה כזו.
    op.execute(
        r"""
        CREATE OR REPLACE FUNCTION strip_nikud(text) RETURNS text
          AS $$ SELECT regexp_replace($1, '[֑-ׇ]', '', 'g') $$
          LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
        """
    )

    op.add_column(
        "documents",
        sa.Column(
            "search_vector",
            TSVECTOR,
            sa.Computed(SEARCH_VECTOR_SQL, persisted=True),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_documents_search_vector", "documents", ["search_vector"], postgresql_using="gin"
    )
    op.create_index(
        "ix_documents_title_trgm",
        "documents",
        ["title"],
        postgresql_using="gin",
        postgresql_ops={"title": "gin_trgm_ops"},
    )


def downgrade() -> None:
    # סדר הפוך: העמודה נשענת על הפונקציה, ולכן היא יורדת קודם.
    op.drop_index("ix_documents_title_trgm", table_name="documents")
    op.drop_index("ix_documents_search_vector", table_name="documents")
    op.drop_column("documents", "search_vector")
    op.execute("DROP FUNCTION IF EXISTS strip_nikud(text)")
    # התוסף נשאר. הוא עשוי לשמש דברים אחרים בבסיס הנתונים, והסרתו היא
    # פעולה רחבה יותר ממה שהמיגרציה הזו אחראית עליה.
