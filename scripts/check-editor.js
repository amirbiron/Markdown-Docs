/* מדדי הקבלה של שלב 6, לפי ROADMAP.md:
 *
 *   הקלדה במסמך של אלף שורות לא מקרטעת; לחיצה על כפתור עם טקסט מסומן
 *   עוטפת אותו ולא דורסת; בלוק עברי בלי שפה מתיישר לימין, ובלוק ```js
 *   עם הערות עבריות נשאר משמאל.
 *
 * הרצה:
 *   DATABASE_URL=... ADMIN_EMAIL=... ADMIN_PASSWORD=... \
 *     python3 -m uvicorn app.main:app --port 8070
 *   node scripts/check-editor.js http://127.0.0.1:8070 <תיקיית-צילומים>
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
  console.error('שימוש: node scripts/check-editor.js <כתובת-בסיס> <תיקיית-צילומים>');
  process.exit(1);
}

const EMAIL = process.env.ADMIN_EMAIL || 'admin@example.com';
const PASSWORD = process.env.ADMIN_PASSWORD || 'correct-horse-battery';
const NAME = 'בדיקת עורך ' + process.pid;

const results = [];
const check = (label, ok, extra) => {
  results.push({ label, ok, extra });
  console.log(`  ${ok ? '✓' : '✗'}  ${label}${extra ? '  — ' + extra : ''}`);
};

/* מסמך ארוך אמיתי, לא אלף שורות ריקות: פרסור של שורות ריקות זול,
   ומדידה עליו הייתה מראה ביצועים שלא קיימים במסמך אמיתי. */
