/* מחיקת פרויקט.
 *
 *   node scripts/check-projects.js http://127.0.0.1:8070
 *
 * המחיקה נמדדת מול השרת ולא רק מול המסך: כרטיס שנעלם מהרשימה נראה
 * זהה בין מחיקה שהצליחה לבין רענון שהחזיר רשימה חלקית, ורק בקשה
 * לפרויקט עצמו מבדילה ביניהן.
 *
 * הקלדת השם נבדקת משני הכיוונים. שם שגוי שאינו מוחק אינו מספיק —
 * צריך גם שהשם הנכון כן ימחק, אחרת כפתור שבור היה עובר את הבדיקה.
 *
 * הניקוי יושב ב-finally, כדי שהרצה שנופלת באמצע לא תשאיר פרויקטים
 * במסד ואת הדפדפן פתוח.
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
  console.error('שימוש: node scripts/check-projects.js <כתובת-בסיס>');
  process.exit(1);
}

const EMAIL = process.env.ADMIN_EMAIL || 'admin@example.com';
const PASSWORD = process.env.ADMIN_PASSWORD || 'correct-horse-battery';

const R = [];
const ok = (l, v, x) => { R.push(v); console.log(`  ${v ? '✓' : '✗'}  ${l}${x ? '  — ' + x : ''}`); };

(async () => {
  const br = await chromium.launch();
  const ctx = await br.newContext({ viewport: { width: 1400, height: 1000 } });
  const p = await ctx.newPage();
  const created = [];
  const errs = [];
  /* הבדיקות מול השרת רצות דרך ctx.request ולא דרך fetch בתוך הדף:
     הדפדפן רושם כל תשובת 404 בקונסולה, וה-404 שמוכיח שהמחיקה עבדה
     היה נספר כשגיאת אפליקציה ומפיל את הבדיקה על עצמה. */
  const status = async (slug) =>
    (await ctx.request.get(BASE + '/api/projects/' + encodeURIComponent(slug))).status();
  try {
    p.on('pageerror', (e) => errs.push('PAGEERROR: ' + e.message));
    p.on('console', (m) => { if (m.type() === 'error') errs.push(m.text()); });

    await p.goto(BASE + '/', { waitUntil: 'load' });
    await p.waitForSelector('#dc-root');
    await p.waitForTimeout(2400);

    // ── אורח: אין כפתור מחיקה כלל ─────────────────────────────────────
    // נבדק לפני הכניסה, כי אחריה ה-cookie כבר קיים.
    const guestButtons = await p.locator('button[title="מחיקת הפרויקט"]').count();
    ok('אורח אינו רואה כפתור מחיקה', guestButtons === 0, guestButtons + ' כפתורים');

    await p.getByRole('button', { name: 'כניסה' }).click();
    await p.waitForTimeout(600);
    await p.fill('input[type="email"]', EMAIL);
    await p.fill('input[type="password"]', PASSWORD);
    await p.getByRole('button', { name: 'כניסה', exact: true }).last().click();
    await p.waitForTimeout(2400);

    // שני פרויקטים: אחד למחיקה ואחד שחייב לשרוד אותה.
    //
    // כל POST נבדק. בלי זה, כניסה שנכשלה מחזירה 401, ה-slug יוצא
    // undefined, וכל המדדים נופלים על סלקטורים ריקים — כלומר הפלט
    // מצביע על המסך בזמן שהבעיה בהכנה. כשל מפורש חוסך את החיפוש.
    const setup = await p.evaluate(async ([stamp]) => {
      const post = async (u, b) => {
        const r = await fetch(u, {
          method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b),
        });
        if (!r.ok) throw new Error('POST ' + u + ' החזיר ' + r.status);
        return r;
      };
      const doomedName = 'למחיקה ' + stamp;
      const keptName = 'לשמירה ' + stamp;
      const doomed = (await (await post('/api/projects', { name: doomedName })).json()).slug;
      const kept = (await (await post('/api/projects', { name: keptName })).json()).slug;
      if (!doomed || !kept) throw new Error('יצירת הפרויקט לא החזירה slug');
      await post(`/api/projects/${encodeURIComponent(doomed)}/docs`, { title: 'ראשון', content: '# ראשון\n' });
      await post(`/api/projects/${encodeURIComponent(doomed)}/docs`, { title: 'שני', content: '# שני\n' });
      await post(`/api/projects/${encodeURIComponent(doomed)}/links`, { title: 'קישור', url: 'https://example.com' });
      return { doomed, kept, doomedName, keptName };
    }, [process.pid]);
    created.push(setup.doomed, setup.kept);

    await p.reload({ waitUntil: 'load' });
    await p.waitForSelector('#dc-root');
    await p.waitForTimeout(2400);

    // ── הכפתור קיים, וההיררכיה של ה-HTML תקינה ───────────────────────
    const card = p.locator('button[aria-label="מחיקת הפרויקט ' + setup.doomedName + '"]');
    ok('יש כפתור מחיקה לכל פרויקט', await card.count() === 1);
    // כפתור בתוך <a> אינו HTML חוקי. הדפדפן אינו מתלונן, אבל יעד
    // הלחיצה הופך לדו-משמעי, ולכן זה נבדק במפורש.
    const nested = await p.evaluate(() =>
      [...document.querySelectorAll('button')].filter((b) => b.closest('a')).length);
    ok('אף כפתור אינו יושב בתוך קישור', nested === 0, nested + ' מקוננים');

    // ── האישור אומר מה נמחק ביחד ──────────────────────────────────────
    await card.click();
    await p.waitForTimeout(700);
    const modal = await p.evaluate(() => {
      const texts = [...document.querySelectorAll('h2, p, label')].map((e) => e.textContent.trim());
      const btn = [...document.querySelectorAll('button')].find((b) => b.textContent.trim() === 'מחיקה');
      return {
        question: texts.some((t) => t === 'למחוק את הפרויקט?'),
        name: texts.some((t) => t.indexOf('למחיקה') === 0),
        note: texts.find((t) => t.indexOf('יימחק איתו') > 0) || '',
        disabled: btn ? btn.disabled : null,
      };
    });
    ok('האישור שואל על פרויקט ונוקב בשם', modal.question && modal.name, JSON.stringify(modal.name));
    ok('האישור מונה מה נמחק ביחד', /2 מסמכים/.test(modal.note) && /קישור אחד/.test(modal.note), modal.note);
    ok('כפתור המחיקה מושבת לפני ההקלדה', modal.disabled === true);

    // ── שם שגוי אינו משחרר את הכפתור ─────────────────────────────────
    await p.fill('input[data-confirm-name]', setup.doomedName + 'x');
    await p.waitForTimeout(400);
    const wrong = await p.evaluate(() => {
      const b = [...document.querySelectorAll('button')].find((x) => x.textContent.trim() === 'מחיקה');
      return b ? b.disabled : null;
    });
    ok('שם שגוי משאיר את הכפתור מושבת', wrong === true);

    // Enter על שם שגוי גם הוא אינו מוחק: הכפתור המושבת אינו המנגנון.
    await p.locator('input[data-confirm-name]').press('Enter');
    await p.waitForTimeout(1200);
    ok('Enter על שם שגוי אינו מוחק', await status(setup.doomed) === 200);

    // ── ביטול אינו מוחק ──────────────────────────────────────────────
    await p.getByRole('button', { name: 'ביטול' }).click();
    await p.waitForTimeout(700);
    ok('ביטול אינו מוחק', await status(setup.doomed) === 200);

    // השדה נקי בפתיחה הבאה: טקסט ששרד היה יכול לשחרר את הכפתור של
    // פרויקט אחר בעל אותו שם בלי שהוקלד דבר.
    await card.click();
    await p.waitForTimeout(700);
    const reopened = await p.evaluate(() => {
      const i = document.querySelector("input[data-confirm-name]");
      return i ? i.value : null;
    });
    ok('פתיחה מחדש מתחילה משדה ריק', reopened === '', JSON.stringify(reopened));

    // ── השם הנכון מוחק ───────────────────────────────────────────────
    await p.fill('input[data-confirm-name]', setup.doomedName);
    await p.waitForTimeout(400);
    await p.getByRole('button', { name: 'מחיקה', exact: true }).click();
    await p.waitForTimeout(2500);

    const gone = await status(setup.doomed);
    ok('השם הנכון מוחק בשרת', gone === 404, 'status ' + gone);
    ok('הפרויקט השני שרד', await status(setup.kept) === 200);

    const listed = await p.evaluate(() =>
      [...document.querySelectorAll('a[href="#top"]')].map((a) => a.textContent));
    ok('הכרטיס נעלם מהרשימה בלי רענון',
      !listed.some((t) => t.indexOf(setup.doomedName) >= 0)
      && listed.some((t) => t.indexOf(setup.keptName) >= 0));

    // ── מחיקת הפרויקט הפתוח מחזירה לרשימה ────────────────────────────
    // המסלול הזה מגיע ממצב אחר: פרויקט פתוח ב-state וכתובת שמצביעה
    // עליו. בלי איפוס, רענון היה מנסה לפתוח פרויקט שאינו קיים.
    await p.locator('a[href="#top"]').filter({ hasText: setup.keptName }).first().click();
    await p.waitForTimeout(1800);
    await p.getByRole('button', { name: 'פרויקטים', exact: true }).click();
    await p.waitForTimeout(1400);
    await p.locator('button[aria-label="מחיקת הפרויקט ' + setup.keptName + '"]').click();
    await p.waitForTimeout(700);
    await p.fill('input[data-confirm-name]', setup.keptName);
    await p.waitForTimeout(400);
    await p.getByRole('button', { name: 'מחיקה', exact: true }).click();
    await p.waitForTimeout(2500);

    const landing = await p.evaluate(() => ({
      hash: location.hash,
      onList: !!document.querySelector('input[placeholder="שם הפרויקט החדש"]'),
    }));
    ok('אחרי מחיקת הפרויקט הפתוח נשארים ברשימה',
      landing.onList && (landing.hash === '#/' || landing.hash === ''), JSON.stringify(landing));

    ok('אין שגיאות קונסולה', errs.length === 0, errs.slice(0, 3).join(' | '));
  } finally {
    // כשל מחיקה מדווח ואינו מסתיר את השגיאה המקורית. 404 תקין: זה
    // בדיוק מה שהבדיקה עשתה בעצמה.
    for (const s of created) {
      // Origin מפורש: בקשה משנה בלעדיו נחסמת ב-403 על ידי הגנת ה-CSRF,
      // וזאת התנהגות נכונה של השרת — הדפדפן שולח אותו מעצמו, ולקוח
      // ה-request לא.
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
