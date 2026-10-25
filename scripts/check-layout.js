/* רוחב אזור התוכן: עמוד המסמך מול מסך העריכה, עם תוכן עניינים ובלעדיו.
 *
 * שתי החלטות נבדקות כאן, ושתיהן נמדדות בדפדפן ולא נקראות מהקוד:
 *
 * 1. בעמוד המסמך רוחב הקריאה אחיד בכל הערכות — אותו רוחב כמו בערכה
 *    "עמום". קודם לכן הערכות ה-Editorial נשאו גבול רוחב נוסף על כל
 *    פסקה, רשימה וציטוט (68ch ודומיו), כך שהטקסט נעצר באמצע אזור רחב.
 *    שני מקורות לאותו מימד היו נסחפים זה מזה בשקט.
 *
 * 2. מסך העריכה (העורך עם הלייב פריוויו) ממלא את כל העמודה הפנויה:
 *    עד תוכן העניינים כשהוא פתוח, ועד המקום שבו הוא היה כשהוא סגור.
 *    קודם לכן קיפול תוכן העניינים השאיר שטח ריק משמאל, כי אזור התוכן
 *    נשאר עם אותה תקרת רוחב.
 *
 * המרווח בין העורך לתוכן העניינים חייב להיות זהה למרווח בין עמודת
 * המסמכים לעורך. זו דרישה מפורשת, והיא נבדקת כמספר ולא כתחושה.
 *
 * הערכים כאן קשיחים בכוונה (READ_W), כמו ב-check-themes.js: בדיקה ששואבת
 * את הרוחב מ-index.html הייתה משווה את הקוד לעצמו ועוברת תמיד.
 *
 * הרצה:
 *   node scripts/check-layout.js http://127.0.0.1:8070 <תיקיית-צילומים>
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
  console.error('שימוש: node scripts/check-layout.js <כתובת-בסיס> <תיקיית-צילומים>');
  process.exit(1);
}

const EMAIL = process.env.ADMIN_EMAIL || 'admin@example.com';
const PASSWORD = process.env.ADMIN_PASSWORD || 'correct-horse-battery';
const NAME = 'בדיקת פריסה ' + process.pid;

/* רוחב הקריאה בעמוד המסמך — הרוחב של הערכה "עמום", שנבחר כנכון. */
const READ_W = 900;

/* 1480 הוא רוחב הטאבלט שעליו נבחר הרוחב (נגזר מצילום מסך, לא נמדד על
   המכשיר). 1920 הוא מסך רחב, שבו המסגרת כולה כבר מוגבלת ויש שוליים
   משני צדיה — שם בודקים שהעורך לא נמתח מעבר למסגרת. */
const WIDTHS = [1480, 1920];

/* סטייה של פיקסל אחד מותרת: גבולות מעוגלים לפיקסל משני הצדדים. */
const near = (a, b) => Math.abs(a - b) <= 1;

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

const results = [];
const check = (label, ok, extra) => {
  results.push({ label, ok, extra });
  console.log(`  ${ok ? '✓' : '✗'}  ${label}${extra ? '  — ' + extra : ''}`);
};

/* מדידה אחת של הפריסה. כל מספר כאן הוא getBoundingClientRect של
   אלמנט אמיתי, ולא ערך מחושב של CSS — מה שקובע הוא איפה הדפדפן צייר. */
