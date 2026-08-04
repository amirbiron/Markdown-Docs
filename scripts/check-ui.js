/* מדד הקבלה של שלב 5: יצירת פרויקט מהדפדפן, ופתיחתו מלקוח אחר.
 *
 * הרצה:
 *   DATABASE_URL=... ADMIN_EMAIL=... ADMIN_PASSWORD=... \
 *     python3 -m uvicorn app.main:app --port 8070
 *   node scripts/check-ui.js http://127.0.0.1:8070 <תיקיית-צילומים>
 */

let chromium;
try {
  ({ chromium } = require('playwright'));
} catch {
  console.error('playwright לא מותקן. הריצו: npm install');
  process.exit(1);
}

const BASE = process.argv[2];
const SHOTS = process.argv[3];
if (!BASE || !SHOTS) {
  console.error('שימוש: node scripts/check-ui.js <כתובת-בסיס> <תיקיית-צילומים>');
  process.exit(1);
}

const EMAIL = process.env.ADMIN_EMAIL || 'admin@example.com';
const PASSWORD = process.env.ADMIN_PASSWORD || 'correct-horse-battery';
const NAME = 'פרויקט הבדיקה ' + process.pid;

const results = [];
const check = (label, ok, extra) => {
  results.push({ label, ok, extra });
  console.log(`  ${ok ? '✓' : '✗'}  ${label}${extra ? '  — ' + extra : ''}`);
};

