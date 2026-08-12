/* בודק שהדף עובד במלואו בלי שום בקשה חיצונית.
 *
 * הרצה:
 *   python3 -m uvicorn app.main:app --port 8070
 *   node scripts/check-offline.js /tmp [http://127.0.0.1:8070]
 *
 * הבדיקה חוסמת כל בקשה שיוצאת מהמקור המקומי ואז מוודאת שהדף עדיין עולה,
 * שהצביעה של Prism רצה, שהתרשים מצויר, ושהאינטראקטיביות עובדת. האחרונה
 * חשובה במיוחד: window.__resources מדלג על שלב שבו ה-runtime קורא מחדש
 * את מקור התבנית, וצריך לוודא שזה לא שובר את שמות האטריביוטים.
 *
 * מסך הרפרנס הוא מה שנבדק כאן, ולא מסך הפרויקטים: הוא סטטי לחלוטין ולכן
 * מה שנכשל בו הוא נכס מקומי שבור ולא שרת שלא ענה. את מסך הפרויקטים בודק
 * scripts/check-ui.js מול API אמיתי.
 */
/* playwright נפתר דרך node_modules הרגיל. אם הוא מותקן גלובלית בלבד,
   NODE_PATH מצביע עליו — עדיף על נתיב מוחלט שקשור למכונה אחת. */
let chromium;
try {
  ({ chromium } = require('playwright'));
} catch {
  console.error('playwright לא מותקן. הריצו: npm install');
  process.exit(1);
}

const SP = process.argv[2];
const BASE = (process.argv[3] || 'http://127.0.0.1:8070').replace(/\/+$/, '');
const EMAIL = process.env.ADMIN_EMAIL || 'admin@example.com';
const PASSWORD = process.env.ADMIN_PASSWORD || 'correct-horse-battery';
if (!SP) {
  console.error('חסר ארגומנט: נתיב לתיקייה שאליה יישמרו צילומי המסך');
  console.error('שימוש: node scripts/check-offline.js <תיקייה> [כתובת-בסיס]');
  process.exit(1);
}

/* אין רשימת היתרים. הגופנים ירדו ל-assets/fonts ולכן לא נשארה אף בקשה
   שיוצאת החוצה — כל אחת כזאת היא רגרסיה. */