const measure = (page) =>
  page.evaluate(() => {
    const main = document.querySelector('main#top');
    if (!main) return { missing: 'main' };
    const grid = main.parentElement;
    const toc = document.querySelector('aside[data-toc]');
    const rail = [...grid.children].find((el) => el.tagName === 'ASIDE' && !el.hasAttribute('data-toc'));
    const cs = getComputedStyle(main);
    const m = main.getBoundingClientRect();
    const g = grid.getBoundingClientRect();
    const t = toc ? toc.getBoundingClientRect() : null;
    const r = rail ? rail.getBoundingClientRect() : null;
    /* קצוות תיבת התוכן של main — שם מתחיל ונגמר מה שהמשתמש רואה. */
    const innerL = m.left + parseFloat(cs.paddingLeft);
    const innerR = m.right - parseFloat(cs.paddingRight);

    /* המסך המפוצל של העורך: האב הראשון של תיבת ההקלדה שמכיל גם את
       חלונית התצוגה. ממנו נמדד אם העורך באמת ממלא את אזור התוכן, ולא
       רק main שמסביבו. */
    const ta = main.querySelector('textarea');
    let split = null;
    if (ta) {
      let el = ta.parentElement;
      while (el && el !== main) {
        if (el.children.length === 2 && el.getBoundingClientRect().width > ta.getBoundingClientRect().width * 1.8) { split = el; break; }
        el = el.parentElement;
      }
    }
    const art = main.querySelector('article');
    const widest = (sel) => {
      if (!art) return null;
      let w = 0;
      art.querySelectorAll(sel).forEach((el) => { w = Math.max(w, el.getBoundingClientRect().width); });
      return Math.round(w);
    };
    return {
      tocShown: !!t,
      mainL: m.left, mainR: m.right, mainW: m.width,
      innerL, innerR, innerW: innerR - innerL,
      gridL: g.left,
      tocL: t ? t.left : null, tocR: t ? t.right : null,
      railL: r ? r.left : null,
      splitL: split ? split.getBoundingClientRect().left : null,
      splitR: split ? split.getBoundingClientRect().right : null,
      textarea: ta ? Math.round(ta.getBoundingClientRect().width) : null,
      p: widest(':scope > p'),
      ul: widest(':scope > ul'),
      quote: widest(':scope > blockquote'),
      hscroll: document.documentElement.scrollWidth > window.innerWidth,
    };
  });

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: WIDTHS[0], height: 950 } });
  const p = await ctx.newPage();
  const errs = [];
  p.on('console', (m) => { if (m.type() === 'error') errs.push(m.text()); });
  p.on('pageerror', (e) => errs.push('PAGEERROR: ' + e.message));

  await p.goto(BASE + '/', { waitUntil: 'load' });
  await p.waitForSelector('#dc-root', { timeout: 20000 });

  /* פסקאות, רשימה וציטוט ארוכים מספיק כדי להגיע לקצה האזור. בלי זה
     פסקה קצרה הייתה "צרה" גם בלי שום גבול רוחב, והבדיקה הייתה נכשלת
     מהסיבה הלא נכונה — או עוברת על גבול שקיים. */
  const setup = await p.evaluate(async ([email, password, name]) => {
    const post = (u, b) => fetch(u, { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b) });
    const login = await post('/api/auth/login', { email, password });
    if (!login.ok) return { error: 'כניסה נכשלה: ' + login.status };
    const created = await post('/api/projects', { name });
    if (!created.ok) return { error: 'יצירת פרויקט נכשלה: ' + created.status };
    const slug = (await created.json()).slug;
    const long = 'טקסט ארוך מספיק כדי למלא שורה שלמה ולראות עד איפה היא נמתחת ברוחב העמוד. ';
    const content = [
      '# מסמך פריסה', '',
      long.repeat(5), '',
      '## כותרת ראשונה', '',
      long.repeat(8), '',
      '- ' + long.repeat(3), '- פריט שני', '',
      '> ' + long.repeat(3), '',
      '## כותרת שנייה', '',
      long.repeat(4), '',
    ].join('\n');
    const doc = await post(`/api/projects/${encodeURIComponent(slug)}/docs`,
      { title: 'מסמך פריסה', slug: 'layout', content });
    if (!doc.ok) return { error: 'יצירת מסמך נכשלה: ' + doc.status };
    return { slug };
  }, [EMAIL, PASSWORD, NAME]);
  if (setup.error) { console.error('FATAL ' + setup.error); await browser.close(); process.exit(1); }

  const slug = setup.slug;
  const docUrl = `${BASE}/#/${encodeURIComponent(slug)}/layout`;
  let removed = 'לא רץ';

  /* הכפתור שבסרגל העליון — אותו כפתור שהמשתמש לוחץ עליו. ה-✕ שבתוך
     תוכן העניינים אינו יכול לפתוח אותו בחזרה, ולכן הוא לא משמש כאן. */
  const setToc = async (want) => {
    const shown = await p.locator('aside[data-toc]').count() > 0;
    if (shown === want) return;
    const btn = await p.evaluateHandle(() => [...document.querySelectorAll('button[aria-label$="תוכן העניינים"]')]
      .find((b) => !b.closest('aside[data-toc]')) || null);
    const el = btn.asElement();
    if (!el) throw new Error('כפתור תוכן העניינים בסרגל לא נמצא');
    await el.click();
    await p.waitForTimeout(300);
    const now = await p.locator('aside[data-toc]').count() > 0;
    if (now !== want) throw new Error(`הלחיצה לא החליפה את מצב תוכן העניינים (רצוי ${want})`);
  };

  const openDoc = async (theme, width) => {
    await p.setViewportSize({ width, height: 950 });
    await p.goto(docUrl);
    await p.evaluate((t) => { localStorage.setItem('md-docs-theme', t); }, theme);
    await p.reload({ waitUntil: 'load' });
    await p.waitForSelector('main article', { timeout: 20000 });
    await p.waitForTimeout(400);
    /* בקרת שפיות: הערכה שהתבקשה היא הערכה שפעילה, אחרת כל המדידות
       הבאות מתייחסות לערכה אחרת. */
    const active = await p.evaluate(() => document.documentElement.getAttribute('data-theme'));
    if (active !== theme) throw new Error(`הערכה הפעילה היא ${active} ולא ${theme}`);
  };

  try {
    for (const width of WIDTHS) {
      for (const theme of ORDER) {
        const tag = `${theme} @${width}`;
        await openDoc(theme, width);

        // ── עמוד המסמך: רוחב הקריאה אחיד, ולא תלוי בתוכן העניינים ─────
        await setToc(true);
        const viewOpen = await measure(p);
        await setToc(false);
        const viewShut = await measure(p);
        await p.screenshot({ path: `${SHOTS}/layout-${theme}-${width}-view.png` });

        for (const [state, v] of [['פתוח', viewOpen], ['סגור', viewShut]]) {
          check(`${tag} מסמך, תוכן עניינים ${state}: אזור הקריאה ברוחב ${READ_W}`,
            near(v.innerW, READ_W), `${Math.round(v.innerW)}px`);
          check(`${tag} מסמך, תוכן עניינים ${state}: פסקה, רשימה וציטוט ממלאים אותו`,
            near(v.p, v.innerW) && near(v.ul, v.innerW) && near(v.quote, v.innerW),
            `פסקה ${v.p} · רשימה ${v.ul} · ציטוט ${v.quote} · אזור ${Math.round(v.innerW)}`);
          check(`${tag} מסמך, תוכן עניינים ${state}: אין גלילה לרוחב`, !v.hscroll);
        }
        check(`${tag} מסמך: קיפול תוכן העניינים לא משנה את רוחב העמוד`,
          near(viewOpen.mainW, viewShut.mainW),
          `${Math.round(viewOpen.mainW)} → ${Math.round(viewShut.mainW)}`);

        // ── מסך העריכה ──────────────────────────────────────────────────
        await p.getByRole('button', { name: 'עריכה', exact: true }).click();
        await p.waitForSelector('main textarea', { timeout: 10000 });
        await p.waitForTimeout(300);

        await setToc(true);
        const editOpen = await measure(p);
        await setToc(false);
        const editShut = await measure(p);
        await p.screenshot({ path: `${SHOTS}/layout-${theme}-${width}-edit.png` });

        check(`${tag} עורך, תוכן עניינים פתוח: ממלא עד תוכן העניינים`,
          near(editOpen.mainL, editOpen.tocR),
          `קצה העורך ${Math.round(editOpen.mainL)} · קצה תוכן העניינים ${Math.round(editOpen.tocR)}`);
        /* המרווח משני הצדדים: מעמודת המסמכים לעורך, ומהעורך לתוכן
           העניינים. נמדד על המסך המפוצל עצמו, לא על main. */
        const gapRail = editOpen.railL - editOpen.splitR;
        const gapToc = editOpen.splitL - editOpen.tocR;
        check(`${tag} עורך, תוכן עניינים פתוח: המרווח לתוכן העניינים זהה למרווח מעמודת המסמכים`,
          editOpen.splitL !== null && near(gapRail, gapToc) && gapToc > 0,
          `מעמודת המסמכים ${Math.round(gapRail)} · לתוכן העניינים ${Math.round(gapToc)}`);
        check(`${tag} עורך, תוכן עניינים סגור: מתרחב עד המקום שבו תוכן העניינים התחיל`,
          near(editShut.mainL, editOpen.tocL),
          `קצה העורך ${Math.round(editShut.mainL)} · תוכן העניינים התחיל ב-${Math.round(editOpen.tocL)}`);
        check(`${tag} עורך, תוכן עניינים סגור: המסך המפוצל ממלא את אזור התוכן`,
          editShut.splitL !== null && near(editShut.splitL, editShut.innerL) && near(editShut.splitR, editShut.innerR),
          `מפוצל ${Math.round(editShut.splitL)}–${Math.round(editShut.splitR)} · אזור ${Math.round(editShut.innerL)}–${Math.round(editShut.innerR)}`);
        check(`${tag} עורך: תיבת ההקלדה גדלה כשתוכן העניינים נסגר`,
          editShut.textarea > editOpen.textarea, `${editOpen.textarea} → ${editShut.textarea}`);
        check(`${tag} עורך: אין גלילה לרוחב`, !editOpen.hscroll && !editShut.hscroll);

        // ── מסך היצירה: אותו עורך, אותו כלל ────────────────────────────
        await p.reload({ waitUntil: 'load' });
        await p.waitForSelector('main article', { timeout: 20000 });
        await p.getByRole('button', { name: /new document|מסמך חדש/ }).first().click();
        await p.waitForSelector('main textarea', { timeout: 10000 });
        await p.waitForTimeout(300);
        const create = await measure(p);
        const createEdge = create.tocShown ? create.tocR : create.gridL;
        check(`${tag} מסך יצירה: ממלא את העמודה`,
          near(create.mainL, createEdge),
          `קצה ${Math.round(create.mainL)} · גבול ${Math.round(createEdge)}`);
      }
    }

    // ── מסך הפרויקטים: לא נמתח מעבר לרוחב הקריאה ───────────────────────
    /* במסך הזה אין סיידבר ואין תוכן עניינים, ולכן main נוחת בעמודה
       הראשונה של הרשת (auto) ורוחבו נקבע לפי התוכן — 600–700px, ולא
       READ_W. מה שנבדק הוא התקרה בלבד: הכלל של העורך לא דלף לכאן. */
    await p.setViewportSize({ width: WIDTHS[0], height: 950 });
    for (const theme of ORDER) {
      await p.goto(BASE + '/#/');
      await p.evaluate((t) => { localStorage.setItem('md-docs-theme', t); }, theme);
      await p.reload({ waitUntil: 'load' });
      await p.getByText(NAME).first().waitFor({ state: 'visible', timeout: 20000 });
      const pr = await measure(p);
      check(`${theme} מסך הפרויקטים: לא רחב מרוחב הקריאה, בלי גלילה לרוחב`,
        pr.innerW <= READ_W + 1 && !pr.hscroll, `${Math.round(pr.innerW)}px`);
    }

    // ── נייד: המגירה במקום הסיידבר, בלי גלילה לרוחב ──────────────────────
    await p.setViewportSize({ width: 390, height: 844 });
    await p.goto(docUrl);
    await p.reload({ waitUntil: 'load' });
    await p.waitForSelector('main article', { timeout: 20000 });
    const mob = await measure(p);
    await p.getByRole('button', { name: 'עריכה', exact: true }).click();
    await p.waitForSelector('main textarea', { timeout: 10000 });
    const mobEdit = await measure(p);
    check('נייד: אין גלילה לרוחב, לא במסמך ולא בעורך', !mob.hscroll && !mobEdit.hscroll);

    check('אין שגיאות קונסולה', errs.length === 0, errs.slice(0, 3).join(' | '));
  } finally {
    /* רץ גם כשבדיקה זרקה, כדי שהרצה כושלת לא תשאיר פרויקט במסד. */
    try {
      removed = await p.evaluate(async (s) => {
        const r = await fetch('/api/projects/' + encodeURIComponent(s),
          { method: 'DELETE', credentials: 'same-origin' });
        return r.status;
      }, slug);
    } catch (e) {
      removed = 'שגיאה: ' + e.message;
    }
    await browser.close();
  }

  check('הפרויקט נמחק בסיום', removed === 204, 'status ' + removed);

  const failed = results.filter((r) => !r.ok);
  console.log(failed.length ? `\nנכשלו ${failed.length} מתוך ${results.length}` : `\nהכול עבר (${results.length})`);
  process.exit(failed.length ? 1 : 0);
})().catch((e) => { console.error('FATAL', e.message); process.exit(1); });
