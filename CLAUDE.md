
## תהליך עבודה

1. **קודם מתכננים** – לפני כל מימוש, יש להציג תוכנית עבודה ברורה (עם הסברים בשפה פשוטה ומובנת לכל)
2. **אחר כך מממשים** – המימוש מתחיל רק לאחר אישור התוכנית.

## כלל חשוב: 

אם נמצאו באגים כלשהם בריפו - תמיד נחפש פיתרונות שורשיים לבעיה, ולא פיתרונות "טלאי".

## שפה

- סיכומי PR, תיאורי commit, והודעות סשן — **בעברית**
- הערות בקוד (comments) — **בעברית**
- שמות משתנים, פונקציות, וטבלאות — באנגלית (כמקובל)

---

### כלל 1: בדוק await על כל קריאה לפונקציה async
> לפני push, חפש בכל הקבצים שהשתנו קריאות לפונקציות async. ודא שכל קריאה עטופה ב-`await`. coroutine object ללא await הוא תמיד truthy — זה באג שקט שיכול לשבור הכול.

### כלל 2: Race conditions — check-then-act חייב להיות אטומי
> אל תפריד בין בדיקת תנאי לביצוע פעולה. אם יש lock/mutex, הבדיקה חייבת להיות בתוכו. במיוחד: daily limits, dedup checks, state transitions. השתמש ב-`UPDATE ... WHERE status = 'X'` + `rowcount` במקום SELECT+UPDATE.

### כלל 3: אל תחשוף מידע פנימי ב-API responses
> לפני כל שינוי ב-error handling או exception classes, ודא ש-`to_dict()` / response body לא מכילים: internal IDs, password hashes, stack traces, מזהי DB, או הודעות שגיאה באנגלית טכנית. החזר הודעה גנרית בעברית למשתמש.
>
> **חריג יחיד, מאושר: מפתח ראשי מסוג UUIDv4 כמזהה יציב לבעלים.**
> הכלל נועד למנוע ספירה, ניחוש שכנים והסקת נפח — סיכונים של מזהה רץ. UUID אקראי (`default=uuid.uuid4`) אינו נושא מהם דבר, ועמודת מזהה חיצוני נפרדת הייתה אותו סוד באותה טבלה, פעמיים.
>
> החריג תקף רק כששלושת התנאים מתקיימים:
> 1. המזהה אקראי, לא רץ ולא נגזר מזמן.
> 2. הוא נחשף לבעלים בלבד (סכמת `*Private` נפרדת), ולא בתשובה ציבורית.
> 3. **כל** שליפה לפיו עוברת את אותה בדיקת נראות כמו שליפה לפי slug, וכתיבה דורשת בעלות. מזהה שמגיע מבחוץ הוא IDOR עד שהוכח אחרת — ולידציה שה-UUID תקין אינה בדיקת הרשאה.
>
> ראו `Document.id` ו-`app/services/documents.py::load_document_by_id`. הסיבה שזה נדרש: `Document.slug` משתנה, ו-slug שהתפנה ניתן לתפיסה מחדש — כלומר slug ישן מחזיר **מסמך אחר**, עם 200 ובלי שגיאה (`tests/test_document_identity.py`).

### כלל 4: ולידציית קלט מספרי — בדוק NaN, Inf, ו-edge cases
> בכל validator מספרי, בדוק קודם `math.isnan()` ו-`math.isinf()` (Python) או `Number.isNaN()` ו-`!Number.isFinite()` (JS). NaN comparisons תמיד מחזירות False — ה-NaN יעבור כל בדיקת טווח.

### כלל 5: SQLAlchemy async — אל תיגע ב-attributes אחרי commit/close
> אחרי `db.commit()`, כל ה-attributes של model objects דורשים re-fetch. חלץ ערכים פרימיטיביים (IDs, strings) לפני ה-commit, ואז בצע `db.execute(select(...))` מחדש בתוך הלולאה. זה מונע MissingGreenlet errors.

### כלל 6: Escape של user-data בכל output formatter שיש לו סינטקס פעיל
> כשמטמיעים נתון מבחוץ (DB / API / user input) לתוך output עם סינטקס פעיל — HTML, mrkdwn, SQL, shell, ANSI — חובה escape. עדיף formatter נפרד פר-target (Telegram HTML, Slack mrkdwn) על format-string אחיד, כי כללי ה-escape שונים פר ספק וtemplate אחת תעבוד טוב על אחד ותשבור על האחר. דוגמה: `parse_mode=HTML` של Telegram דורש `html.escape`; Slack mrkdwn דורש escape של `& < >` בלבד. סובייקט "Price < $100" או שולח "AT&T" מספיקים לשבור את שני הספקים.