(async () => {
  const browser = await chromium.launch();
  const errs = [];

  // ── לקוח א': בעלים ────────────────────────────────────────────────
  const owner = await browser.newContext({ viewport: { width: 1500, height: 1000 } });
  const p = await owner.newPage();
  p.on('console', (m) => { if (m.type() === 'error') errs.push(m.text()); });
  p.on('pageerror', (e) => errs.push('PAGEERROR: ' + e.message));

  await p.goto(BASE, { waitUntil: 'load' });
  await p.waitForSelector('#dc-root', { timeout: 20000 });
  await p.waitForTimeout(3000);

  check('הדף עולה כאנונימי', await p.getByRole('button', { name: 'כניסה' }).isVisible());

  await p.getByRole('button', { name: 'כניסה' }).click();
  await p.waitForTimeout(600);
  await p.fill('input[type="email"]', EMAIL);
  await p.fill('input[type="password"]', PASSWORD);
  await p.getByRole('button', { name: 'כניסה', exact: true }).last().click();
  await p.waitForTimeout(2500);
  check('כניסה הצליחה', await p.getByRole('button', { name: 'יציאה' }).isVisible());

  // זו הבדיקה שמוכיחה ש-Origin נשלח בבקשות שמשנות מצב. אילו לא היה
  // נשלח, OriginGuard היה מחזיר 403 וכלום לא היה נוצר.
  await p.fill('input[placeholder="שם הפרויקט החדש"]', NAME);
  await p.getByRole('button', { name: 'יצירת פרויקט' }).click();
  await p.waitForTimeout(2500);
  const inProject = await p.evaluate(() => document.body.innerText);
  check('הפרויקט נוצר ונפתח', inProject.includes(NAME), NAME);
  await p.screenshot({ path: SHOTS + '/ui-project.png' });

  // מסמך דרך הדבקה
  await p.getByRole('button', { name: /paste markdown|הדבקת Markdown ידנית/i }).click();
  await p.waitForTimeout(500);
  await p.fill('textarea', '# מדריך התקנה\n\nפסקת פתיחה.\n\n## שלב ראשון\n\n- פריט\n- פריט נוסף\n');
  await p.getByRole('button', { name: 'הוספה לפרויקט' }).click();
  await p.waitForTimeout(2500);
  const docText = await p.evaluate(() => document.body.innerText);
  check('המסמך נוצר ומרונדר', docText.includes('מדריך התקנה') && docText.includes('שלב ראשון'));

  // מסמך דרך העלאת קובץ. מסלול נפרד לגמרי מההדבקה — הוא עובר דרך
  // readFiles, ואיפוס value של ה-input מרוקן את אותו FileList שמחזיקים.
  // בלי הבדיקה הזאת הרגרסיה הזו שקטה לחלוטין.
  const upload = SHOTS + '/upload-check.md';
  require('fs').writeFileSync(upload, '# מסמך מקובץ\n\nתוכן שהועלה.\n');
  await p.setInputFiles('input[type="file"]', [upload]);
  await p.waitForTimeout(3000);
  const uploaded = await p.evaluate(() => document.body.innerText);
  check('העלאת קובץ יוצרת מסמך', uploaded.includes('מסמך מקובץ') && uploaded.includes('תוכן שהועלה'));

  // קישור
  await p.fill('input[placeholder="שם הקישור"]', 'CodeKeeper');
  await p.fill('input[placeholder="https://…"]', 'https://codekeeper.com');
  await p.getByRole('button', { name: 'הוספה', exact: true }).click();
  await p.waitForTimeout(2000);
  const hrefs = await p.evaluate(() =>
    [...document.querySelectorAll('a')].map((a) => a.getAttribute('href'))
  );
  check('הקישור נוסף', hrefs.includes('https://codekeeper.com'));

  // פרסום, כדי שהלקוח השני יראה אותו בלי כניסה
  const published = await p.evaluate(async (slugName) => {
    const list = await (await fetch('/api/projects', { credentials: 'same-origin' })).json();
    const mine = list.find((x) => x.name === slugName);
    // בלי זה, כשל קודם ביצירת הפרויקט הופך כאן ל-TypeError בתוך evaluate,
    // שמטפס ל-catch הכללי ומבטל את כל הבדיקות שאחריו — הפלט מסתיר אז את
    // הכשל האמיתי במקום להצביע עליו.
    if (!mine) return null;
    const res = await fetch('/api/projects/' + encodeURIComponent(mine.slug), {
      method: 'PATCH',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ visibility: 'public' }),
    });
    return res.ok ? mine.slug : null;
  }, NAME);
  check('הפרויקט פורסם', !!published, published || '');

  await p.screenshot({ path: SHOTS + '/ui-doc.png' });

  // ── לקוח ב': דפדפן אחר לגמרי, בלי cookie ──────────────────────────
  const guest = await browser.newContext({ viewport: { width: 1500, height: 1000 } });
  const g = await guest.newPage();
  // אותם מאזינים כמו בדף הבעלים. בלעדיהם "אין שגיאות קונסולה" בסוף
  // ההרצה מדלג דווקא על מסלול המבקר האנונימי — זה שרוב הקוראים רואים.
  g.on('console', (m) => { if (m.type() === 'error') errs.push('אורח: ' + m.text()); });
  g.on('pageerror', (e) => errs.push('אורח PAGEERROR: ' + e.message));
  await g.goto(BASE, { waitUntil: 'load' });
  await g.waitForSelector('#dc-root', { timeout: 20000 });
  await g.waitForTimeout(3000);

  const guestSees = await g.evaluate(() => document.body.innerText);
  check('הלקוח השני רואה את הפרויקט', guestSees.includes(NAME));

  await g.getByText(NAME).first().click();
  await g.waitForTimeout(2500);
  const guestDoc = await g.evaluate(() => document.body.innerText);
  check('והוא רואה את התוכן', guestDoc.includes('מדריך התקנה') && guestDoc.includes('שלב ראשון'));
  check('ובלי כלי עריכה', !(await g.getByRole('button', { name: 'יצירת פרויקט' }).isVisible().catch(() => false)));
  await g.screenshot({ path: SHOTS + '/ui-guest.png' });

  // ── מצב בהיר עדיין עובד ───────────────────────────────────────────
  const before = await p.evaluate(() => document.documentElement.getAttribute('data-theme'));
  await p.click('button[title^="מעבר למצב"]');
  await p.waitForTimeout(1500);
  const after = await p.evaluate(() => document.documentElement.getAttribute('data-theme'));
  check('החלפת מצב עובדת', before !== after, `${before} → ${after}`);
  await p.screenshot({ path: SHOTS + '/ui-light.png' });

  check('אין שגיאות קונסולה', errs.length === 0, errs.slice(0, 3).join(' | '));

  // ── ניקוי ─────────────────────────────────────────────────────────
  // בלי זה כל הרצה משאירה פרויקט ומסמך במסד, ואחרי כמה הרצות מסך
  // הפרויקטים והחיפוש מתמלאים בזבל בדיקות. המחיקה מדורגת — CASCADE
  // מוריד גם את המסמכים, הגרסאות והקישורים.
  // הניקוי אינו מותנה בהצלחת הפרסום. אילו היה, כשל ב-PATCH היה משאיר
  // את הפרויקט במסד — כלומר בדיוק בהרצות הכושלות, שבהן חוזרים ומריצים
  // שוב ושוב, הזבל היה מצטבר הכי מהר.
  const removed = await p.evaluate(async (name) => {
    const list = await (await fetch('/api/projects', { credentials: 'same-origin' })).json();
    const mine = list.find((x) => x.name === name);
    if (!mine) return 'לא נמצא';
    const res = await fetch('/api/projects/' + encodeURIComponent(mine.slug), {
      method: 'DELETE',
      credentials: 'same-origin',
    });
    return res.status;
  }, NAME);
  check('הפרויקט נמחק בסיום', removed === 204, 'status ' + removed);

  await browser.close();
  const failed = results.filter((r) => !r.ok);
  console.log(failed.length ? `\nנכשלו ${failed.length}` : '\nהכול עבר');
  process.exit(failed.length ? 1 : 0);
})().catch((e) => { console.error('FATAL', e.message); process.exit(1); });
