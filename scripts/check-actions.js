/* שלושת הכפתורים החדשים בעורך: שמירה, ביטול/חזרה, והעתקה.
 *
 *   node scripts/check-actions.js http://127.0.0.1:8070
 *
 * הביטול נבדק גם על מסלול התבניות ולא רק על הקלדה. ה-undo של הדפדפן
 * נשבר בדיוק שם — applySnippet כותב value חדש — ולכן זה המסלול שבגללו
 * המחסנית קיימת.
 *
 * המדד החשוב ביותר הוא שהיסטוריה אינה עוברת בין מסמכים: ביטול שמזריק
 * תוכן של מסמך אחר נשמר לשרת מיד אחר כך, ואז האובדן קבוע.
 *
 * ההעתקה נמדדת מול הלוח דרך הרשאת clipboard-read, ולא מול תווית
 * הכפתור: תווית שמשתנה מוכיחה שהכפתור נלחץ, לא שמשהו הועתק.
 */

let chromium;
try {
  ({ chromium } = require('playwright'));
} catch {
  console.error('playwright לא מותקן. הריצו: npm install');
  process.exit(1);
}

const BASE = (process.argv[2] || '').replace(/\/+$/, '');
if (!BASE) {
  console.error('שימוש: node scripts/check-actions.js <כתובת-בסיס>');
  process.exit(1);
}

const EMAIL = process.env.ADMIN_EMAIL || 'admin@example.com';
const PASSWORD = process.env.ADMIN_PASSWORD || 'correct-horse-battery';
const NEW_DOC = /כתיבת מסמך חדש|new document|מסמך חדש/i;

const R = [];
const ok = (l, v, x) => { R.push(v); console.log(`  ${v ? '✓' : '✗'}  ${l}${x ? '  — ' + x : ''}`); };