function bigDocument(lines) {
  const out = ['# מסמך גדול', ''];
  for (let i = 0; out.length < lines; i++) {
    out.push('## פרק ' + i, '', 'פסקה עם **הדגשה** ו-`קוד` וקישור [לכאן](https://example.com).', '',
      '- פריט ראשון', '- פריט שני', '', '```js', 'const x = ' + i + ';', '```', '');
  }
  return out.slice(0, lines).join('\n');
}

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
  const p = await ctx.newPage();
  const errs = [];
  p.on('console', (m) => { if (m.type() === 'error') errs.push(m.text()); });
  p.on('pageerror', (e) => errs.push('PAGEERROR: ' + e.message));

  // ── הכנה: פרויקט ומסמך ────────────────────────────────────────────
  await p.goto(BASE + '/', { waitUntil: 'load' });
  await p.waitForSelector('#dc-root', { timeout: 20000 });
  await p.waitForTimeout(3000);

  const setup = await p.evaluate(async ([email, password, name]) => {
    const post = (u, b) => fetch(u, { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b) });
    const login = await post('/api/auth/login', { email, password });
    if (!login.ok) return { error: 'כניסה נכשלה: ' + login.status };
    const created = await post('/api/projects', { name });
    if (!created.ok) return { error: 'יצירת פרויקט נכשלה: ' + created.status };
    const slug = (await created.json()).slug;
    const doc = await post(`/api/projects/${encodeURIComponent(slug)}/docs`,
      { title: 'טיוטה', content: '# טיוטה\n\nפסקה ראשונה.\n' });
    if (!doc.ok) return { error: 'יצירת מסמך נכשלה: ' + doc.status };
    /* המסמך השני נוצר כאן ולא בהמשך: הסיידבר נטען פעם אחת עם פתיחת
       הפרויקט, ומסמך שנוצר אחר כך דרך ה-API לא מופיע בו. */
    const second = await post(`/api/projects/${encodeURIComponent(slug)}/docs`,
      { title: 'מסמך שני', content: '# מסמך שני\n' });
    if (!second.ok) return { error: 'יצירת מסמך שני נכשלה: ' + second.status };
    return { slug, docSlug: (await doc.json()).slug };
  }, [EMAIL, PASSWORD, NAME]);

  if (setup.error) { console.error('FATAL ' + setup.error); await browser.close(); process.exit(1); }

  /* המתנה לתנאי ולא לשעון. השהיות קבועות כאן נכשלו בפועל כשהטעינה
     איטית מהצפוי, והכישלון נראה כמו באג במוצר במקום כמו בדיקה חסרת
     סבלנות. ההשהיות הקבועות שנשארו בהמשך הן מדידה — שם הן המדד עצמו. */
  await p.reload({ waitUntil: 'load' });
  await p.waitForSelector('#dc-root', { timeout: 20000 });

  const projectLink = p.getByText(NAME).first();
  await projectLink.waitFor({ state: 'visible', timeout: 20000 });
  await projectLink.click();

  const editButton = p.getByRole('button', { name: 'עריכה', exact: true });
  await editButton.waitFor({ state: 'visible', timeout: 20000 });
  await editButton.click();

  await p.locator('textarea').first().waitFor({ state: 'visible', timeout: 20000 });
  check('העורך נפתח', await p.locator('textarea').first().isVisible());

  const ta = p.locator('textarea').first();

  // ── מדד: עטיפה ולא דריסה ──────────────────────────────────────────
  await ta.fill('טקסט חשוב');
  await p.waitForTimeout(400);
  await p.evaluate(() => {
    const el = document.querySelector('textarea');
    el.focus();
    el.setSelectionRange(0, 'טקסט חשוב'.length);
  });
  await p.locator('button[title="הדגשה"]').click();
  await p.waitForTimeout(600);
  const wrapped = await ta.inputValue();
  check('כפתור עוטף בחירה ולא דורס', wrapped === '==טקסט חשוב==', JSON.stringify(wrapped));

  // והסמן נשאר בתוך התבנית, על מה שנעטף
  const sel = await p.evaluate(() => {
    const el = document.querySelector('textarea');
    return { start: el.selectionStart, end: el.selectionEnd, val: el.value };
  });
  check('הסמן נשאר על הטקסט שנעטף',
    sel.val.slice(sel.start, sel.end) === 'טקסט חשוב', `[${sel.start},${sel.end}]`);

  // בלי בחירה — נכנס placeholder, והוא מסומן
  await ta.fill('');
  await p.waitForTimeout(300);
  await p.locator('button[title="מודגש"]').click();
  await p.waitForTimeout(600);
  const ph = await p.evaluate(() => {
    const el = document.querySelector('textarea');
    return { val: el.value, picked: el.value.slice(el.selectionStart, el.selectionEnd) };
  });
  check('בלי בחירה נכנס placeholder מסומן', ph.val === '**טקסט**' && ph.picked === 'טקסט',
    JSON.stringify(ph));

  // ── מדד: RTL בבלוקי קוד ───────────────────────────────────────────
  const rtlDoc = [
    '# בדיקת כיוון', '',
    '```', 'זהו טקסט עברי בתוך גדר בלי שפה כלל.', 'שורה שנייה בעברית מלאה.', '```', '',
    '```js', '// הערה בעברית מלאה כאן, ועוד הערה בעברית', 'const x = 1;', '```', '',
    '```', 'plain english text inside a fence with no language', '```', ''
  ].join('\n');
  await ta.fill(rtlDoc);
  await p.waitForTimeout(1200);

  const dirs = await p.evaluate(() =>
    [...document.querySelectorAll('pre')].map((el) => ({
      dir: el.getAttribute('dir'),
      align: el.style.textAlign,
      head: (el.textContent || '').trim().slice(0, 28),
    }))
  );
  const hebrewPlain = dirs.filter((d) => d.head.startsWith('זהו טקסט'))[0];
  const jsBlock = dirs.filter((d) => d.head.indexOf('הערה בעברית') >= 0)[0];
  const englishPlain = dirs.filter((d) => d.head.startsWith('plain english'))[0];

  check('בלוק עברי בלי שפה מתיישר לימין',
    !!hebrewPlain && hebrewPlain.dir === 'rtl' && hebrewPlain.align === 'right',
    JSON.stringify(hebrewPlain));
  check('בלוק ```js עם הערות עבריות נשאר משמאל',
    !!jsBlock && jsBlock.dir === 'ltr' && jsBlock.align === 'left',
    JSON.stringify(jsBlock));
  check('בלוק אנגלי בלי שפה נשאר משמאל',
    !!englishPlain && englishPlain.dir === 'ltr', JSON.stringify(englishPlain));

  await p.screenshot({ path: SHOTS + '/editor-rtl.png' });

  // ── מדד: מסמך של אלף שורות לא מקרטע ───────────────────────────────
  const big = bigDocument(1000);
  await p.evaluate((text) => {
    /* הזרקה ישירה דרך ה-setter של React, כדי שהמדידה תמדוד הקלדה
       ולא את זמן ההדבקה של אלף שורות. */
    const el = document.querySelector('textarea');
    const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
    setter.call(el, text);
    el.dispatchEvent(new Event('input', { bubbles: true }));
  }, big);
  await p.waitForTimeout(2500);

  const lineCount = await p.evaluate(() => document.querySelector('textarea').value.split('\n').length);
  check('נטען מסמך של 1000 שורות', lineCount === 1000, lineCount + ' שורות');

  // 40 הקלדות רצופות, ומודדים כמה זמן כל אחת חוסמת את ה-thread
  await p.evaluate(() => {
    const el = document.querySelector('textarea');
    el.focus();
    el.setSelectionRange(el.value.length, el.value.length);
  });
  const t0 = Date.now();
  const gaps = [];
  for (let i = 0; i < 40; i++) {
    const before = Date.now();
    await p.keyboard.type('א');
    gaps.push(Date.now() - before);
  }
  const total = Date.now() - t0;
  gaps.sort((a, b) => a - b);
  const median = gaps[Math.floor(gaps.length / 2)];
  const worst = gaps[gaps.length - 1];
  /* 120ms לתו הוא הרבה מעבר להקלדה מהירה (~60-80ms בין תווים). חריגה
     כאן פירושה שהפרסר רץ בתוך מסלול ההקלדה. */
  check('הקלדה במסמך גדול לא מקרטעת',
    median < 120, `חציון ${median}ms · גרוע ${worst}ms · 40 תווים ב-${total}ms`);

  // ── מדד: השמירה לא מציפה את השרת ──────────────────────────────────
  const puts = [];
  p.on('request', (r) => { if (r.method() === 'PUT') puts.push(r.url()); });
  for (let i = 0; i < 25; i++) await p.keyboard.type('ב');
  await p.waitForTimeout(4000);
  check('הקלדה רציפה אינה מייצרת בקשה לכל תו', puts.length > 0 && puts.length <= 3,
    puts.length + ' בקשות PUT על 25 תווים');

  // ── מדד: מעבר מסמך באמצע ההמתנה לא מאבד ───────────────────────────
  const marker = 'סימן ' + process.pid;
  await ta.fill('# טיוטה\n\n' + marker + '\n');
  await p.waitForTimeout(300);          // הרבה פחות מ-SAVE_MS
  await p.getByText('מסמך שני').first().click();
  await p.waitForTimeout(3000);

  const persisted = await p.evaluate(async ([slug, docSlug]) => {
    const r = await fetch(`/api/projects/${encodeURIComponent(slug)}/docs/${encodeURIComponent(docSlug)}`,
      { credentials: 'same-origin' });
    return r.ok ? (await r.json()).content : null;
  }, [setup.slug, setup.docSlug]);
  check('מעבר מסמך באמצע ההמתנה לא מאבד את השמירה',
    !!persisted && persisted.indexOf(marker) >= 0, JSON.stringify((persisted || '').slice(0, 40)));

  /* בדיקת הקונסולה רצה כאן ולא בסוף: הבדיקה הבאה מפילה בקשות בכוונה,
     והדפדפן מדווח על כל אחת מהן כשגיאה. ערבוב של השתיים היה הופך את
     "אין שגיאות" לבדיקה שאי אפשר להעביר. */
  check('אין שגיאות קונסולה', errs.length === 0, errs.slice(0, 3).join(' | '));

  // ── מדד: שמירה שנכשלה ברשת אינה מאבדת תוכן ────────────────────────
  // הכשל הזה שקט: המשתמש כתב, הרשת נפלה, והוא לא מקליד עוד תו אלא פשוט
  // עוזב. אם מסלול היציאה מדלג על מצב failed, מה שנכתב נעלם בלי סימן.
  // הבדיקה הקודמת העבירה אותנו למסמך השני. חוזרים לראשון, כי הוא זה
  // שנבדק מול השרת בסוף.
  const backToFirst = p.getByText('טיוטה').first();
  await backToFirst.waitFor({ state: 'visible', timeout: 20000 });
  await backToFirst.click();
  await p.waitForTimeout(2000);

  await p.route('**/api/projects/**', (route) =>
    route.request().method() === 'PUT' ? route.abort('failed') : route.continue());

  const failMarker = 'תוכן אחרי כשל ' + process.pid;
  await ta.fill('# טיוטה\n\n' + failMarker + '\n');
  await p.waitForTimeout(4500);          // מעבר ל-SAVE_MS — השמירה נכשלה
  const showedFailure = await p.evaluate(() => document.body.innerText.indexOf('השמירה נכשלה') >= 0);
  check('כשל שמירה מוצג למשתמש', showedFailure);

  // הרשת חוזרת, והמשתמש עוזב בלי להקליד עוד תו
  await p.unroute('**/api/projects/**');
  await p.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await p.waitForTimeout(3000);

  const afterFail = await p.evaluate(async ([slug, docSlug]) => {
    const r = await fetch(`/api/projects/${encodeURIComponent(slug)}/docs/${encodeURIComponent(docSlug)}`,
      { credentials: 'same-origin' });
    return r.ok ? (await r.json()).content : null;
  }, [setup.slug, setup.docSlug]);
  check('שמירה שנכשלה מנוסה שוב ביציאה',
    !!afterFail && afterFail.indexOf(failMarker) >= 0,
    JSON.stringify((afterFail || '').slice(0, 40)));


  // ── ניקוי ─────────────────────────────────────────────────────────
  const removed = await p.evaluate(async (slug) => {
    const r = await fetch('/api/projects/' + encodeURIComponent(slug),
      { method: 'DELETE', credentials: 'same-origin' });
    return r.status;
  }, setup.slug);
  check('הפרויקט נמחק בסיום', removed === 204, 'status ' + removed);

  await browser.close();
  const failed = results.filter((r) => !r.ok);
  console.log(failed.length ? `\nנכשלו ${failed.length}` : '\nהכול עבר');
  process.exit(failed.length ? 1 : 0);
})().catch((e) => { console.error('FATAL', e.message); process.exit(1); });
