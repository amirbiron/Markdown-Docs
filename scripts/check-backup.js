/* מדדי הקבלה של שלב 7, לפי ROADMAP.md:
 *
 *   אחרי ההעלאה, ניקוי נתוני האתר לא מוחק כלום. ה-ZIP נפתח ומכיל את כל
 *   המסמכים. אחרי 31 גיבויים, הישן ביותר נמחק.
 *
 * (הרוטציה נבדקת ב-tests/test_scheduler.py — היא לא נוגעת בדפדפן.)
 *
 * הרצה:
 *   node scripts/check-backup.js http://127.0.0.1:8070 <תיקיית-צילומים>
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
  console.error('שימוש: node scripts/check-backup.js <כתובת-בסיס> <תיקיית-צילומים>');
  process.exit(1);
}

const EMAIL = process.env.ADMIN_EMAIL || 'admin@example.com';
const PASSWORD = process.env.ADMIN_PASSWORD || 'correct-horse-battery';
const NAME = 'בדיקת גיבוי ' + process.pid;
const LEGACY_KEY = 'md-docs-site-v1';

const results = [];
const check = (label, ok, extra) => {
  results.push({ label, ok, extra });
  console.log(`  ${ok ? '✓' : '✗'}  ${label}${extra ? '  — ' + extra : ''}`);
};

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1500, height: 1000 } });
  const p = await ctx.newPage();
  const errs = [];
  p.on('console', (m) => { if (m.type() === 'error') errs.push(m.text()); });
  p.on('pageerror', (e) => errs.push('PAGEERROR: ' + e.message));

  await p.goto(BASE + '/', { waitUntil: 'load' });
  await p.waitForSelector('#dc-root', { timeout: 20000 });

  // מסמכים ישנים באחסון המקומי, במבנה של הגרסה הקודמת. כולל את מסמך
  // ההדגמה, שאסור שיועלה.
  await p.evaluate(([key]) => {
    localStorage.setItem(key, JSON.stringify([
      { id: 'sample', name: 'markdown-components.md', src: '# הדגמה\n\nלא אמור לעבור.\n' },
      { id: 'a1', name: 'מדריך-ישן.md', src: '# מדריך ישן\n\nתוכן שנשמר בדפדפן.\n' },
      { id: 'a2', name: 'הערות.md', src: 'בלי כותרת ראשית, השם הוא הכותרת.\n' },
    ]));
  }, [LEGACY_KEY]);

  const setup = await p.evaluate(async ([email, password, name]) => {
    const post = (u, b) => fetch(u, { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b) });
    const login = await post('/api/auth/login', { email, password });
    if (!login.ok) return { error: 'כניסה נכשלה: ' + login.status };
    const created = await post('/api/projects', { name });
    if (!created.ok) return { error: 'יצירת פרויקט נכשלה: ' + created.status };
    return { slug: (await created.json()).slug };
  }, [EMAIL, PASSWORD, NAME]);
  if (setup.error) { console.error('FATAL ' + setup.error); await browser.close(); process.exit(1); }

  await p.reload({ waitUntil: 'load' });
  await p.waitForSelector('#dc-root', { timeout: 20000 });
  const projectLink = p.getByText(NAME).first();
  await projectLink.waitFor({ state: 'visible', timeout: 20000 });
  await projectLink.click();
  await p.waitForTimeout(2000);

  // ── מדד: ההגירה ───────────────────────────────────────────────────
  const banner = p.getByText(/מסמכים נמצאו באחסון של הדפדפן/);
  await banner.waitFor({ state: 'visible', timeout: 20000 });
  const bannerText = await banner.textContent();
  check('הצעת ההגירה מופיעה ומדלגת על מסמך ההדגמה',
    bannerText.indexOf('2 מסמכים') >= 0, bannerText.trim());

  await p.getByRole('button', { name: 'העלאה לפרויקט הזה' }).click();
  await p.waitForTimeout(4000);
  await p.screenshot({ path: SHOTS + '/backup-migrated.png' });

  const uploaded = await p.evaluate(async (slug) => {
    const r = await fetch('/api/projects/' + encodeURIComponent(slug), { credentials: 'same-origin' });
    const body = await r.json();
    return body.documents.map((d) => d.title).sort();
  }, setup.slug);
  check('שני המסמכים עלו לשרת', uploaded.length === 2, JSON.stringify(uploaded));
  check('כותרת נלקחת מהשורה הראשונה', uploaded.indexOf('מדריך ישן') >= 0, JSON.stringify(uploaded));
  check('ובהיעדרה — משם הקובץ', uploaded.indexOf('הערות') >= 0, JSON.stringify(uploaded));
  check('מסמך ההדגמה לא הועלה',
    uploaded.every((t) => t.indexOf('הדגמה') < 0), JSON.stringify(uploaded));

  // ── מדד: ניקוי נתוני האתר לא מוחק כלום ────────────────────────────
  const cleared = await p.evaluate(([key]) => {
    const before = localStorage.getItem(key);
    localStorage.clear();
    sessionStorage.clear();
    return { hadKeyAfterMigration: before !== null };
  }, [LEGACY_KEY]);
  check('המפתח הישן נמחק אחרי הגירה מלאה', cleared.hadKeyAfterMigration === false);

  await p.reload({ waitUntil: 'load' });
  await p.waitForSelector('#dc-root', { timeout: 20000 });
  await p.waitForTimeout(2500);

  const survived = await p.evaluate(async (slug) => {
    const r = await fetch('/api/projects/' + encodeURIComponent(slug), { credentials: 'same-origin' });
    if (!r.ok) return null;
    return (await r.json()).documents.map((d) => d.title).sort();
  }, setup.slug);
  check('אחרי ניקוי נתוני האתר המסמכים עדיין קיימים',
    !!survived && survived.length === 2, JSON.stringify(survived));

  // ── מדד: ה-ZIP נפתח ומכיל את כל המסמכים ───────────────────────────
  // ההורדה נעשית מהדפדפן ולא ב-curl, כדי שהיא תעבור באותו מסלול cookie
  // שהמשתמש עובר בו.
  const zipInfo = await p.evaluate(async () => {
    const res = await fetch('/api/backup.zip', { credentials: 'same-origin' });
    if (!res.ok) return { error: res.status };
    const buf = new Uint8Array(await res.arrayBuffer());
    return {
      status: res.status,
      type: res.headers.get('content-type'),
      size: buf.length,
      // חתימת ZIP: PK\x03\x04
      magic: buf[0] === 0x50 && buf[1] === 0x4b && buf[2] === 0x03 && buf[3] === 0x04,
      bytes: Array.from(buf),
    };
  });
  check('ההורדה מחזירה ZIP',
    !zipInfo.error && zipInfo.magic && zipInfo.type === 'application/zip',
    JSON.stringify({ status: zipInfo.status, type: zipInfo.type, size: zipInfo.size }));

  if (!zipInfo.error) {
    const fs = require('fs');
    const zipPath = SHOTS + '/backup-download.zip';
    fs.writeFileSync(zipPath, Buffer.from(zipInfo.bytes));

    // נפתח בכלי חיצוני — כלומר הוא ZIP אמיתי ולא רק נראה כמו אחד
    const { execFileSync } = require('child_process');
    let listing = '';
    try {
      listing = execFileSync('unzip', ['-Z1', zipPath], { encoding: 'utf8' });
    } catch (e) {
      listing = 'unzip נכשל: ' + e.message;
    }
    const names = listing.split('\n').filter(Boolean);
    check('הארכיון נפתח בכלי חיצוני', names.length > 0, names.length + ' רשומות');
    check('ומכיל את שני המסמכים שהועלו',
      names.filter((n) => n.startsWith(setup.slug + '/') && n.endsWith('.md')).length === 2,
      names.filter((n) => n.startsWith(setup.slug + '/')).join(' '));
    check('ומכיל manifest', names.indexOf('manifest.json') >= 0);
  }

  const anonZip = await ctx.browser().newContext();
  const ap = await anonZip.newPage();
  await ap.goto(BASE + '/', { waitUntil: 'load' });
  const anonStatus = await ap.evaluate(async () => {
    const r = await fetch('/api/backup.zip', { credentials: 'same-origin' });
    return r.status;
  });
  check('אנונימי לא מקבל את הגיבוי', anonStatus === 401, 'status ' + anonStatus);
  await anonZip.close();

  check('אין שגיאות קונסולה', errs.length === 0, errs.slice(0, 3).join(' | '));

  // ── הענף שמגן על הנתונים: הגירה חלקית לא מוחקת ────────────────────
  // מפילים בקשות בכוונה, ולכן הבדיקה הזאת אחרי בדיקת הקונסולה.
  const partialName = 'הגירה חלקית ' + process.pid;
  const partialSlug = await p.evaluate(async ([name]) => {
    localStorage.setItem('md-docs-site-v1', JSON.stringify([
      { id: 'b1', name: 'ראשון.md', src: '# ראשון\n\nתוכן.\n' },
      { id: 'b2', name: 'שני.md', src: '# שני\n\nתוכן.\n' },
      { id: 'b3', name: 'שלישי.md', src: '# שלישי\n\nתוכן.\n' },
    ]));
    const r = await fetch('/api/projects', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name }),
    });
    return (await r.json()).slug;
  }, [partialName]);

  // הכתובת מאופסת לרשימת הפרויקטים לפני הטעינה מחדש: מאז שנוסף ניתוב,
  // reload משחזר את המסמך שהיה פתוח ולא נוחת ברשימה.
  await p.goto(BASE + '/#/', { waitUntil: 'load' });
  await p.reload({ waitUntil: 'load' });
  await p.waitForSelector('#dc-root', { timeout: 20000 });
  const partialLink = p.getByText(partialName).first();
  await partialLink.waitFor({ state: 'visible', timeout: 20000 });
  await partialLink.click();
  await p.waitForTimeout(2000);

  let posts = 0;
  await p.route('**/docs', (route) => {
    if (route.request().method() === 'POST' && ++posts >= 3) return route.abort('failed');
    return route.continue();
  });
  await p.getByRole('button', { name: 'העלאה לפרויקט הזה' }).click();
  await p.waitForTimeout(5000);
  await p.unroute('**/docs');

  const partial = await p.evaluate(() => {
    const raw = localStorage.getItem('md-docs-site-v1');
    return { kept: raw !== null, count: raw ? JSON.parse(raw).length : 0 };
  });
  check('הגירה חלקית משאירה את העותק המקומי',
    partial.kept && partial.count === 3, JSON.stringify(partial));

  await p.evaluate(async (slug) => {
    await fetch('/api/projects/' + encodeURIComponent(slug),
      { method: 'DELETE', credentials: 'same-origin' });
  }, partialSlug);

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