(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1500, height: 1000 } });

  const external = [];
  const errs = [];
  await p.route('**/*', async (route) => {
    const url = route.request().url();
    if (url.startsWith(BASE + '/')) return route.continue();
    external.push(url);
    return route.abort();
  });
  p.on('console', (m) => { if (m.type() === 'error') errs.push(m.text()); });
  p.on('pageerror', (e) => errs.push('PAGEERROR: ' + e.message));

  await p.goto(BASE + '/', { waitUntil: 'load', timeout: 60000 });
  await p.waitForSelector('#dc-root', { timeout: 20000 });
  await p.waitForTimeout(4000);

  // כל מה שנבדק מכאן ואילך נמצא במסך הרפרנס.
  await p.getByRole('button', { name: 'רפרנס', exact: true }).click();
  await p.waitForTimeout(2500);

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
  await p.click('button[title^="מעבר למצב"]');
  await p.waitForTimeout(2500);
  const light = await probe();

  // onChange על שדה קלט. שדה החיפוש של הרפרנס הוא הקישוט היחיד שאינו
  // תלוי ב-API, ולכן הוא זה שנבדק כאן.
  await p.fill('input[placeholder="חיפוש בתיעוד…"]', 'רכיבי');
  await p.waitForTimeout(600);
  const searchWorks = await p.evaluate(
    () => document.querySelector('input[placeholder="חיפוש בתיעוד…"]').value === 'רכיבי'
  );
  await p.fill('input[placeholder="חיפוש בתיעוד…"]', '');
  await p.waitForTimeout(400);

  const refSections = await p.evaluate(() => document.querySelectorAll('[data-sec]').length);
  await p.screenshot({ path: SP + '/offline-ref.png' });

  // onClick על טאב מסך — חזרה לפרויקטים ושוב לרפרנס
  await p.getByRole('button', { name: 'פרויקטים', exact: true }).click();
  await p.waitForTimeout(1500);
  await p.screenshot({ path: SP + '/offline-projects.png' });
  await p.getByRole('button', { name: 'רפרנס', exact: true }).click();
  await p.waitForTimeout(1500);
  const backToReference = await p.evaluate(() => document.querySelectorAll('[data-sec]').length);

  // ── מסמך אמיתי: כאן Prism ו-Mermaid באמת רצים ────────────────────
  // זה החלק שמצדיק את vendor.sh. ה-autoloader של Prism מוריד דקדוק בזמן
  // ריצה לפי השפה שנתקל בה, ו-Mermaid מושך גופנים ותוספים. כל בקשה כזאת
  // תיחסם כאן ותופיע ב-externalRequests, ולכן מסמך שנצבע במלואו בלי אף
  // בקשה חיצונית הוא ההוכחה שהחבילה המקומית שלמה.
  const doc = await p.evaluate(async ([email, password]) => {
    const post = (path, body) =>
      fetch(path, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });

    const login = await post('/api/auth/login', { email, password });
    if (!login.ok) return { error: 'כניסה נכשלה: ' + login.status };

    const name = 'בדיקת נכסים מקומיים ' + Math.floor(performance.now());
    const created = await post('/api/projects', { name });
    if (!created.ok) return { error: 'יצירת פרויקט נכשלה: ' + created.status };
    const slug = (await created.json()).slug;

    const content = [
      '# בדיקת צביעה',
      '',
      '```python',
      'def שלום(שם: str) -> str:',
      '    return f"שלום {שם}"',
      '```',
      '',
      '```sql',
      'SELECT id, title FROM documents WHERE project_id = $1 ORDER BY position, id;',
      '```',
      '',
      '```mermaid',
      'graph TD;  A[בקשה] --> B[שרת];  B --> C[מסד];',
      '```',
      '',
    ].join('\n');

    const added = await post(`/api/projects/${encodeURIComponent(slug)}/docs`, {
      title: 'בדיקת צביעה',
      content,
    });
    if (!added.ok) return { error: 'יצירת מסמך נכשלה: ' + added.status };
    return { slug, name };
  }, [EMAIL, PASSWORD]);

  let rendered = { tokens: 0, languages: [], mermaid: 0 };
  let cleanup = null;
  if (!doc.error) {
    // שני השלבים נחוצים, וכל אחד מסיבה אחרת.
    //
    // מאז שנוסף ניתוב, טעינה מחדש משחזרת את המקום שבו היינו — כאן מסך
    // הרפרנס — ולא נוחתת ברשימת הפרויקטים, ולכן הכתובת מאופסת ל-"#/".
    //
    // אבל שינוי hash לבדו אינו טעינה מחדש: הרשימה שבזיכרון נשארת זו
    // שנטענה לפני שהפרויקט נוצר, והפרויקט החדש אינו בה. reload מריץ
    // את ה-bootstrap מחדש ומביא רשימה עדכנית.
    await p.goto(BASE + '/#/', { waitUntil: 'load' });
    await p.reload({ waitUntil: 'load' });
    await p.waitForSelector('#dc-root', { timeout: 20000 });
    await p.waitForTimeout(3000);
    // נכנסים לפרויקט ורק אז למסמך.
    await p.getByText(doc.name).first().click();
    await p.waitForTimeout(2500);
    await p.getByText('בדיקת צביעה').first().click();
    await p.waitForTimeout(4500);
    rendered = await p.evaluate(() => ({
      tokens: document.querySelectorAll('pre code span.token').length,
      languages: [...new Set([...document.querySelectorAll('pre code[class*="language-"]')]
        .map((el) => el.className.replace(/.*language-/, '').trim()))].sort(),
      mermaid: document.querySelectorAll('[data-mermaid] svg').length,
    }));
    await p.screenshot({ path: SP + '/offline-doc.png' });

    // ניקוי: בלי זה כל הרצה משאירה פרויקט במסד. לא מפיל את התרחיש —
    // כשל מחיקה מדווח בפלט ולא הופך בדיקה שעברה לכישלון.
    cleanup = await p.evaluate(async (slug) => {
      try {
        const res = await fetch('/api/projects/' + encodeURIComponent(slug), {
          method: 'DELETE',
          credentials: 'same-origin',
        });
        return res.status;
      } catch (e) {
        return 'שגיאה: ' + e.message;
      }
    }, doc.slug);
  }

  console.log(JSON.stringify({
    externalRequests: external,
    errs: errs.slice(0, 8),
    dark, light, rendered, doc, cleanup,
    interactive: { themeToggle: dark.theme !== light.theme, searchWorks, refSections, backToReference },
  }, null, 2));

  await b.close();

  const ok =
    external.length === 0 &&
    errs.length === 0 &&
    !doc.error &&
    rendered.tokens > 0 &&
    rendered.mermaid > 0 &&
    dark.theme !== light.theme &&
    searchWorks &&
    // לא מספר קבוע: מקטע תשיעי ברפרנס אינו רגרסיה. מה שנבדק הוא שהמסך
    // חזר לאותו מבנה אחרי מעבר הלוך-חזור.
    refSections > 0 &&
    backToReference === refSections;
  console.log(ok ? '\nהכול עבר' : '\nנכשל');
  process.exit(ok ? 0 : 1);
})().catch((e) => { console.error('FATAL', e.message); process.exit(1); });
