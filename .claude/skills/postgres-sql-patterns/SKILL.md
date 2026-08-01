---
name: postgres-sql-patterns
description: >-
  קטלוג באגים אמיתיים ב-PostgreSQL, SQLAlchemy ו-Alembic, עם הסימפטום והתיקון
  לכל אחד. Use this skill when writing or reviewing SQL queries, SQLAlchemy
  models and queries, Alembic migrations, pagination logic, index definitions,
  or CAS/optimistic-locking updates. Trigger it on phrases like "כתוב מיגרציה",
  "הוסף אינדקס", "דפדוף", "ORDER BY", "LIKE", "UPDATE ... WHERE", "model drift",
  "write a migration", "add a column", or whenever a change touches
  models.py, migrations/, alembic/, or raw SQL. Do NOT use for MongoDB or other
  NoSQL stores beyond the short footnote, and do NOT use for general database
  administration unrelated to application code.
---

# דפוסי כשל ב-Postgres, SQLAlchemy ו-Alembic

כל דפוס כאן נגזר מבאג אמיתי בפרודקשן. הסדר הוא לפי כמה קל לפספס אותו, לא לפי חומרה.

ארבעה מהדפוסים כאן מקוצרים גם ב-`CLAUDE.md` ככללים 8–11, כי שיעור הפגיעה שלהם גבוה. כאן יש את הקוד המלא ואת הנימוק.

---

## 1. סמנטיקת NULL ב-compare-and-swap

```sql
UPDATE subscription SET history_id = :new
WHERE id = :id AND history_id = :expected_old;
```

אם `history_id` בשורה הוא `NULL` וגם `:expected_old` הוא `NULL`, ההשוואה `=` מחזירה `NULL`, לא `TRUE`. Postgres מתייחס לזה כ-`FALSE`, אף שורה לא נתפסת, וה-CAS נראה ככושל. התוצאה: cursor שתקוע לנצח, בלי שגיאה.

```python
if expected_old is None:
    where = Subscription.history_id.is_(None)
else:
    where = Subscription.history_id == expected_old
```

---

## 2. `String(N)` קטן מדי לערך enum

```python
class Source(StrEnum):
    WHATSAPP = "whatsapp"
    EMAIL_PROVIDER = "email_provider"   # 14 תווים

class Lead(Base):
    source = Column(String(10), ...)    # ❌
```

SQLite לא אוכף אורך עמודה, אז זה עובר בפיתוח ובטסטים ונכשל ב-`INSERT` הראשון בפרודקשן.

עדיף `Enum(Source)`, שיוצר טיפוס enum ב-Postgres. אם חייבים `String(N)`, הוסף טסט:

```python
assert MAX_LEN >= max(len(v) for v in Source)
```

---

## 3. `Integer` קטן מדי ל-ID חיצוני

```python
telegram_user_id = Column(Integer, ...)   # ❌ 2^31
```

מזהים של Telegram, Discord, Stripe ו-GitHub חורגים מ-2³¹. `BigInteger`.

---

## 4. `ORDER BY` בלי tiebreaker ייחודי

```python
select(Lead).order_by(Lead.updated_at.desc()).limit(20).offset(40)   # ❌
```

שתי שורות עם `updated_at` זהה מקבלות סדר שרירותי, והוא לא יציב בין הרצות. בדפדוף זה מתבטא בפריטים שמדלגים או מופיעים פעמיים.

```python
select(Lead).order_by(Lead.updated_at.desc(), Lead.id.desc()).limit(20).offset(40)
```

ב-CAS עם סמנטיקת "האחרון", ה-selector וה-verifier חייבים להשתמש באותו tuple מיון בדיוק.

שני מקרים ששווה לחפש אקטיבית: עמודות `position` שכולן מתחילות ב-0, ו-`ts_rank` שמחזיר ערכים זהים למסמכים שונים.

---

## 5. סטייה בין migration למודל

