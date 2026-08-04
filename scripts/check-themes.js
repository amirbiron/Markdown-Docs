/* שלוש התמות: dark, dim, light.
 *
 * מה שנבדק כאן אינו "הכפתור עובד" אלא שהמעבר באמת מחליף ערכה — הרבה
 * מהדברים שיכולים להישבר כאן נשברים בשקט: data-theme מתחלף אבל הצבעים
 * לא, color-scheme מקבל ערך שאינו חוקי, או שתמה חדשה יורשת בטעות את
 * צורות ה-Editorial.
 *
 * הרצה:
 *   node scripts/check-themes.js http://127.0.0.1:8070 <תיקיית-צילומים>
 */

let chromium;
try {
  ({ chromium } = require('playwright'));
} catch {
  console.error('playwright לא מותקן. הריצו: npm install');
  process.exit(1);
}

const BASE = (process.argv[2] || '').replace(/\/+$/, '');
const SHOTS = process.argv[3];
if (!BASE || !SHOTS) {
  console.error('שימוש: node scripts/check-themes.js <כתובת-בסיס> <תיקיית-צילומים>');
  process.exit(1);
}

const EMAIL = process.env.ADMIN_EMAIL || 'admin@example.com';
const PASSWORD = process.env.ADMIN_PASSWORD || 'correct-horse-battery';
const NAME = 'בדיקת תמות ' + process.pid;

/* מה שכל תמה חייבת לקיים.

   הערכים כאן קשיחים **בכוונה**, ולא נקראים מהערכות. אילו הבדיקה הייתה
   שואבת אותם מ-index.html היא הייתה משווה את הקוד לעצמו ועוברת תמיד;
   מה שהיא בודקת הוא שהגוון, הגופן והמשפחה לא השתנו בלי שאיש התכוון.
   שינוי מכוון דורש עדכון של השורה כאן, וזו בדיוק המטרה. */
const EXPECTED = {
  dark: { scheme: 'dark', bg: '#0e1022', accent: '#0088cc', font: 'Heebo', editorial: false },
  dim: { scheme: 'dark', bg: '#2f3338', accent: '#c8823c', font: 'Rubik', editorial: false },
  light: { scheme: 'light', bg: '#f7f5f1', accent: '#9c3b2e', font: 'Assistant', editorial: true },
  /* שתי ערכות Coast הן זהות אחת בשתי בהירויות, ולכן שתיהן editorial
     ושתיהן על אותו סולם גופנים. מה שנבדל הוא scheme, הרקע והאקסנט. */
  coast: { scheme: 'light', bg: '#f9f4e8', accent: '#1e5f8c', font: 'Heebo', editorial: true },
  'coast-dark': { scheme: 'dark', bg: '#0a1828', accent: '#76b5d9', font: 'Heebo', editorial: true },
};

/* סדר המחזור, לעומת זאת, כן נקרא מהמקור. הוא מטא-דאטה ולא ערך שנבדק,
   ורשימה שנייה כאן הייתה מתפצלת בשקט: תמה שתתווסף ל-index.html ולא
   לכאן פשוט לא הייתה נבדקת, וההרצה הייתה ירוקה. */
