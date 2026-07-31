/* בודק שהדף עובד במלואו בלי שום בקשה חיצונית מלבד גופנים.
 *
 * הרצה:
 *   npx http-server -p 8899 -s .
 *   node scripts/check-offline.js /tmp
 *
 * הבדיקה חוסמת כל בקשה שיוצאת מהמקור המקומי ואז מוודאת שהדף עדיין עולה,
 * שהצביעה של Prism רצה, שהתרשים מצויר, ושהאינטראקטיביות עובדת. האחרונה
 * חשובה במיוחד: window.__resources מדלג על שלב שבו ה-runtime קורא מחדש
 * את מקור התבנית, וצריך לוודא שזה לא שובר את שמות האטריביוטים.
 */
const { chromium } = require('/opt/node22/lib/node_modules/playwright');
const SP = process.argv[2];

(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1500, height: 1000 } });

  const external = [];
  const errs = [];
  await p.route('**/*', async (route) => {
    const url = route.request().url();
    if (url.startsWith('http://127.0.0.1:8899')) return route.continue();
    // גופנים הם התלות החיצונית היחידה שנשארה. הם לא מריצים קוד.
    if (!url.startsWith('https://fonts.')) external.push(url);
    return route.abort();
  });
  p.on('console', (m) => { if (m.type() === 'error') errs.push(m.text()); });
  p.on('pageerror', (e) => errs.push('PAGEERROR: ' + e.message));

  await p.goto('http://127.0.0.1:8899/index.html', { waitUntil: 'load', timeout: 60000 });
  await p.waitForSelector('#dc-root', { timeout: 20000 });
  await p.waitForTimeout(4000);

  const probe = async () => await p.evaluate(() => ({
    theme: document.documentElement.getAttribute('data-theme'),
    accent: getComputedStyle(document.documentElement).getPropertyValue('--accent').trim(),
    // הצביעה של Prism מייצרת span עם class token — אם היא לא רצה, אין אף אחד
    tokens: document.querySelectorAll('pre code span.token').length,
    languages: [...new Set([...document.querySelectorAll('pre code[class*="language-"]')]
      .map((el) => el.className.replace('language-', '')))].sort(),
    mermaid: document.querySelectorAll('[data-mermaid] svg').length,
    h2: document.querySelectorAll('h2').length,
  }));

  const dark = await probe();

  // ── אינטראקטיביות: זה מה שהיה נשבר אילו ה-casing של האטריביוטים אבד ──
  await p.click('button[title="מעבר למצב בהיר"], button[title="מעבר למצב כהה"]');
  await p.waitForTimeout(2500);
  const light = await probe();

  // onChange על שדה חיפוש
  await p.fill('input[placeholder="חיפוש במסמכים…"]', 'רכיבי');
  await p.waitForTimeout(600);
  const searchWorks = await p.evaluate(
    () => document.querySelector('input[placeholder="חיפוש במסמכים…"]').value === 'רכיבי'
  );
  await p.fill('input[placeholder="חיפוש במסמכים…"]', '');
  await p.waitForTimeout(400);

  // onClick על טאב מסך
  await p.getByRole('button', { name: 'רפרנס', exact: true }).click();
  await p.waitForTimeout(1500);
  const refSections = await p.evaluate(() => document.querySelectorAll('[data-sec]').length);
  await p.screenshot({ path: SP + '/offline-ref.png' });

  await p.getByRole('button', { name: 'ספרייה', exact: true }).click();
  await p.waitForTimeout(1200);
  const backToLibrary = await p.evaluate(() => document.querySelectorAll('[data-h2]').length > 0);
  await p.screenshot({ path: SP + '/offline-lib.png' });

  console.log(JSON.stringify({
    externalRequests: external,
    errs: errs.slice(0, 8),
    dark, light,
    interactive: { themeToggle: dark.theme !== light.theme, searchWorks, refSections, backToLibrary },
  }, null, 2));

  await b.close();

  const ok =
    external.length === 0 &&
    dark.tokens > 0 &&
    dark.mermaid > 0 &&
    dark.theme !== light.theme &&
    searchWorks &&
    refSections === 8 &&
    backToLibrary;
  console.log(ok ? '\nהכול עבר' : '\nנכשל');
  process.exit(ok ? 0 : 1);
})().catch((e) => { console.error('FATAL', e.message); process.exit(1); });