(async () => {
  const br = await chromium.launch();
  const ctx = await br.newContext({
    viewport: { width: 1500, height: 1000 },
    permissions: ['clipboard-read', 'clipboard-write'],
  });
  const p = await ctx.newPage();
  const created = [];
  const errs = [];
  const clip = () => p.evaluate(() => navigator.clipboard.readText());

  try {
    /* יציאה ממסך היצירה עם טיוטה פתוחה שואלת ב-window.confirm.
       Playwright דוחה דיאלוגים כברירת מחדל, ואז הביטול פשוט לא קורה. */
    p.on('dialog', (d) => d.accept());
    p.on('pageerror', (e) => errs.push('PAGEERROR: ' + e.message));
    p.on('console', (m) => { if (m.type() === 'error') errs.push(m.text()); });

    await p.goto(BASE + '/', { waitUntil: 'load' });
    await p.waitForSelector('#dc-root');
    await p.waitForTimeout(2400);
    await p.getByRole('button', { name: 'כניסה' }).click();
    await p.waitForTimeout(600);
    await p.fill('input[type="email"]', EMAIL);
    await p.fill('input[type="password"]', PASSWORD);
    await p.getByRole('button', { name: 'כניסה', exact: true }).last().click();
    await p.waitForTimeout(2400);

    /* השגיאה מוחזרת ולא נזרקת. throw אחרי שהפרויקט כבר נוצר היה
       מדלג על created.push, וה-finally לא היה יודע מה למחוק — כלומר
       הרצה שנכשלה בהכנה משאירה פרויקט במסד. */
    const setup = await p.evaluate(async ([stamp]) => {
      const post = (u, b) => fetch(u, {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b),
      });
      const created = await post('/api/projects', { name: 'פעולות ' + stamp });
      if (!created.ok) return { error: 'יצירת הפרויקט החזירה ' + created.status };
      const slug = (await created.json()).slug;
      if (!slug) return { error: 'יצירת הפרויקט לא החזירה slug' };
      for (const doc of [
        { title: 'ראשון', content: '# ראשון\n\nתוכן של הראשון.\n' },
        { title: 'שני', content: '# שני\n\nתוכן של השני.\n' },
      ]) {
        const r = await post(`/api/projects/${encodeURIComponent(slug)}/docs`, doc);
        if (!r.ok) return { slug, error: 'יצירת ' + doc.title + ' החזירה ' + r.status };
      }
      return { slug };
    }, [process.pid]);
    if (setup.slug) created.push(setup.slug);
    if (setup.error) throw new Error('ההכנה נכשלה: ' + setup.error);

    await p.goto(BASE + '/#/' + encodeURIComponent(setup.slug), { waitUntil: 'load' });
    await p.waitForSelector('#dc-root');
    await p.waitForTimeout(3000);

    // ── מסך היצירה: העתקה עובדת גם כשאין מסמך בשרת ───────────────────
    await p.getByRole('button', { name: NEW_DOC }).first().click();
    await p.waitForTimeout(1200);
    await p.fill('textarea', '# טיוטה\n\nתוכן שלא נשמר בשום מקום.\n');
    await p.waitForTimeout(900);

    await p.getByRole('button', { name: 'העתקה' }).first().click();
    await p.waitForTimeout(900);
    const draftCopy = await clip();
    ok('העתקה עובדת במסך היצירה',
      draftCopy.indexOf('תוכן שלא נשמר בשום מקום') >= 0, JSON.stringify(draftCopy.slice(0, 30)));

    // ── ביטול על הקלדה ───────────────────────────────────────────────
    const redoAtStart = await p.evaluate(() => {
      const b = [...document.querySelectorAll('button')]
        .find((x) => x.getAttribute('aria-label') === 'ביצוע מחדש');
      return b ? b.disabled : null;
    });
    ok('כפתור החזרה מושבת כשלא בוטל דבר', redoAtStart === true);

    await p.fill('textarea', '# טיוטה\n\nתוכן שלא נשמר בשום מקום.\nשורה שנוספה בטעות.\n');
    await p.waitForTimeout(1200);
    await p.getByRole('button', { name: 'ביטול הפעולה האחרונה' }).click();
    await p.waitForTimeout(900);
    const undone = await p.evaluate(() => document.querySelector('textarea').value);
    ok('ביטול מחזיר את הטקסט הקודם',
      undone.indexOf('שורה שנוספה בטעות') < 0 && undone.indexOf('תוכן שלא נשמר') >= 0,
      JSON.stringify(undone.slice(-30)));

    /* מצבי הכפתורים הם חוזה בפני עצמו: כפתור חזרה שפעיל לפני שבוטל
       משהו, או שנשאר פעיל אחרי שנוצל, נראה תקין בבדיקה שמסתכלת רק על
       הטקסט שהתקבל. */
    const btnState = () => p.evaluate(() => {
      const at = (label) => [...document.querySelectorAll('button')]
        .find((b) => b.getAttribute('aria-label') === label);
      const u = at('ביטול הפעולה האחרונה'), r = at('ביצוע מחדש');
      return { undo: u ? u.disabled : null, redo: r ? r.disabled : null };
    });
    const afterUndoState = await btnState();
    ok('אחרי ביטול, החזרה נפתחת', afterUndoState.redo === false, JSON.stringify(afterUndoState));

    await p.getByRole('button', { name: 'ביצוע מחדש' }).click();
    await p.waitForTimeout(900);
    const redone = await p.evaluate(() => document.querySelector('textarea').value);
    ok('חזרה מחזירה את מה שבוטל', redone.indexOf('שורה שנוספה בטעות') >= 0);
    const afterRedoState = await btnState();
    ok('ואחרי שנוצלה היא נסגרת', afterRedoState.redo === true, JSON.stringify(afterRedoState));

    // ── ביטול על תבנית מהסרגל ────────────────────────────────────────
    // זה המסלול שבגללו המחסנית קיימת: applySnippet כותב value חדש,
    // וה-undo של הדפדפן מת ברגע הזה.
    const before = await p.evaluate(() => document.querySelector('textarea').value);
    await p.locator('button[title="מודגש"]').click();
    await p.waitForTimeout(700);
    const withSnippet = await p.evaluate(() => document.querySelector('textarea').value);
    ok('התבנית נכנסה', withSnippet !== before && withSnippet.indexOf('**') >= 0);
    await p.getByRole('button', { name: 'ביטול הפעולה האחרונה' }).click();
    await p.waitForTimeout(900);
    const afterUndo = await p.evaluate(() => document.querySelector('textarea').value);
    ok('ביטול מסיר תבנית שלמה בבת אחת', afterUndo === before,
      JSON.stringify(afterUndo.slice(-24)));

    // ── Ctrl+Z ───────────────────────────────────────────────────────
    await p.locator('button[title="מודגש"]').click();
    await p.waitForTimeout(700);
    await p.locator('textarea').press('Control+z');
    await p.waitForTimeout(900);
    const afterKey = await p.evaluate(() => document.querySelector('textarea').value);
    ok('Ctrl+Z עושה את אותו דבר', afterKey === before, JSON.stringify(afterKey.slice(-24)));

    await p.getByRole('button', { name: 'ביטול', exact: true }).click();
    await p.waitForTimeout(1500);

    // ── מסמך קיים: כפתור השמירה ──────────────────────────────────────
    await p.locator('a[href="#top"]').filter({ hasText: 'ראשון' }).first().click();
    await p.waitForTimeout(1800);
    await p.locator('button[title="עריכה"]').last().click();
    await p.waitForTimeout(1400);

    const idle = await p.evaluate(() => {
      const b = [...document.querySelectorAll('button')].find((x) => x.textContent.trim() === 'שמירה');
      return b ? b.disabled : null;
    });
    ok('כפתור השמירה מושבת כשאין מה לשמור', idle === true);

    await p.fill('textarea', '# ראשון\n\nנכתב ונשמר בכפתור.\n');
    await p.waitForTimeout(300);
    const armed = await p.evaluate(() => {
      const b = [...document.querySelectorAll('button')].find((x) => x.textContent.trim() === 'שמירה');
      return b ? b.disabled : null;
    });
    ok('ומשתחרר ברגע שיש', armed === false);

    /* ההוכחה שהכפתור שמר ולא ה-debounce נמדדת בזמן ולא בהמתנה: הבקשה
       חייבת לצאת הרבה לפני SAVE_MS=2500 שמתחיל מרגע ההקלדה. גרסה
       קודמת המתינה 800+1500 מילישניות לפני הלחיצה, כלומר השאירה
       למרווח הזה כ-200 מילישניות — ריצה איטית אחת והטיימר היה מקדים
       את הכפתור, והמדד היה מאשר את הדבר הלא נכון. */
    const typedAt = Date.now();
    const savePut = p.waitForRequest(
      (r) => r.method() === 'PUT' && r.url().indexOf('/docs/') >= 0, { timeout: 4000 });
    await p.locator('button[title="שמירת המסמך"]').click();
    const req = await savePut;
    const elapsed = Date.now() - typedAt;
    ok('הבקשה יוצאת מהכפתור ולא מהטיימר', elapsed < 1800, elapsed + 'ms מאז ההקלדה');

    /* מדידה חוזרת עד שהתוכן בשרת, ולא קריאה בודדת אחרי המתנה קבועה:
       ריצה איטית הייתה מדווחת כישלון על שמירה שרק אחרה. */
    let stored = '';
    for (let i = 0; i < 20; i++) {
      stored = await p.evaluate(async (s) => {
        const r = await fetch('/api/projects/' + encodeURIComponent(s) + '/docs/ראשון',
          { credentials: 'same-origin' });
        return r.ok ? (await r.json()).content : 'status ' + r.status;
      }, setup.slug);
      if (stored.indexOf('נכתב ונשמר בכפתור') >= 0) break;
      await p.waitForTimeout(300);
    }
    ok('הכפתור שומר לשרת', stored.indexOf('נכתב ונשמר בכפתור') >= 0,
      JSON.stringify(stored.slice(0, 34)) + ' · ' + req.method());

    // ── ההעתקה בעריכה לוקחת את מה שעל המסך ───────────────────────────
    await p.fill('textarea', '# ראשון\n\nשינוי שטרם נשמר.\n');
    await p.waitForTimeout(700);
    await p.getByRole('button', { name: 'העתקה' }).first().click();
    await p.waitForTimeout(900);
    const editCopy = await clip();
    ok('העתקה בעריכה מחזירה את מה שעל המסך ולא את הגרסה בשרת',
      editCopy.indexOf('שינוי שטרם נשמר') >= 0, JSON.stringify(editCopy.slice(0, 30)));

    // ── ההיסטוריה אינה עוברת בין מסמכים ──────────────────────────────
    // הכשל כאן אינו ויזואלי: ביטול היה מזריק את תוכן המסמך הקודם,
    // והשמירה האוטומטית הייתה שולחת אותו לשרת דקות ספורות אחר כך.
    await p.locator('button[title="שמירת המסמך"]').click();
    await p.waitForTimeout(1500);
    await p.locator('a[href="#top"]').filter({ hasText: 'שני' }).first().click();
    await p.waitForTimeout(2200);

    const crossDoc = await p.evaluate(() => {
      const b = [...document.querySelectorAll('button')]
        .find((x) => x.getAttribute('aria-label') === 'ביטול הפעולה האחרונה');
      return { disabled: b ? b.disabled : null, text: document.querySelector('textarea').value };
    });
    ok('אחרי מעבר מסמך אין מה לבטל', crossDoc.disabled === true, JSON.stringify(crossDoc.disabled));
    ok('והתיבה מציגה את המסמך הנכון',
      crossDoc.text.indexOf('תוכן של השני') >= 0, JSON.stringify(crossDoc.text.slice(0, 24)));

    // גם לחיצה ישירה, למקרה שהכפתור מושבת רק למראית עין
    await p.evaluate(() => {
      const b = [...document.querySelectorAll('button')]
        .find((x) => x.getAttribute('aria-label') === 'ביטול הפעולה האחרונה');
      if (b) b.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
    });
    await p.waitForTimeout(1200);
    const stillSecond = await p.evaluate(() => document.querySelector('textarea').value);
    ok('לחיצה כפויה על ביטול אינה מזריקה תוכן ממסמך אחר',
      stillSecond.indexOf('תוכן של השני') >= 0 && stillSecond.indexOf('נשמר בכפתור') < 0,
      JSON.stringify(stillSecond.slice(0, 24)));

    // ── בידוד: אותו מסמך, מפגש עריכה חדש ─────────────────────────────
    // יציאה מהעורך וכניסה מחדש משאירות גם את היעד וגם את התוכן זהים,
    // ולכן שתי הבדיקות שב-captureUndo לא רואות דבר. בלי זיהוי המפגש,
    // ביטול כאן היה מחזיר snapshot מלפני עריכות ששמורות כבר בשרת.
    await p.fill('textarea', '# שני\n\nעריכה במפגש הראשון.\n');
    await p.waitForTimeout(1200);
    await p.locator('button[title="שמירת המסמך"]').click();
    await p.waitForTimeout(1500);
    await p.locator('button[title="שמירה"]').last().click();   // סגירת העורך
    await p.waitForTimeout(1500);
    await p.locator('button[title="עריכה"]').last().click();
    await p.waitForTimeout(1500);
    const reopened = await p.evaluate(() => {
      const b = [...document.querySelectorAll('button')]
        .find((x) => x.getAttribute('aria-label') === 'ביטול הפעולה האחרונה');
      return b ? b.disabled : null;
    });
    ok('פתיחה מחדש של אותו מסמך מתחילה היסטוריה נקייה', reopened === true);

    // ── בידוד: שתי יצירות רצופות באותו פרויקט ────────────────────────
    await p.getByRole('button', { name: NEW_DOC }).first().click();
    await p.waitForTimeout(1200);
    await p.fill('textarea', '# ראשונה\n\nטיוטה ראשונה.\n');
    await p.waitForTimeout(1200);
    await p.getByRole('button', { name: 'ביטול', exact: true }).click();
    await p.waitForTimeout(1500);
    await p.getByRole('button', { name: NEW_DOC }).first().click();
    await p.waitForTimeout(1400);
    const secondCreate = await p.evaluate(() => {
      const b = [...document.querySelectorAll('button')]
        .find((x) => x.getAttribute('aria-label') === 'ביטול הפעולה האחרונה');
      return { disabled: b ? b.disabled : null, text: document.querySelector('textarea').value };
    });
    ok('יצירה שנייה אינה יורשת את היסטוריית הראשונה',
      secondCreate.disabled === true && secondCreate.text === '',
      JSON.stringify(secondCreate));

    // ── בידוד: אותו שם מסמך בשני פרויקטים ────────────────────────────
    // ה-slug נגזר מהכותרת, ולכן "ראשון" קיים בשני הפרויקטים. בלי
    // ה-slug של הפרויקט במפתח, המעבר ביניהם נראה למחסנית כמו הישארות.
    /* אותו דפוס כמו בהכנה הראשית: כל תשובה נבדקת, וה-slug מוחזר גם
       כשמשהו נכשל אחריו כדי שהניקוי ימצא מה למחוק.

       בלי בדיקת המסמך, המדד שלמטה מצהיר "נוצר פרויקט שני עם מסמך"
       ומאמת רק שהפרויקט נוצר — כלומר הוא ירוק גם כשאין מסמך, והכשל
       מתגלה שלושה שלבים אחר כך כ-timeout על כפתור העריכה, שאינו רומז
       על ההכנה. */
    const other = await p.evaluate(async ([stamp]) => {
      const post = (u, b) => fetch(u, {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b),
      });
      const r = await post('/api/projects', { name: 'פעולות ב ' + stamp });
      if (!r.ok) return { error: 'יצירת הפרויקט השני החזירה ' + r.status };
      const slug = (await r.json()).slug;
      if (!slug) return { error: 'יצירת הפרויקט השני לא החזירה slug' };
      const doc = await post(`/api/projects/${encodeURIComponent(slug)}/docs`,
        { title: 'ראשון', content: '# ראשון\n\nתוכן אחר לגמרי.\n' });
      if (!doc.ok) return { slug, error: 'יצירת המסמך החזירה ' + doc.status };
      return { slug };
    }, [process.pid]);
    if (other.slug) created.push(other.slug);
    ok('נוצר פרויקט שני עם מסמך באותו שם', !other.error, other.error || other.slug);
    if (other.error) throw new Error('ההכנה לבדיקת הבידוד נכשלה: ' + other.error);

    /* reload ולא goto בלבד: שינוי hash אינו טעינה מחדש, ורשימת
       הפרויקטים שבזיכרון נטענה לפני שהפרויקט השני נוצר. */
    await p.goto(BASE + '/#/' + encodeURIComponent(other.slug) + '/ראשון', { waitUntil: 'load' });
    await p.reload({ waitUntil: 'load' });
    await p.waitForSelector('#dc-root');
    await p.waitForTimeout(3400);
    await p.locator('button[title="עריכה"]').last().click();
    await p.waitForTimeout(1500);
    const crossProject = await p.evaluate(() => {
      const b = [...document.querySelectorAll('button')]
        .find((x) => x.getAttribute('aria-label') === 'ביטול הפעולה האחרונה');
      return { disabled: b ? b.disabled : null, text: document.querySelector('textarea').value };
    });
    ok('מסמך באותו שם בפרויקט אחר אינו יורש היסטוריה',
      crossProject.disabled === true && crossProject.text.indexOf('תוכן אחר לגמרי') >= 0,
      JSON.stringify(crossProject.text.slice(0, 26)));

    // ── הפעלה במקלדת ─────────────────────────────────────────────────
    // הכפתורים ביצעו ב-onMouseDown בלבד, ואז Tab+Enter לא עשה כלום:
    // כפתור ממוקד משגר click, לא mousedown.
    await p.fill('textarea', '# ראשון\n\nתוכן אחר לגמרי.\nשורה למחיקה במקלדת.\n');
    await p.waitForTimeout(1200);
    await p.locator('button[aria-label="ביטול הפעולה האחרונה"]').focus();
    await p.keyboard.press('Enter');
    await p.waitForTimeout(900);
    const byKeyboard = await p.evaluate(() => document.querySelector('textarea').value);
    ok('Enter על כפתור ממוקד מפעיל אותו',
      byKeyboard.indexOf('שורה למחיקה במקלדת') < 0, JSON.stringify(byKeyboard.slice(-26)));

    ok('אין שגיאות קונסולה', errs.length === 0, errs.slice(0, 3).join(' | '));
  } finally {
    for (const s of created) {
      const st = await ctx.request
        .delete(BASE + '/api/projects/' + encodeURIComponent(s), { headers: { Origin: BASE } })
        .then((r) => r.status())
        .catch((e) => 'שגיאה: ' + e.message);
      if (st !== 204 && st !== 404) console.log('  !  ניקוי הפרויקט ' + s + ' החזיר ' + st);
    }
    await br.close();
  }

  const failed = R.filter((x) => !x).length;
  console.log(failed ? `\nנכשלו ${failed}` : '\nהכול עבר');
  process.exit(failed ? 1 : 0);
})().catch((e) => { console.error('FATAL', e.message); process.exit(1); });