### כלל 7: SSRF — URL מ-user → allowlist origin, לא רק https
> כל endpoint שה-backend עושה אליו fetch/POST עם URL שמשתמש סיפק (webhooks, redirect URIs, image-proxy, file-download) חייב לאמת origin מול allowlist קבוע. הגבלת `https://` בלבד לא מספיקה — `https://169.254.169.254/` היא URL חוקי שמצביע ל-AWS metadata service. לדוגמה: Slack webhook → `https://hooks.slack.com/services/` בלבד.

### כלל 8: `ORDER BY` בלי tiebreaker ייחודי = סדר לא מוגדר
> כל `ORDER BY` שמזין דפדוף או תצוגה מסודרת חייב להסתיים בעמודה ייחודית: `.order_by(Doc.position, Doc.id)`. שתי שורות עם אותו ערך מיון מקבלות סדר שרירותי שמשתנה בין הרצות — פריטים ידלגו או יופיעו פעמיים בין דפים, והרשימה תתהפך בלי סיבה נראית לעין. במיוחד: עמודות `position` שמתחילות ב-0, ו-`ts_rank` שמחזיר ערכים זהים.

### כלל 9: `LIKE` על קלט משתמש — escape של `%` ו-`_`
> `%` ו-`_` הם wildcards. prefix של `"test_key"` תופס גם `"testXkey"`, ו-prefix של `"%"` תופס הכול. ב-SQLAlchemy: `col.startswith(value, autoescape=True)`. ב-SQL גולמי: escape ל-`%`, `_` ולתו ה-escape עצמו, ואז `LIKE :p ESCAPE '\'`. אם לא נדרש חיפוש prefix — `=` מדויק.

### כלל 10: `String(N)` שמתמלא מ-enum — ודא ש-N מספיק
> `Column(String(10))` עם ערך `"email_provider"` (14 תווים) עובר ב-SQLite ונכשל ב-INSERT של Postgres. SQLite לא אוכף אורך, ולכן זה מתגלה רק בפרודקשן. עדיף `Enum(EnumClass)` שיוצר את הטיפוס ב-DB; אם חייבים `String(N)`, הוסף טסט ש-`N >= max(len(v) for v in EnumClass)`.

### כלל 11: כל constraint ו-index ב-migration חייב להשתקף במודל
> `op.create_index()` ב-Alembic בלי `__table_args__` מקביל במודל = ה-index קיים בפרודקשן הממוגרר ולא קיים ב-DB טרי, כלומר test, dev ו-prod-from-scratch מתפצלים בשקט. באותו אופן: `DROP COLUMN` ב-migration בזמן שהקוד עדיין מתייחס לעמודה, ו-revision id ארוך מ-32 תווים שנחתך ב-`alembic_version`. מיגרציות הן additive כל עוד קוד ישן עדיין רץ.

---

## עדכון בטלגרם — לפי בקשה

כשהמשתמש מבקש בתחילת משימה "עדכן אותי בטלגרם" / "תשלח לי בטלגרם כשתסיים"
(או ניסוח דומה) — בסיום המשימה שלח הודעת סיכום קצרה בעברית דרך Telegram Bot API:

- שולחים עם `curl` ל-`sendMessage`, דרך משתני הסביבה
  `TELEGRAM_BOT_TOKEN` ו-`TELEGRAM_CHAT_ID`.
- מעבירים את הטקסט עם `--data-urlencode "text=..."` (מטפל נכון בעברית ובתווים מיוחדים).
- כותבים את גוף ההודעה לקובץ ב-scratchpad ואז `text=$(cat file)` —
  כדי לא להתעסק עם escaping של shell על טקסט רב-שורתי.
- **degradation:** אם אחד המשתנים חסר — אל תיכשל ואל תעצור את המשימה;
  דווח בצ'אט "לא נשלח לטלגרם (משתנה X חסר)" והמשך כרגיל.

### עיצוב (אופציונלי)
כברירת מחדל שלח **בלי** `parse_mode` — טקסט גולמי, בטוח לכל תו.
רק אם צריך מודגש/נטוי/קישור לחיץ הוסף `--data-urlencode "parse_mode=HTML"`,
ואז — לפי כלל 6 — **חובה** `html.escape` על כל תוכן שמגיע ממקור חיצוני
(DB/API/פלט כלים), אחרת `< > &` ישברו את ההודעה.

---

Team CodeKeeper forever 💫 
We love Claude 💌
