/* שמירה אוטומטית של טיוטות בדפדפן.
 *
 *   node scripts/check-drafts.js http://127.0.0.1:8070
 *
 * שני חורים שונים נבדקים כאן, כי הגיבוי סוגר את שניהם: מסמך חדש
 * שאינו קיים בשרת עד ללחיצה, ומסמך קיים ששמירתו לשרת נכשלה.
 *
 * המדד המרכזי הוא רענון אמיתי ולא קריאה ל-localStorage: מה שנשמר
 * ואינו נטען חזרה שווה בדיוק לכלום, וקריאת המפתח לבדה לא מבדילה
 * בין השניים.
 *
 * הכישלון ברשת מיוצר ב-route.abort ולא בכיבוי השרת, כדי שהבדיקה
 * תמדוד את העורך ולא את סביבת ההרצה.
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
  console.error('שימוש: node scripts/check-drafts.js <כתובת-בסיס>');
  process.exit(1);
}

const EMAIL = process.env.ADMIN_EMAIL || 'admin@example.com';
const PASSWORD = process.env.ADMIN_PASSWORD || 'correct-horse-battery';

const R = [];
const ok = (l, v, x) => { R.push(v); console.log(`  ${v ? '✓' : '✗'}  ${l}${x ? '  — ' + x : ''}`); };

(async () => {
  const br = await chromium.launch();
  const ctx = await br.newContext({ viewport: { width: 1500, height: 1000 } });
  const p = await ctx.newPage();
  const created = [];
  const errs = [];
  /* בזמן שהחסימה דלוקה הדפדפן רושם ERR_FAILED על כל PUT שהופל
     בכוונה. התעלמות גורפת מ-ERR_FAILED הייתה מסתירה גם תקלת רשת
     אמיתית, ולכן היא מוגבלת לחלון שבו אנחנו עצמנו מפילים בקשות. */
  let blocking = false;
  const status = async (slug) =>
    (await ctx.request.get(BASE + '/api/projects/' + encodeURIComponent(slug))).status();

  try {
    p.on('pageerror', (e) => errs.push('PAGEERROR: ' + e.message));
    p.on('console', (m) => {
      if (m.type() !== 'error') return;
      if (blocking && m.text().indexOf('net::ERR_FAILED') >= 0) return;
      errs.push(m.text());
    });

    await p.goto(BASE + '/', { waitUntil: 'load' });
    await p.waitForSelector('#dc-root');
    await p.waitForTimeout(2400);
    await p.getByRole('button', { name: 'כניסה' }).click();
    await p.waitForTimeout(600);
    await p.fill('input[type="email"]', EMAIL);
    await p.fill('input[type="password"]', PASSWORD);
    await p.getByRole('button', { name: 'כניסה', exact: true }).last().click();
    await p.waitForTimeout(2400);

    const setup = await p.evaluate(async ([stamp]) => {
      const post = async (u, b) => {
        const r = await fetch(u, {
          method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b),
        });
        if (!r.ok) throw new Error('POST ' + u + ' החזיר ' + r.status);
        return r;
      };
      const name = 'טיוטות ' + stamp;
      const slug = (await (await post('/api/projects', { name })).json()).slug;
      if (!slug) throw new Error('יצירת הפרויקט לא החזירה slug');
      await post(`/api/projects/${encodeURIComponent(slug)}/docs`,
        { title: 'קיים', content: '# קיים\n\nתוכן מקורי.\n' });
      return { slug, name };
    }, [process.pid]);
    created.push(setup.slug);

    await p.goto(BASE + '/#/' + encodeURIComponent(setup.slug), { waitUntil: 'load' });
    await p.waitForSelector('#dc-root');
    await p.waitForTimeout(3000);

    // ── טיוטת מסמך חדש שורדת רענון ────────────────────────────────────
    await p.getByRole('button', { name: /כתיבת מסמך חדש|new document|מסמך חדש/i }).first().click();
    await p.waitForTimeout(1200);
    await p.fill('textarea', '# טיוטה שלא נשמרה\n\nשורה שנכתבה ולא הוגשה.\n');
    await p.waitForTimeout(2200);

    const stored = await p.evaluate(() =>
      Object.keys(localStorage).filter((k) => k.indexOf('md-docs-draft-v1:') === 0));
    ok('הטיוטה נכתבה לאחסון המקומי', stored.length === 1, JSON.stringify(stored));

    await p.reload({ waitUntil: 'load' });
    await p.waitForSelector('#dc-root');
    await p.waitForTimeout(3200);

    const banner = p.locator('[data-draft-notice]');
    await banner.waitFor({ state: 'visible', timeout: 15000 });
    const bannerText = await banner.textContent();
    ok('אחרי רענון מוצע לשחזר', bannerText.indexOf('טיוטה שלא נשמרה') >= 0, bannerText.trim());
    // הבאנר ולא שחזור אוטומטי: התיבה עדיין ריקה עד שמבקשים.
    const beforeRestore = await p.evaluate(() => {
      const t = document.querySelector('textarea');
      return t ? t.value : null;
    });
    ok('השחזור אינו אוטומטי', !beforeRestore || beforeRestore.indexOf('שורה שנכתבה') < 0,
      JSON.stringify(beforeRestore));

    await p.getByRole('button', { name: 'שחזור' }).click();
    await p.waitForTimeout(1600);
    const restored = await p.evaluate(() => document.querySelector('textarea').value);
    ok('השחזור מחזיר את התוכן',
      restored.indexOf('שורה שנכתבה ולא הוגשה') >= 0, JSON.stringify(restored.slice(0, 40)));

    // ── הגשה מוחקת את הגיבוי ─────────────────────────────────────────
    await p.getByRole('button', { name: 'הוספה לפרויקט' }).first().click();
    await p.waitForTimeout(3000);
    const afterSubmit = await p.evaluate(() =>
      Object.keys(localStorage).filter((k) => k.indexOf('md-docs-draft-v1:') === 0));
    ok('הגשה מוצלחת מוחקת את הגיבוי', afterSubmit.length === 0, JSON.stringify(afterSubmit));

    // ── מסך יצירה ריק אינו מוחק טיוטה קיימת ──────────────────────────
    // הגרסה הראשונה מחקה כאן תמיד: "מסמך חדש" ואז יציאה, בלי להקליד
    // תו, מחקו טיוטה שנשמרה קודם — בלי אישור ובלי סימן. זה בדיוק
    // הכישלון שהמנגנון בא למנוע, ולכן הוא נבדק ולא רק תוקן.
    await p.getByRole('button', { name: /כתיבת מסמך חדש|new document|מסמך חדש/i }).first().click();
    await p.waitForTimeout(1200);
    await p.fill('textarea', '# עבודה יקרה\n\nלא ללכת לאיבוד.\n');
    await p.waitForTimeout(2200);
    await p.reload({ waitUntil: 'load' });
    await p.waitForSelector('#dc-root');
    await p.waitForTimeout(3200);
    await p.getByRole('button', { name: /כתיבת מסמך חדש|new document|מסמך חדש/i }).first().click();
    await p.waitForTimeout(1200);
    await p.getByRole('button', { name: 'פרויקטים', exact: true }).click();
    await p.waitForTimeout(1600);
    const survived = await p.evaluate(() =>
      Object.keys(localStorage).filter((k) => k.indexOf('md-docs-draft-v1:') === 0));
    ok('מסך יצירה ריק אינו מוחק טיוטה קיימת', survived.length === 1, JSON.stringify(survived));

    // ניקוי לקראת ההמשך: הפעם מוחקים במפורש מהבאנר.
    await p.goto(BASE + '/#/' + encodeURIComponent(setup.slug), { waitUntil: 'load' });
    await p.waitForSelector('#dc-root');
    await p.waitForTimeout(3200);
    await p.getByRole('button', { name: 'מחיקה', exact: true }).first().click();
    await p.waitForTimeout(1000);

    // ── מסמך קיים: שמירה שנכשלה משאירה גיבוי ─────────────────────────
    await p.locator('a[href="#top"]').filter({ hasText: 'קיים' }).first().click();
    await p.waitForTimeout(1800);
    await p.locator('button[title="עריכה"]').last().click();
    await p.waitForTimeout(1400);

    // כל PUT נופל. זה מדמה רשת שנעלמה, לא שרת שכבוי.
    blocking = true;
    await p.route('**/api/projects/**/docs/**', (route) =>
      route.request().method() === 'PUT' ? route.abort('failed') : route.continue());
    await p.fill('textarea', '# קיים\n\nתוכן שלא הגיע לשרת.\n');
    await p.waitForTimeout(4500);

    const failedState = await p.evaluate(() => ({
      keys: Object.keys(localStorage).filter((k) => k.indexOf('md-docs-draft-v1:') === 0),
      label: [...document.querySelectorAll('span')]
        .map((e) => e.textContent.trim()).filter((t) => t === 'השמירה נכשלה').length,
    }));
    ok('שמירה שנכשלה משאירה גיבוי מקומי',
      failedState.keys.length === 1, JSON.stringify(failedState));

    /* החסימה שורדת את הרענון בכוונה. ה-handler של pagehide מריץ
       flushSave בדרך החוצה, ולכן unroute לפני reload היה גורם לשמירה
       להצליח דווקא אז — והבדיקה הייתה מודדת רענון מוצלח במקום רשת
       שנפלה. */
    await p.reload({ waitUntil: 'load' });
    await p.waitForSelector('#dc-root');
    await p.waitForTimeout(3400);

    const editBanner = p.locator('[data-draft-notice]');
    await editBanner.waitFor({ state: 'visible', timeout: 15000 });
    const editText = await editBanner.textContent();
    ok('אחרי רענון מוצע לשחזר את מה שלא נשמר',
      editText.indexOf('לא הגיעו לשרת') >= 0, editText.trim());
    ok('אין אזהרת התנגשות כשהשרת לא השתנה',
      editText.indexOf('השתנה מאז') < 0, editText.trim());

    // מכאן הרשת חוזרת, כדי שהשחזור באמת יגיע לשרת.
    await p.unroute('**/api/projects/**/docs/**');
    blocking = false;
    await p.getByRole('button', { name: 'שחזור' }).click();
    await p.waitForTimeout(3000);
    const saved = await p.evaluate(async (s) => {
      const r = await fetch('/api/projects/' + encodeURIComponent(s) + '/docs/קיים',
        { credentials: 'same-origin' });
      return r.ok ? (await r.json()).content : 'status ' + r.status;
    }, setup.slug);
    ok('השחזור נשמר לשרת מעצמו',
      saved.indexOf('תוכן שלא הגיע לשרת') >= 0, JSON.stringify(saved.slice(0, 40)));

    // ── אזהרת התנגשות: השרת השתנה מתחת לגיבוי ────────────────────────
    // מאז שיש MCP זה תרחיש ממשי — סוכן כותב למסמך שפתוח בעורך.
    blocking = true;
    await p.route('**/api/projects/**/docs/**', (route) =>
      route.request().method() === 'PUT' ? route.abort('failed') : route.continue());
    await p.fill('textarea', '# קיים\n\nעריכה מקומית בזמן שמישהו אחר כתב.\n');
    await p.waitForTimeout(4000);

    // כתיבה מבחוץ, דרך request כדי שלא תעבור דרך העורך הפתוח — ומעל
    // ה-route, שחוסם רק בקשות שיוצאות מהדף עצמו.
    const put = await ctx.request.put(
      BASE + '/api/projects/' + encodeURIComponent(setup.slug) + '/docs/קיים',
      { headers: { Origin: BASE }, data: { content: '# קיים\n\nנכתב ממקום אחר.\n' } });
    ok('הכתיבה החיצונית הצליחה', put.status() === 200, 'status ' + put.status());

    await p.reload({ waitUntil: 'load' });
    await p.waitForSelector('#dc-root');
    await p.waitForTimeout(3400);
    const staleBanner = p.locator('[data-draft-notice]');
    await staleBanner.waitFor({ state: 'visible', timeout: 15000 });
    const staleText = await staleBanner.textContent();
    ok('התנגשות מוצהרת במפורש', staleText.indexOf('השתנה מאז') >= 0, staleText.trim());
    await p.unroute('**/api/projects/**/docs/**');
    blocking = false;

    // ── מחיקה מהבאנר ─────────────────────────────────────────────────
    await p.getByRole('button', { name: 'מחיקה', exact: true }).first().click();
    await p.waitForTimeout(1200);
    const afterDiscard = await p.evaluate(() => ({
      keys: Object.keys(localStorage).filter((k) => k.indexOf('md-docs-draft-v1:') === 0),
      banner: !!document.querySelector('[data-draft-notice]'),
    }));
    ok('מחיקה מהבאנר מסירה את הגיבוי',
      afterDiscard.keys.length === 0 && !afterDiscard.banner, JSON.stringify(afterDiscard));

    // ── תפוגה: גיבוי בן יותר משבוע אינו מוצע ─────────────────────────
    await p.evaluate((s) => {
      localStorage.setItem('md-docs-draft-v1:' + s, JSON.stringify({
        v: 1, at: Date.now() - 8 * 24 * 60 * 60 * 1000,
        title: 'ישן', content: '# ישן\n', base: null,
      }));
    }, setup.slug);
    await p.reload({ waitUntil: 'load' });
    await p.waitForSelector('#dc-root');
    await p.waitForTimeout(3400);
    const expired = await p.evaluate(() => ({
      keys: Object.keys(localStorage).filter((k) => k.indexOf('md-docs-draft-v1:') === 0),
      banner: !!document.querySelector('[data-draft-notice]'),
    }));
    ok('גיבוי שפג תוקפו אינו מוצע ונמחק',
      expired.keys.length === 0 && !expired.banner, JSON.stringify(expired));

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