function themeOrderFromSource() {
  const fs = require('fs');
  const path = require('path');
  const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
  const m = html.match(/const THEME_ORDER = \[([^\]]+)\]/);
  if (!m) {
    console.error('FATAL לא נמצא THEME_ORDER ב-index.html — האם השם השתנה?');
    process.exit(1);
  }
  return m[1].split(',').map((x) => x.trim().replace(/^["']|["']$/g, '')).filter(Boolean);
}

const ORDER = themeOrderFromSource();

/* data-props הוא החוזה המוצהר של הרכיב — ממנו נגזרים ה-enum בעורך
   והטיפוס. הוא נכתב ביד ואינו נגזר מ-THEME_ORDER, ולכן תמה חדשה
   שנוספת לקוד ולא שם עובדת בזמן ריצה ואינה קיימת לפי המטא-דאטה.
   זה בדיוק מה שקרה עם coast, ולכן הבדיקה כאן. */
function declaredThemes() {
  const fs = require('fs');
  const path = require('path');
  const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
  const m = html.match(/defaultTheme&quot;:\{[^}]*&quot;options&quot;:\[([^\]]+)\]/);
  if (!m) return null;
  return m[1].split(',').map((x) => x.replace(/&quot;/g, '').trim());
}

/* הגשר בין השניים: תמה שנוספה ל-index.html ואין לה שורה ב-EXPECTED
   הייתה נבדקת ריק. כאן זה נכשל ברעש. */
const missing = ORDER.filter((t) => !EXPECTED[t]);
if (missing.length) {
  console.error(`FATAL אין ציפיות לתמות: ${missing.join(', ')} — הוסיפו אותן ל-EXPECTED`);
  process.exit(1);
}

const results = [];
const check = (label, ok, extra) => {
  results.push({ label, ok, extra });
  console.log(`  ${ok ? '✓' : '✗'}  ${label}${extra ? '  — ' + extra : ''}`);
};

const snapshot = (page) =>
  page.evaluate(() => {
    const cs = getComputedStyle(document.documentElement);
    const btn = document.querySelector('button[title^="מעבר למצב"]');
    return {
      theme: document.documentElement.getAttribute('data-theme'),
      scheme: document.documentElement.style.colorScheme,
      bg: cs.getPropertyValue('--bg').trim(),
      accent: cs.getPropertyValue('--accent').trim(),
      sans: cs.getPropertyValue('--sans').trim(),
      display: cs.getPropertyValue('--display').trim(),
      /* h1 בעיצוב ה-Editorial הוא serif וגדול יותר. זה ההבדל הצורני
         שמבדיל בין המשפחות, ולא רק הצבע. */
      h1Font: (() => {
        const h1 = document.querySelector('article h1, header h1, h1');
        return h1 ? getComputedStyle(h1).fontFamily : '';
      })(),
      /* אסור שיהיו משתני CSS למטא-דאטה של התמה. */
      leakedScheme: cs.getPropertyValue('--scheme').trim(),
      leakedEditorial: cs.getPropertyValue('--editorial').trim(),
      title: btn ? btn.getAttribute('title') : null,
      glyph: btn ? btn.textContent.trim() : null,
    };
  });

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1500, height: 1100 } });
  const p = await ctx.newPage();
  const errs = [];
  p.on('console', (m) => { if (m.type() === 'error') errs.push(m.text()); });
  p.on('pageerror', (e) => errs.push('PAGEERROR: ' + e.message));

  await p.goto(BASE + '/', { waitUntil: 'load' });
  await p.waitForSelector('#dc-root', { timeout: 20000 });
  await p.waitForTimeout(2500);

  // מסמך ציבורי עם כל סוגי הבלוקים, כדי שהצילומים יהיו בני השוואה
  const setup = await p.evaluate(async ([email, password, name]) => {
    const post = (u, b) => fetch(u, { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b) });
    const login = await post('/api/auth/login', { email, password });
    if (!login.ok) return { error: 'כניסה נכשלה: ' + login.status };
    const created = await post('/api/projects', { name, visibility: 'public' });
    if (!created.ok) return { error: 'יצירת פרויקט נכשלה: ' + created.status };
    const slug = (await created.json()).slug;
    const content = [
      '# מדריך התמה', '',
      'פסקה עם **הדגשה** ו-`קוד inline`.', '',
      '## בלוק קוד', '',
      '```python', 'def שלום(שם):', '    return f"שלום {שם}"', '```', '',
      '::: warning', 'בלוק אזהרה.', ':::', '',
      '::: tip', 'טיפ ירוק.', ':::', '',
      '| עמודה | ערך |', '|---|---|', '| אחד | 1 |', '',
      '```mermaid', 'graph TD;  A[בקשה] --> B[שרת];', '```', '',
    ].join('\n');
    const doc = await post(`/api/projects/${encodeURIComponent(slug)}/docs`,
      { title: 'מדריך התמה', slug: 'guide', content });
    if (!doc.ok) return { error: 'יצירת מסמך נכשלה: ' + doc.status };
    return { slug };
  }, [EMAIL, PASSWORD, NAME]);
  if (setup.error) { console.error('FATAL ' + setup.error); await browser.close(); process.exit(1); }

  /* ה-slug נשמר מיד, וכל השאר רץ בתוך try. בלי זה, בדיקה שזורקת
     באמצע מדלגת על המחיקה ומשאירה פרויקט במסד — ודווקא בהרצות
     הכושלות, שבהן מריצים שוב ושוב, הזבל מצטבר הכי מהר. */
  const slug = setup.slug;
  let removed = 'לא רץ';

  try {
  await p.reload({ waitUntil: 'load' });
  await p.waitForSelector('#dc-root', { timeout: 20000 });
  const link = p.getByText(NAME).first();
  await link.waitFor({ state: 'visible', timeout: 20000 });
  await link.click();
  await p.waitForTimeout(2500);

  // ── מחזור מלא: כל תמה בדיוק פעם אחת, וחזרה להתחלה ─────────────────
  const seen = [];
  for (let i = 0; i < ORDER.length + 1; i++) {
    seen.push(await snapshot(p));
    await p.click('button[title^="מעבר למצב"]');
    await p.waitForTimeout(1800);
  }

  const cycle = seen.slice(0, ORDER.length).map((s) => s.theme);

  /* לא רק "כל התמות הופיעו". סדר כמו dark → light → dim מכסה את
     שלושתן ועדיין אינו הסדר שהוגדר, והמשתמש היה מקבל קפיצות בלתי
     צפויות. משווים לסיבוב של ORDER שמתחיל מהתמה שנצפתה ראשונה. */
  const start = ORDER.indexOf(cycle[0]);
  const rotated = start >= 0
    ? ORDER.slice(start).concat(ORDER.slice(0, start))
    : [];
  check('המחזור עובר בכל התמות, לפי הסדר שהוגדר',
    rotated.length === cycle.length && rotated.every((t, i) => t === cycle[i]),
    `${cycle.join(' → ')}  (צפוי ${rotated.join(' → ') || '?'})`);
  check('והוא חוזר להתחלה', seen[ORDER.length].theme === seen[0].theme,
    `${seen[0].theme} … ${seen[ORDER.length].theme}`);

  // ── כל תמה מקבלת באמת את הערכה שלה ────────────────────────────────
  for (const name of ORDER) {
    const got = seen.filter((s) => s.theme === name)[0];
    const want = EXPECTED[name];
    if (!got) { check(`התמה ${name} הופיעה במחזור`, false); continue; }

    check(`${name}: הצבעים הם של הערכה`,
      got.bg === want.bg && got.accent === want.accent,
      `bg ${got.bg} · accent ${got.accent}`);
    check(`${name}: הגופן הוא של הערכה`,
      got.sans.indexOf(want.font) >= 0, got.sans);
    /* זה מה שהיה שקט: color-scheme חייב להיות dark או light בלבד.
       "dim" הוא ערך לא חוקי שהדפדפן מתעלם ממנו, ואז פסי הגלילה
       ופקדי הטפסים נראים מהמשפחה ההפוכה. */
    check(`${name}: color-scheme חוקי`,
      got.scheme === want.scheme, got.scheme);
    check(`${name}: אין דליפה של מטא-טוקנים ל-CSS`,
      !got.leakedScheme && !got.leakedEditorial,
      `--scheme:"${got.leakedScheme}" --editorial:"${got.leakedEditorial}"`);

    const serif = /serif/i.test(got.h1Font) && !/sans-serif/i.test(got.h1Font);
    check(`${name}: צורות הבלוקים הן ${want.editorial ? 'Editorial' : 'של הכהים'}`,
      serif === want.editorial, got.h1Font.slice(0, 40));

    await p.evaluate((t) => localStorage.setItem('md-docs-theme', t), name);
    await p.reload({ waitUntil: 'load' });
    await p.waitForSelector('#dc-root', { timeout: 20000 });
    await p.waitForTimeout(2200);
    const after = await snapshot(p);
    check(`${name}: ההעדפה שורדת רענון`, after.theme === name, after.theme);
    await p.screenshot({ path: `${SHOTS}/theme-${name}.png` });
  }

  // ── הכותרת אומרת לאן הלחיצה תוביל ─────────────────────────────────
  const titles = seen.slice(0, ORDER.length).map((s) => `${s.theme}:${s.title}`);
  const distinct = new Set(seen.slice(0, ORDER.length).map((s) => s.title));
  /* המספר נגזר מ-ORDER ואינו קבוע. קבוע 3 כאן היה בדיוק אותה בעיה
     שהתגובה למעלה מזהירה ממנה, רק בכיוון ההפוך: תמה רביעית עם כותרת
     תקינה לגמרי הייתה מפילה את הבדיקה, ותמה רביעית שחולקת כותרת עם
     אחרת הייתה עוברת. */
  check('לכל תמה כותרת אחרת בכפתור', distinct.size === ORDER.length, titles.join(' | '));
  const glyphs = new Set(seen.slice(0, ORDER.length).map((s) => s.glyph));
  check('ולכל תמה סמל אחר', glyphs.size === ORDER.length, [...glyphs].join(' '));

  const declared = declaredThemes();
  const undeclared = declared ? ORDER.filter((t) => declared.indexOf(t) < 0) : ORDER;
  check('כל תמה מוצהרת גם ב-data-props',
    !!declared && undeclared.length === 0,
    declared ? `חסרות: ${undeclared.join(', ') || 'אין'}` : 'לא נמצא defaultTheme.options');

  // ── ערך פסול ב-localStorage לא מפיל ולא נדבק ──────────────────────
  await p.evaluate(() => localStorage.setItem('md-docs-theme', 'אין-כזו'));
  await p.reload({ waitUntil: 'load' });
  await p.waitForSelector('#dc-root', { timeout: 20000 });
  await p.waitForTimeout(2200);
  const fallback = await snapshot(p);
  check('שם תמה פסול נופל לברירת מחדל',
    ORDER.indexOf(fallback.theme) >= 0 && fallback.bg === EXPECTED[fallback.theme].bg,
    `${fallback.theme} · ${fallback.bg}`);

  check('אין שגיאות קונסולה', errs.length === 0, errs.slice(0, 3).join(' | '));

  } finally {
    /* רץ גם כשבדיקה זרקה. אם גם המחיקה נכשלת, זה מדווח ולא מסתיר את
       השגיאה המקורית. */
    if (slug) {
      try {
        removed = await p.evaluate(async (s) => {
          const r = await fetch('/api/projects/' + encodeURIComponent(s),
            { method: 'DELETE', credentials: 'same-origin' });
          return r.status;
        }, slug);
      } catch (e) {
        removed = 'שגיאה: ' + e.message;
      }
    }
    await browser.close();
  }

  check('הפרויקט נמחק בסיום', removed === 204, 'status ' + removed);

  const failed = results.filter((r) => !r.ok);
  console.log(failed.length ? `\nנכשלו ${failed.length}` : '\nהכול עבר');
  process.exit(failed.length ? 1 : 0);
})().catch((e) => { console.error('FATAL', e.message); process.exit(1); });
