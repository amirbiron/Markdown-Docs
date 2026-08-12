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
  const DESC = 'תיאור מהטופס ' + process.pid;
  await p.fill('input[placeholder="שם הפרויקט החדש"]', NAME);
  await p.fill('input[placeholder="תיאור קצר (לא חובה)"]', DESC);
  await p.getByRole('button', { name: 'יצירת פרויקט' }).click();
  await p.waitForTimeout(2500);
  const inProject = await p.evaluate(() => document.body.innerText);
  check('הפרויקט נוצר ונפתח', inProject.includes(NAME), NAME);

  // התיאור נבדק מול השרת ולא מול המסך: כרטיס שמציג את מה שהוקלד
  // ייראה תקין גם אם השדה מעולם לא נשלח ב-POST.
  const storedDesc = await p.evaluate(async (name) => {
    const list = await (await fetch('/api/projects', { credentials: 'same-origin' })).json();
    const mine = list.find((x) => x.name === name);
    return mine ? mine.description : null;
  }, NAME);
  check('התיאור נשמר בשרת', storedDesc === DESC, JSON.stringify(storedDesc));
  await p.screenshot({ path: SHOTS + '/ui-project.png' });

  // מסמך דרך מסך הכתיבה — המסך המפוצל שנפתח מכפתור הסרגל
  await p.getByRole('button', { name: /new document|כתיבת מסמך חדש/i }).click();
  await p.waitForSelector('input[placeholder^="שם המסמך"]', { timeout: 15000 });
  // הטבלה כאן רחבה בכוונה: היא מה שמוודא שטבלה שאינה נכנסת ברוחב
  // גוללת בתוך מעטפת במקום להיחתך.
  await p.fill('textarea', [
    // הפסקה הראשונה מקודמת לכותרת העמוד ולכן מרונדרת במסלול נפרד מהגוף.
    // הקישור וההדגשה כאן הם מה שמבדיל בין המסלולים.
    '# מדריך התקנה עם [קישור](https://example.org/מדריך)', '',
    'פסקת פתיחה עם [קישור בפתיח](https://example.org/פתיח) ו-**מודגש**.',
    'שורה שנייה של הפתיח.', '',
    '## שלב ראשון', '', '- פריט', '- פריט נוסף', '',
    // אחד עברי ואחד אנגלי: כיוון הקריאה של code span נגזר מתוכנו
    'הודעה `תוכן זה אינו זמין` ומזהה `pages_read_engagement`.', '',
    // כותרת עם תווים מילוליים: תוכן העניינים חייב להציג אותה כלשונה,
    // ולא למחוק ממנה את הכוכבית ואת שווה כאילו היו סימון
    '## החישוב 5 * 3 = 15', '', 'טקסט.', '',
    '## טבלה רחבה', '',
    '| מזהה | שם הפרויקט | בעלים | נראות | מסמכים | קישורים | נוצר בתאריך | עודכן לאחרונה |',
    '| --- | --- | --- | --- | --- | --- | --- | --- |',
    '| a1b2c3 | תיעוד המוצר הראשי | admin@example.com | פומבי | 42 | 7 | 2024-01-15 | 2026-08-04 |',
    '',
    '## שורות',
    '',
    'שורה ראשונה',
    'שורה שנייה',
    'שורה שלישית',
    '',
    '**מודגש שנפרס',
    'על שתי שורות**',
    '',
    '*מוטה שנפרס',
    'על שתי שורות*',
    '',
    'בחישוב 5 * 3 ועוד * 2 אין שום הדגשה.',
    '',
    '> ציטוט ראשון',
    '> ציטוט שני',
    '',
    '#### כותרת H4',
    '',
    '##### כותרת H5',
    '',
    '###### כותרת H6',
    '',
    '## הגדרות',
    '',
    'בלוק',
    ': יחידת התוכן הקטנה ביותר.',
    '',
    'מונח עם שתיים',
    ': ההגדרה הראשונה.',
    ': ההגדרה השנייה.',
    '',
    '::: note',
    'ההתראה חייבת לשרוד את התחביר החדש',
    ':::',
    '',
    ': שורה יתומה בלי מונח לפניה',
    '',
  ].join('\n'));
  await p.getByRole('button', { name: 'הוספה לפרויקט' }).click();
  await p.waitForTimeout(2500);
  const docText = await p.evaluate(() => document.body.innerText);
  check('המסמך נוצר ומרונדר', docText.includes('מדריך התקנה') && docText.includes('שלב ראשון'));

  // נמדד ברוחב צר, כי שם טבלה של שמונה עמודות באמת לא נכנסת. הבדיקה
  // היא שהמעטפת גוללת ולא שהטבלה צרה — overflow:hidden על המעטפת חתך
  // את העמודות האחרונות בלי שום סימן שהן קיימות.
  //
  // overflowX נבדק במפורש ולא רק scrollWidth > clientWidth: תיבה עם
  // overflow:hidden עדיין מדווחת scrollWidth גדול יותר, וגם עדיין ניתן
  // לגלול אותה מקוד. מה שהיא לא מאפשרת הוא גלילה של המשתמש — גלגלת,
  // מגע או Shift+גלילה — וזו בדיוק ההצהרה שקובעת אותה.
  await p.setViewportSize({ width: 430, height: 900 });
  await p.waitForTimeout(800);
  const wide = await p.evaluate(() => {
    const t = [...document.querySelectorAll('table')].sort((a, b) => b.scrollWidth - a.scrollWidth)[0];
    if (!t) return null;
    const box = t.parentElement;
    return {
      overflowX: getComputedStyle(box).overflowX,
      tableW: Math.round(t.scrollWidth),
      boxW: Math.round(box.clientWidth),
      scrolls: box.scrollWidth > box.clientWidth + 1,
      pageOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1,
    };
  });
  check('טבלה רחבה גוללת ואינה נחתכת',
    !!wide && wide.overflowX === 'auto' && wide.scrolls
      && wide.tableW > wide.boxW && !wide.pageOverflow,
    JSON.stringify(wide));
  await p.screenshot({ path: SHOTS + '/ui-table-narrow.png' });
  await p.setViewportSize({ width: 1500, height: 1000 });
  await p.waitForTimeout(600);

  // ── שבירת שורות בתוך פסקה ─────────────────────────────────────────
  // נמדד במספר השורות הוויזואליות (getClientRects) ולא בספירת <br>.
  // <br> קיים שנבלע ב-CSS נראה בדיוק כמו <br> חסר בעין של הקורא,
  // וספירת אלמנטים הייתה עוברת עליו. הרוחב כאן 1500 והשורות קצרות,
  // ולכן כל שבירה שנמדדת היא שבירה שנכתבה ולא גלישה.
  const lineBreaks = await p.evaluate(() => {
    const find = (tag, txt) => [...document.querySelectorAll(tag)]
      .find((el) => (el.textContent || '').includes(txt));
    /* Range ולא el.getClientRects: על אלמנט בלוק המתודה מחזירה תמיד
       מלבן אחד — תיבת הבלוק — ולא שורה לשורה. Range על התוכן מחזיר
       מלבן לכל תיבת שורה, וזה מה שהעין באמת רואה.

       המלבנים מקובצים בסובלנות ולא לפי top מעוגל. באותה שורה ויזואלית
       יושבים כמה מלבנים — צומת טקסט, <strong>, <code> — ואין להם אותו
       top: תיבת inline עם גודל גופן או ריפוד אחרים מתחילה כמה פיקסלים
       מעל או מתחת. עיגול לשלם היה סופר אותם כשורות נפרדות. הסף 8px
       יושב בבטחה בין הפער הזה לבין מרווח שורה אמיתי, שהוא כאן כ-30px. */
    const rects = (el) => {
      if (!el) return 0;
      const r = document.createRange();
      r.selectNodeContents(el);
      const tops = [...r.getClientRects()].map((b) => b.top).sort((a, b) => a - b);
      if (!tops.length) return 0;
      return tops.reduce((n, t, i) => (i && t - tops[i - 1] > 8 ? n + 1 : n), 1);
    };
    const three = find('p', 'שורה ראשונה');
    const bold = find('p', 'מודגש שנפרס');
    const em = find('p', 'מוטה שנפרס');
    const plain = find('p', '5 * 3');
    const quote = find('blockquote', 'ציטוט ראשון');
    return {
      three: rects(three),
      // שורה ריקה חייבת להישאר גבול של פסקה ולא להתמזג לשבירה בלבד
      separate: !!three && !(three.textContent || '').includes('מודגש'),
      bold: rects(bold),
      italic: rects(em),
      /* ההגבלה ששמרה על זה קודם הייתה ה-\n עצמו: כל עוד הפסקה חוברה
         ברווחים, נטוי לא יכול היה לחצות שורה ממילא. עכשיו שהוא יכול,
         הכוכביות הספרותיות הן מה שנשאר בסיכון. */
      literal: !!plain && !plain.querySelector('em')
        && (plain.textContent || '').includes('5 * 3'),
      // ההדגשה נפרסת על שתי שורות; אם הפסקה פוצלה לפני הפרסינג היא
      // מתפרקת לשני חצאים עם ** גלויות ובלי <strong> כלל
      strong: !!bold && !!bold.querySelector('strong')
        && !(bold.textContent || '').includes('**'),
      // אותו דבר בכוכבית יחידה. זה מה שנשבר כשה-\n נשמר לראשונה:
      // החלופה של כוכבית יחידה אסרה \n במפורש
      emTag: !!em && !!em.querySelector('em')
        && !(em.textContent || '').includes('*'),
      quote: rects(quote),
    };
  });
  // ── כיוון הכתיבה בשם הקובץ ובקוד השורתי ───────────────────────────
  // dir="ltr" קבוע הפך את סדר הקריאה של שם עברי: "…-app.md" נראה מתחיל
  // מ-app.md. הבדיקה מודדת את הסדר הוויזואלי בפועל, ולא את הערך של
  // התכונה — כי מה שנשבר הוא מה שהעין רואה.
  //
  // שני הכיוונים נמדדים. השני הוא החשוב: הוא מה שייפול אם מישהו
  // "יתקן" בעתיד ל-dir="rtl" ויהפוך את שמות האנגלית.
  const bidi = await p.evaluate(() => {
    /* צמתי טקסט ולא firstChild: React מפצל טקסט לכמה צמתים, וסטייה
       מעבר לצומת הראשון זורקת IndexSizeError. */
    const startsRight = (el) => {
      if (!el) return null;
      const nodes = [];
      const w = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
      for (let n = w.nextNode(); n; n = w.nextNode()) if (n.textContent.length) nodes.push(n);
      if (!nodes.length) return null;
      const rect = (node, i) => {
        const r = document.createRange();
        r.setStart(node, i); r.setEnd(node, i + 1);
        return r.getBoundingClientRect();
      };
      const last = nodes[nodes.length - 1];
      return rect(nodes[0], 0).left > rect(last, last.textContent.length - 1).left;
    };
    const badge = [...document.querySelectorAll('[data-doc] header span')]
      .find((s) => /\.md$/.test(s.textContent.trim()));
    const codes = [...document.querySelectorAll('[data-doc] code')];
    const heb = codes.find((c) => /^[֐-׿]/.test(c.textContent.trim()));
    const eng = codes.find((c) => /^[A-Za-z]/.test(c.textContent.trim()));
    return {
      badgeText: badge ? badge.textContent.trim() : null,
      badgeStartsRight: startsRight(badge),
      hebCode: startsRight(heb),
      engCode: startsRight(eng),
    };
  });
  check('שם קובץ וקוד שורתי נקראים לפי תוכנם',
    bidi.badgeStartsRight === true && bidi.hebCode === true && bidi.engCode === false,
    JSON.stringify(bidi));

  // ── הכותרת והפתיח ─────────────────────────────────────────────────
  // שניהם נלקחים מבלוקים של המסמך ומוזרקים לכותרת העמוד, ולכן הם עברו
  // במסלול נפרד מהגוף. התוצאה הייתה שאותה פסקה נראתה תקינה בפריוויו
  // של העורך וגולמית במסמך. נמדד <a href> אמיתי ולא טקסט שנראה כמוהו.
  const promoted = await p.evaluate(() => {
    const header = document.querySelector('[data-doc] header');
    if (!header) return null;
    const h1 = header.querySelector('h1'), lead = header.querySelector('p');
    /* השבירה בפתיח נמדדת בשורות ויזואליות: היא עבדה קודם דרך
       white-space:pre-line, שהוסר יחד עם המחרוזת הגולמית. */
    const lines = (el) => {
      const r = document.createRange();
      r.selectNodeContents(el);
      const tops = [...r.getClientRects()].map((b) => b.top).sort((a, b) => a - b);
      return tops.length ? tops.reduce((n, t, i) => (i && t - tops[i - 1] > 8 ? n + 1 : n), 1) : 0;
    };
    return {
      h1Link: !!(h1 && h1.querySelector('a[href^="https://"]')),
      leadLink: !!(lead && lead.querySelector('a[href^="https://"]')),
      leadStrong: !!(lead && lead.querySelector('strong')),
      // ")(" של קישור לא נמחק על ידי ה-replace שהיה כאן, ולכן הוא הסימן
      raw: /\]\(http/.test(header.textContent),
      leadLines: lead ? lines(lead) : 0,
      /* פריט בתוכן העניינים יושב בתוך <a> וחייב להישאר טקסט נקי.

         מזוהה דרך [data-toc] ולא לפי href שמתחיל ב-"#h-": קישור במסמך
         שמצביע לעוגן של כותרת הוא קישור לגיטימי לגמרי, והבורר הרחב היה
         מפיל את הבדיקה עליו בזמן שהתוויות עצמן נקיות. */
      tocHasUrl: [...document.querySelectorAll('[data-toc] a')]
        .some((a) => /\]\(|https?:\/\//.test(a.textContent)),
      /* והצד השני של אותו מטבע: הסרת סימון שמוחקת גם תווים מילוליים.
         "5 * 3 = 15" הוצג כ-"5  3  15", כלומר הטקסט אבד ולא הסימון. */
      tocKeepsLiterals: [...document.querySelectorAll('[data-toc] a')]
        .some((a) => a.textContent.trim() === 'החישוב 5 * 3 = 15'),
    };
  });
  check('הכותרת והפתיח מרונדרים ולא גולמיים',
    !!promoted && promoted.h1Link && promoted.leadLink && promoted.leadStrong
      && !promoted.raw && promoted.leadLines === 2
      && !promoted.tocHasUrl && promoted.tocKeepsLiterals,
    JSON.stringify(promoted));

  // ── רשימת הגדרות ──────────────────────────────────────────────────
  // התחביר החדש מתחיל בנקודתיים, בדיוק כמו ההתראות. לכן נמדד כאן לא רק
  // שהוא עובד אלא גם שהוא לא בלע את מה שהיה קודם: ההתראה חייבת לשרוד,
  // ושורת ":" בלי מונח לפניה חייבת להישאר טקסט ולא להפוך להגדרה.
  const defs = await p.evaluate(() => {
    const dls = [...document.querySelectorAll('[data-doc] dl')];
    const pairs = dls.flatMap((dl) => [...dl.querySelectorAll('dt')].map((dt) => ({
      term: dt.textContent.trim(),
      defs: [...dt.parentElement.querySelectorAll('dd')].map((d) => d.textContent.trim()),
    })));
    return {
      // שורה ריקה בין זוגות ממשיכה את אותה רשימה ולא פותחת חדשה
      lists: dls.length,
      pairs: pairs.length,
      first: pairs[0] || null,
      multi: (pairs.find((x) => x.term === 'מונח עם שתיים') || {}).defs || [],
      callout: document.body.innerText.includes('ההתראה חייבת לשרוד'),
      orphanIsText: [...document.querySelectorAll('[data-doc] p')]
        .some((e) => e.textContent.includes(': שורה יתומה'))
        && ![...document.querySelectorAll('[data-doc] dd')]
          .some((e) => e.textContent.includes('יתומה')),
    };
  });
  check('רשימת הגדרות נבנית מהתחביר',
    defs.lists === 1 && defs.pairs === 2
      && !!defs.first && defs.first.term === 'בלוק' && defs.first.defs.length === 1
      && defs.multi.length === 2
      && defs.callout && defs.orphanIsText,
    JSON.stringify(defs));

  // ── שלוש הרמות העמוקות ────────────────────────────────────────────
  // הן חלקו ענף else אחד, כלומר אותו תג ואותו גודל בדיוק. נמדד מה
  // שנשבר: תג נפרד לכל רמה, וגודל שיורד ממש בין רמה לרמה.
  const deep = await p.evaluate(() => {
    const of = (tag) => {
      const el = [...document.querySelectorAll('[data-doc] ' + tag)]
        .find((e) => (e.textContent || '').includes('כותרת ' + tag.toUpperCase()));
      if (!el) return null;
      const cs = getComputedStyle(el);
      return { size: parseFloat(cs.fontSize), weight: Number(cs.fontWeight), color: cs.color };
    };
    return { h4: of('h4'), h5: of('h5'), h6: of('h6') };
  });
  check('H4, H5 ו-H6 נבדלות זו מזו',
    !!deep.h4 && !!deep.h5 && !!deep.h6
      && deep.h4.size > deep.h5.size && deep.h5.size > deep.h6.size
      && new Set([deep.h4.color, deep.h6.color]).size === 2,
    JSON.stringify(deep));

  check('שורות בפסקה נשברות כפי שנכתבו',
    lineBreaks.three === 3 && lineBreaks.separate
      && lineBreaks.bold === 2 && lineBreaks.strong
      && lineBreaks.italic === 2 && lineBreaks.emTag && lineBreaks.literal
      && lineBreaks.quote === 2,
    JSON.stringify(lineBreaks));

  // מסמך דרך העלאת קובץ. מסלול נפרד לגמרי מההדבקה — הוא עובר דרך
  // readFiles, ואיפוס value של ה-input מרוקן את אותו FileList שמחזיקים.
  // בלי הבדיקה הזאת הרגרסיה הזו שקטה לחלוטין.
  const upload = SHOTS + '/upload-check.md';
  require('fs').writeFileSync(upload, '# מסמך מקובץ\n\nתוכן שהועלה.\n');
  await p.setInputFiles('input[type="file"]', [upload]);
  await p.waitForTimeout(3000);
  const uploaded = await p.evaluate(() => document.body.innerText);
  check('העלאת קובץ יוצרת מסמך', uploaded.includes('מסמך מקובץ') && uploaded.includes('תוכן שהועלה'));

  // ── מסך מלא ───────────────────────────────────────────────────────
  // שלושת המסלולים, כי כל אחד נשבר אחרת. המסלול השלישי — יציאה שלא דרך
  // הכפתור — הוא זה שקל לפספס: בלי האזנה ל-fullscreenchange, ה-state
  // ממשיך להצהיר "במסך מלא" אחרי שהמשתמש כבר יצא ב-Esc.
  await p.locator('button[title="מסך מלא"]').click();
  await p.waitForTimeout(700);
  const fsOn = await p.evaluate(() => {
    const el = document.querySelector('[data-doc]');
    return {
      isFsElement: document.fullscreenElement === el,
      bg: getComputedStyle(el).backgroundColor,
      exitTitle: !!document.querySelector('button[title="יציאה ממסך מלא"]'),
    };
  });
  check('מסך מלא נפתח על מכל המסמך',
    fsOn.isFsElement && fsOn.exitTitle && fsOn.bg !== 'rgba(0, 0, 0, 0)',
    JSON.stringify(fsOn));
  await p.screenshot({ path: SHOTS + '/ui-fullscreen.png' });

  // יציאה שלא דרך הכפתור. ב-headless מקש Escape אינו מפעיל את יציאת
  // הדפדפן ממסך מלא — זו התנהגות של כרום הדפדפן ולא אירוע DOM — ולכן
  // נבדק כאן אותו אות בדיוק שהיא מייצרת.
  await p.evaluate(() => document.exitFullscreen());
  await p.waitForTimeout(700);
  const fsOff = await p.evaluate(() => ({
    fs: !!document.fullscreenElement,
    style: document.querySelector('[data-doc]').getAttribute('style') || '',
    backToTitle: !!document.querySelector('button[title="מסך מלא"]'),
  }));
  check('יציאה שלא דרך הכפתור מסונכרנת חזרה',
    !fsOff.fs && fsOff.style === '' && fsOff.backToTitle, JSON.stringify(fsOff));

  // ה-fallback. Safari ב-iOS אינו תומך ב-requestFullscreen על אלמנט,
  // ובלי המסלול הזה הכפתור שם נלחץ ולא קורה כלום.
  await p.evaluate(() => {
    Element.prototype.requestFullscreen = undefined;
    Element.prototype.webkitRequestFullscreen = undefined;
  });
  await p.locator('button[title="מסך מלא"]').click();
  await p.waitForTimeout(700);
  const fb = await p.evaluate(() => {
    const el = document.querySelector('[data-doc]');
    const r = el.getBoundingClientRect();
    return {
      position: getComputedStyle(el).position,
      covers: Math.round(r.width) === window.innerWidth && Math.round(r.height) === window.innerHeight,
      exitTitle: !!document.querySelector('button[title="יציאה ממסך מלא"]'),
    };
  });
  check('בלי Fullscreen API נכנס מצב מיקוד',
    fb.position === 'fixed' && fb.covers && fb.exitTitle, JSON.stringify(fb));

  await p.keyboard.press('Escape');
  await p.waitForTimeout(500);
  const fbOff = await p.evaluate(() => ({
    style: document.querySelector('[data-doc]').getAttribute('style') || '',
    backToTitle: !!document.querySelector('button[title="מסך מלא"]'),
  }));
  check('Escape יוצא ממצב המיקוד', fbOff.style === '' && fbOff.backToTitle, JSON.stringify(fbOff));

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
