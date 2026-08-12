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
  /* הרשאות הלוח נדרשות לבדיקת כפתור ההעתקה: בלעדיהן readText נדחה,
     והבדיקה הייתה מאשרת "הועתק" בלי לדעת מה באמת נכנס ללוח. */
  const ctx = await browser.newContext({
    viewport: { width: 1600, height: 1000 },
    permissions: ['clipboard-read', 'clipboard-write'],
  });
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


  // ── מסך היצירה ────────────────────────────────────────────────────
  // רענון מלא: הבדיקות שלמעלה השאירו את הדף עם visibilityState מזויף
  // ועם נתב שהוסר, ומצב כזה מסתיר רגרסיות במקום לחשוף אותן.
  await p.reload({ waitUntil: 'load' });
  await p.waitForSelector('#dc-root', { timeout: 20000 });
  await p.waitForTimeout(3000);
  await p.getByText(NAME).first().click();
  await p.waitForTimeout(2500);

  // הקלדה ואז מעבר מיידי למסך היצירה, בתוך חלון ה-debounce של
  // הפריוויו. הטיימר מחזיק את הערך הישן ב-closure, ואם הוא לא מבוטל
  // הוא מחזיר את תוכן המסמך ל-editView אחרי שהתיבה כבר רוקנה —
  // תיבה ריקה מול פריוויו של מסמך אחר.
  await p.getByRole('button', { name: 'עריכה', exact: true }).click();
  await p.locator('textarea').first().waitFor({ state: 'visible', timeout: 15000 });
  await p.fill('textarea', '# תוכן שלא אמור לדלוף לפריוויו של היצירה');
  await p.getByRole('button', { name: /כתיבת מסמך חדש|new document/i }).click();
  await p.waitForSelector('input[placeholder^="שם המסמך"]', { timeout: 15000 });
  await p.waitForTimeout(1200);           // מעבר ל-PREVIEW_MS
  const leaked = await p.evaluate(() => {
    const head = [...document.querySelectorAll('div')].filter((d) => d.textContent.trim() === 'תצוגה')[0];
    const box = head && head.parentElement;
    return {
      preview: box ? box.innerText.trim() : null,
      textarea: (document.querySelector('textarea') || {}).value,
    };
  });
  check('טיימר הפריוויו אינו מחזיר תוכן ישן אחרי מעבר ליצירה',
    leaked.textarea === '' && (leaked.preview || '').indexOf('שלא אמור לדלוף') < 0,
    JSON.stringify(leaked));

  // הכפתור בסרגל פותח את אותו מסך מפוצל של העריכה — גם כשאין מסמך
  // פתוח. קודם הוא היה מקונן בתוך בלוק המסמך ולא נפתח כלל.
  await p.getByRole('button', { name: /כתיבת מסמך חדש|new document/i }).click();
  // המתנה לפקד מזהה ולא לשעון: שדה השם קיים רק במסך היצירה, ולכן
  // הופעתו היא הסימן שהמסך נפתח. השהיה קבועה עוברת גם כשהוא איטי.
  await p.waitForSelector('input[placeholder^="שם המסמך"]', { timeout: 15000 });

  const createPane = await p.evaluate(() => {
    const ta = document.querySelector('textarea');
    return {
      textareaHeight: ta ? Math.round(ta.getBoundingClientRect().height) : 0,
      hasNameField: !!document.querySelector('input[placeholder^="שם המסמך"]'),
      hasToolbar: !!document.querySelector('button[title]'),
    };
  });
  check('מסך היצירה נפתח כמסך מפוצל',
    createPane.textareaHeight > 300 && createPane.hasNameField && createPane.hasToolbar,
    JSON.stringify(createPane));

  const draft = '# כותרת הטיוטה\n\nפסקה.\n\n## תת פרק\n\n1. פריט ראשון';
  await p.fill('textarea', draft);
  await p.waitForTimeout(1500);

  // כותרת רמה 1 חייבת להופיע בפריוויו של מסך היצירה: אין כאן כותרת
  // עמוד שתציג אותה, ובלי הדגל היא נבלעה בשקט.
  const previewTags = await p.evaluate(() => {
    const head = [...document.querySelectorAll('div')].filter((d) => d.textContent.trim() === 'תצוגה')[0];
    const box = head && head.parentElement;
    return box ? [...box.querySelectorAll('h1,h2,p,li')].map((e) => e.tagName) : [];
  });
  check('הפריוויו מציג גם כותרת רמה 1', previewTags.indexOf('H1') >= 0, previewTags.join(','));

  // Enter בסוף פריט ממשיך את הרשימה במספר הבא
  await p.click('textarea');
  await p.keyboard.press('End');
  await p.keyboard.press('Enter');
  await p.keyboard.type('פריט שני');
  await p.waitForTimeout(400);
  const continued = await p.inputValue('textarea');
  check('Enter ממשיך רשימה ממוספרת',
    continued.indexOf('2. פריט שני') >= 0,
    JSON.stringify(continued.slice(-24)));

  // Enter על פריט ריק יוצא מהרשימה במקום לייצר עוד "3."
  await p.keyboard.press('Enter');
  await p.waitForTimeout(200);
  await p.keyboard.press('Enter');
  await p.waitForTimeout(300);
  const exited = await p.inputValue('textarea');
  check('פריט ריק יוצא מהרשימה', !/3\./.test(exited), JSON.stringify(exited.slice(-20)));

  // Enter מיד אחרי הסימון, כשיש טקסט אחריו. מה שלפני הסמן נראה ריק
  // כאן, והחלטה על סמך זה בלבד הייתה מוחקת את הסימון ומשאירה את
  // הטקסט מרחף מחוץ לרשימה.
  await p.fill('textarea', '- אלף');
  await p.waitForTimeout(300);
  await p.evaluate(() => {
    const el = document.querySelector('textarea');
    el.focus();
    el.setSelectionRange(2, 2);           // בדיוק אחרי "- "
  });
  await p.keyboard.press('Enter');
  await p.waitForTimeout(400);
  const midMarker = await p.inputValue('textarea');
  check('Enter אחרי הסימון מפצל ולא מוחק אותו',
    midMarker === '- \n- אלף', JSON.stringify(midMarker));

  // רשימה באותיות עבריות: הסופיות יושבות בין האותיות בטבלת יוניקוד,
  // ולכן י+1 הוא ך ולא כ. הבדיקה עוברת דווקא על המעברים האלה.
  for (const [from, to] of [['ט', 'י'], ['י', 'כ'], ['ל', 'מ'], ['פ', 'צ']]) {
    await p.fill('textarea', from + ') פריט');
    await p.waitForTimeout(300);
    await p.click('textarea');
    await p.keyboard.press('End');
    await p.keyboard.press('Enter');
    await p.waitForTimeout(350);
    const grew = await p.inputValue('textarea');
    check(`רשימה באותיות: ${from} ממשיך ל-${to}`,
      grew === from + ') פריט\n' + to + ') ', JSON.stringify(grew));
  }

  // ת היא סוף האלף-בית — אין לאן להמשיך, ו-Enter חוזר להיות רגיל
  await p.fill('textarea', 'ת) אחרון');
  await p.waitForTimeout(300);
  await p.click('textarea');
  await p.keyboard.press('End');
  await p.keyboard.press('Enter');
  await p.waitForTimeout(350);
  const afterTav = await p.inputValue('textarea');
  check('ת מסיימת את הרצף', afterTav === 'ת) אחרון\n', JSON.stringify(afterTav));

  // לחיצה חוזרת על "מסמך חדש" מוחקת טיוטה בדיוק כמו יציאה, ולכן היא
  // עוברת באותו אישור. בלי זה היא הייתה המסלול היחיד שמוחק בשקט.
  await p.fill('textarea', '# טיוטה שנייה');
  await p.waitForTimeout(600);
  let reclicks = 0;
  const denyReset = async (d) => { reclicks++; await d.dismiss(); };
  p.on('dialog', denyReset);
  await p.getByRole('button', { name: /כתיבת מסמך חדש|new document/i }).click();
  await p.waitForTimeout(900);
  const survived = await p.inputValue('textarea');
  p.off('dialog', denyReset);
  check('לחיצה חוזרת על "מסמך חדש" מבקשת אישור',
    reclicks === 1 && survived === '# טיוטה שנייה', `${reclicks} אישורים`);

  // הבדיקות שלמעלה דרסו את התיבה. מחזירים את הטיוטה כפי שהייתה אחרי
  // המשך הרשימה, כי ההגשה וההעתקה בהמשך נשענות עליה.
  await p.fill('textarea', draft + '\n2. פריט שני');
  await p.waitForTimeout(1500);

  // ניווט החוצה עם טיוטה פתוחה מבקש אישור, וביטול משאיר את הטקסט.
  // הטיוטה חיה בזיכרון בלבד — יציאה שקטה היא אובדן נתונים.
  let prompts = 0;
  const dismiss = async (d) => { prompts++; await d.dismiss(); };
  p.on('dialog', dismiss);
  await p.getByRole('link', { name: /כל הפרויקטים/ }).click();
  await p.waitForTimeout(1200);
  const kept = await p.evaluate(() => {
    const ta = document.querySelector('textarea');
    return { stillHere: !!document.querySelector('input[placeholder^="שם המסמך"]'), val: ta ? ta.value : '' };
  });
  p.off('dialog', dismiss);
  check('יציאה עם טיוטה פתוחה מבקשת אישור, וביטול משמר אותה',
    prompts === 1 && kept.stillHere && kept.val.indexOf('כותרת הטיוטה') >= 0,
    `${prompts} אישורים`);

  await p.fill('input[placeholder^="שם המסמך"]', 'מסמך ממסך היצירה');
  await p.getByRole('button', { name: 'הוספה לפרויקט' }).click();
  await p.waitForTimeout(2500);
  const createdText = await p.evaluate(() => document.body.innerText);
  check('הטיוטה נוספה לפרויקט',
    createdText.indexOf('מסמך ממסך היצירה') >= 0 && createdText.indexOf('תת פרק') >= 0);
  await p.screenshot({ path: SHOTS + '/editor-create.png' });

  // ── שינוי שם מסמך ─────────────────────────────────────────────────
  // שלושה דברים נמדדים כאן, וכל אחד מהם היה יכול להישבר לבד: שורת
  // ה-.md זזה עם השם, השורה שמתחילה ב-# לא, והשמירה שאחרי השינוי
  // מגיעה לנתיב החדש.
  await p.locator('button[title="עריכה"]').click();
  await p.waitForSelector('input[placeholder="שם המסמך"]', { timeout: 15000 });

  const nameField = p.locator('input[placeholder="שם המסמך"]');
  const beforeRename = await p.evaluate(() => ({
    /* מאותר לפי התוכן (מסתיים ב-.md) ולא לפי dir. הבורר הקודם נשען על
       dir="ltr" — מאפיין של כיוון כתיבה, שנשבר ברגע שהוא שונה ל-auto
       כדי ששם עברי ייקרא נכון. הבדיקה נפלה על שינוי שאינו קשור אליה. */
    badge: ([...document.querySelectorAll('[data-doc] header span')]
      .find((s) => /\.md$/.test(s.textContent.trim())) || {}).textContent,
    button: [...document.querySelectorAll('button')].filter((b) => b.textContent.trim() === 'שינוי שם').length,
  }));
  check('כפתור שינוי השם מוסתר כשהשם לא שונה', beforeRename.button === 0, JSON.stringify(beforeRename));

  await nameField.fill('מדריך מפורט');
  await p.waitForTimeout(400);
  await p.locator('button:has-text("שינוי שם")').click();
  await p.waitForTimeout(2500);

  const afterRename = await p.evaluate(() => ({
    /* מאותר לפי התוכן (מסתיים ב-.md) ולא לפי dir. הבורר הקודם נשען על
       dir="ltr" — מאפיין של כיוון כתיבה, שנשבר ברגע שהוא שונה ל-auto
       כדי ששם עברי ייקרא נכון. הבדיקה נפלה על שינוי שאינו קשור אליה. */
    badge: ([...document.querySelectorAll('[data-doc] header span')]
      .find((s) => /\.md$/.test(s.textContent.trim())) || {}).textContent,
    firstLine: (document.querySelector('textarea') || {}).value.split('\n')[0],
  }));
  check('שורת ה-md נעה עם השם',
    afterRename.badge === 'מדריך-מפורט.md' && afterRename.badge !== beforeRename.badge,
    JSON.stringify(afterRename.badge));
  check('השורה שמתחילה ב-# לא השתנתה',
    afterRename.firstLine === '# כותרת הטיוטה', JSON.stringify(afterRename.firstLine));

  // הבדיקה החדה: docSlug חייב לעבור לחדש, אחרת השמירה הבאה מקבלת 404
  // ומה שהוקלד נעלם בלי שום סימן.
  const afterMark = 'נכתב אחרי שינוי השם ' + process.pid;
  await p.locator('textarea').fill('# כותרת הטיוטה\n\n' + afterMark);
  await p.waitForTimeout(4500);
  const afterRenameSave = await p.evaluate(async (slug) => {
    const r = await fetch(`/api/projects/${encodeURIComponent(slug)}/docs/${encodeURIComponent('מדריך-מפורט')}`,
      { credentials: 'same-origin' });
    return r.ok ? (await r.json()).content : 'HTTP ' + r.status;
  }, setup.slug);
  check('שמירה אחרי שינוי השם מגיעה לנתיב החדש',
    afterRenameSave.indexOf(afterMark) >= 0, JSON.stringify(afterRenameSave.slice(0, 40)));

  // ── כפתור ההעתקה ──────────────────────────────────────────────────
  // מה שנבדק הוא תוכן הלוח ולא הסימן שהתחלף: כפתור שמצייר ✓ בלי
  // להעתיק דבר עובר כל בדיקה שמסתכלת רק על הסימן.
  await p.locator('button[title="העתקת המסמך"]').click();
  await p.waitForTimeout(500);
  const clip = await p.evaluate(() => navigator.clipboard.readText());
  /* נמדד מול מה שבתיבה ברגע הזה ולא מול מחרוזת קבועה. בדיקות שקדמו
     כאן משנות את התוכן, וציפייה קשיחה הייתה נשברת בכל פעם שמישהו
     מוסיף שלב לפניה — כישלון שנראה כמו באג בהעתקה ואינו. */
  const inBox = await p.locator('textarea').first().inputValue();
  check('כפתור ההעתקה מעתיק את מקור המסמך',
    clip === inBox && clip.length > 0,
    JSON.stringify(clip.slice(0, 30)));
  const copiedGlyph = await p.evaluate(
    () => (document.querySelector('button[title="הועתק"]') || {}).textContent
  );
  check('הכפתור מסמן שההעתקה בוצעה', copiedGlyph === '✓', JSON.stringify(copiedGlyph));

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