הקוד רץ מול DB ממוגרר בפרודקשן ומול DB טרי בטסטים ובפיתוח. כשהשניים לא זהים, ההבדל מתגלה רחוק מהמקום שיצר אותו.

הכשלים הנפוצים:

- `op.create_index()` ב-migration בלי `__table_args__` מקביל במודל — ל-DB טרי אין את ה-index.
- `CheckConstraint("a > b")` ב-migration מול `CheckConstraint("b < a")` במודל. לוגית זהה, אבל Postgres מנרמל את שתיהן לשמות שונים, ו-autogenerate ידווח על constraint חסר.
- `DROP COLUMN` ב-migration בזמן שהקוד עדיין מתייחס לעמודה.
- revision id ארוך מ-32 תווים — `alembic_version` הוא `VARCHAR(32)` כברירת מחדל, וה-migration ייכשל רק ב-deploy טרי.
- `ADD COLUMN IF NOT EXISTS` הוא תחביר של Postgres בלבד. ב-MySQL הוא נכשל.
- migration שמתחיל ב-`ALTER` בלי שקיים `CREATE TABLE` קודם בשרשרת.

הכלל: מיגרציות הן additive כל עוד קוד ישן עדיין רץ. מוחקים עמודה רק אחרי שאין קוד שמתייחס אליה.

---

## 6. `LIKE` wildcard injection

```python
session.execute(select(Config).where(Config.key.like(f"{user_prefix}%")))   # ❌
```

`%` ו-`_` הם wildcards. prefix של `"test_key"` תופס גם `"testXkey_foo"`; prefix של `"%"` תופס את כל הטבלה.

```python
Config.key.startswith(user_prefix, autoescape=True)
```

ב-SQL גולמי: escape ל-`%`, ל-`_` ולתו ה-escape עצמו, ואז `LIKE :p ESCAPE '\'`. ואם חיפוש prefix לא באמת נדרש — `=` מדויק.

---

## 7. `ANY(:ids)` עם אי-התאמת טיפוס

```python
select(Lead).where(Lead.id == any_(string_ids))   # ❌ אם Lead.id הוא UUID
```

Postgres לא עושה cast מרומז ממערך מחרוזות למערך UUID. המר בקוד לפני העברה, או `cast(Lead.id, String) == any_(string_ids)`.

---

## 8. `postgresql_ops` הוא לא כיוון מיון

```python
Index("ix_lead_created", "created_at", postgresql_ops={"created_at": "DESC"})   # ❌
```

`postgresql_ops` מיועד למחלקות אופרטור כמו `text_pattern_ops` ו-`varchar_pattern_ops`. לכיוון:

```python
from sqlalchemy import desc
Index("ix_lead_created_desc", desc("created_at"))
```

---

## 9. פעולות מחרוזת של Python על Column

```python
select(Lead).where(Lead.email.strip() == "x@example.com")   # ❌
```

`.strip()` רץ על אובייקט ה-Column עצמו ולא מתורגם ל-SQL. השתמש ב-`func`:

```python
from sqlalchemy import func
select(Lead).where(func.trim(Lead.email) == "x@example.com")
```

---

## 10. בדיקת truthy מול `IS NULL` בעמודת מחרוזת

```python
if lead.notes:   # "  " הוא truthy ב-Python
    process(lead.notes)
```

אם חלק מהזרימות שומרות `NULL` וחלק שומרות מחרוזת ריקה או רווחים, שתי הייצוגים מתערבבים. נרמל במפורש:

```python
notes = (lead.notes or "").strip()
if notes:
    process(notes)
```

---

## מסדי נתונים אחרים

**MySQL** — אין `ADD COLUMN IF NOT EXISTS`. `CHECK` constraints לא נאכפים לפני 8.0.16. `utf8` אינו UTF-8 אמיתי, צריך `utf8mb4`. פרודקשן דורש הגדרת SSL מפורשת.

**MongoDB** — pipeline של aggregation בלי `{allowDiskUse: true}` נתקל במגבלת 100MB RAM על `$sort`. שווה להעביר את זה בכל pipeline לא טריוויאלי.
