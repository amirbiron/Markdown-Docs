/* ניגודיות בתרשימי Mermaid, בכל משפחת תרשים ובכל ערכה.
 *
 *   node scripts/check-mermaid.js http://127.0.0.1:8070 <תיקיית-צילומים>
 *
 * הבדיקה שהייתה קיימת ספרה כמה SVG נוצרו. תרשים שנוצר במלואו ושכל
 * הטקסט בו לבן על לבן עובר אותה בשלמות — וזה בדיוק מה שקרה: paintSvg
 * הכירה רק צורות של flowchart, ולכן קופסאות ER ו-sequence נשארו עם
 * הפלטה הבהירה של Mermaid בזמן שהטקסט נצבע בצבע הטקסט של הערכה הכהה.
 *
 * לכן נמדד כאן היחס בפועל: לכל טקסט מאתרים את הצורה הצבועה שמתחתיו
 * ומחשבים contrast ratio. סף של 3:1 הוא הרף של WCAG לטקסט גדול, והוא
 * נבחר כאן כי תוויות בתרשים הן קצרות ומודגשות — מה שנפל בבאג הזה היה
 * יחס של 1.0, כלומר אותו צבע בדיוק.
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
  console.error('שימוש: node scripts/check-mermaid.js <כתובת-בסיס> <תיקיית-צילומים>');
  process.exit(1);
}

const EMAIL = process.env.ADMIN_EMAIL || 'admin@example.com';
const PASSWORD = process.env.ADMIN_PASSWORD || 'correct-horse-battery';
const NAME = 'בדיקת תרשימים ' + process.pid;
const MIN_RATIO = 3;

const results = [];
const check = (label, ok, extra) => {
  results.push({ label, ok, extra });
  console.log(`  ${ok ? '✓' : '✗'}  ${label}${extra ? '  — ' + extra : ''}`);
};

/* משפחה אחת לכל סוג רינדור ב-Mermaid. ER ו-sequence אינם קישוט ברשימה
   הזאת — הם שתי המשפחות שנשברו, והשאר כאן כדי שתיקון עתידי באחת לא
   ישבור בשקט אחרת. */
const DIAGRAMS = [
  ['flowchart', ['flowchart TB', '  A["בקשה"] --> B["שרת"]', '  B --> C[("מסד")]']],
  ['ER', ['erDiagram', '  users ||--o{ projects : "owner_id"', '  users {', '    uuid id PK', '    string email UK', '  }', '  projects {', '    uuid id PK', '    string slug', '  }']],
  ['class', ['classDiagram', '  class Backup {', '    +stream()', '    +encrypt()', '  }', '  Backup <|-- Scheduler']],
  ['state', ['stateDiagram-v2', '  [*] --> טיוטה', '  טיוטה --> פורסם', '  פורסם --> [*]']],
  ['sequence', ['sequenceDiagram', '  לקוח->>שרת: בקשה', '  שרת-->>לקוח: תשובה']],
];

const DOC = ['# תרשימים', ''].concat(
  DIAGRAMS.flatMap(([name, lines]) => ['## ' + name, '', '```mermaid'].concat(lines, ['```', '']))
).join('\n');

/* הפונקציה רצה בתוך הדפדפן, ולכן היא עצמאית לגמרי. */
function measure(minRatio) {
  const lum = (css) => {
    const m = String(css).match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(',').map((x) => parseFloat(x));
    if (p.length > 3 && p[3] === 0) return null;      // שקוף — לא רקע
    const ch = p.slice(0, 3).map((v) => {
      const c = v / 255;
      return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2];
  };
  const ratio = (a, b) => (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);

  const out = [];
  document.querySelectorAll('[data-mermaid] svg').forEach((svg, i) => {
    /* כל הצורות הצבועות, בסדר המסמך. ב-SVG מה שמאוחר יותר מצויר מעל,
       ולכן הרקע האפקטיבי של טקסט הוא הצורה האחרונה שמכילה אותו. */
    const shapes = [...svg.querySelectorAll('rect, polygon, circle, ellipse, path')]
      .map((el) => ({ el, fill: getComputedStyle(el).fill, box: el.getBoundingClientRect() }))
      .filter((s) => lum(s.fill) !== null && s.box.width > 0 && s.box.height > 0);

    /* רקע הכרטיס עצמו, כשאין שום צורה מתחת לטקסט. */
    const host = svg.closest('[data-mermaid]').parentElement;
    const pageBg = getComputedStyle(host).backgroundColor;

    svg.querySelectorAll('text, tspan').forEach((t) => {
      const txt = (t.textContent || '').trim();
      if (!txt) return;
      if (t.querySelector('tspan')) return;              // נמדד ב-tspan הפנימי
      const b = t.getBoundingClientRect();
      if (!b.width || !b.height) return;
      const cx = b.left + b.width / 2, cy = b.top + b.height / 2;

      let bg = pageBg;
      for (const s of shapes) {
        if (cx >= s.box.left && cx <= s.box.right && cy >= s.box.top && cy <= s.box.bottom) bg = s.fill;
      }
      const lt = lum(getComputedStyle(t).fill), lb = lum(bg);
      if (lt === null || lb === null) return;
      const r = ratio(lt, lb);
      if (r < minRatio) out.push({ i, text: txt.slice(0, 24), fg: getComputedStyle(t).fill, bg, ratio: Math.round(r * 100) / 100 });
    });
  });
  return out;
}

(async () => {
  const browser = await chromium.launch();
  const errs = [];
  let slug = null;

  for (const theme of ['dark', 'dim', 'light', 'coast', 'coast-dark']) {
    const ctx = await browser.newContext({ viewport: { width: 1500, height: 1000 } });
    const p = await ctx.newPage();
    p.on('console', (m) => { if (m.type() === 'error') errs.push(`${theme}: ${m.text()}`); });
    p.on('pageerror', (e) => errs.push(`${theme} PAGEERROR: ${e.message}`));
    await p.addInitScript((t) => localStorage.setItem('md-docs-theme', t), theme);

    await p.goto(BASE + '/', { waitUntil: 'load' });
    await p.waitForSelector('#dc-root', { timeout: 20000 });
    await p.waitForTimeout(2500);
    await p.getByRole('button', { name: 'כניסה' }).click();
    await p.waitForTimeout(600);
    await p.fill('input[type="email"]', EMAIL);
    await p.fill('input[type="password"]', PASSWORD);
    await p.getByRole('button', { name: 'כניסה', exact: true }).last().click();
    await p.waitForTimeout(2500);

    if (!slug) {
      slug = await p.evaluate(async ([name, content]) => {
        const post = (u, b) => fetch(u, { method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(b) });
        const pr = await post('/api/projects', { name });
        if (!pr.ok) return null;
        const s = (await pr.json()).slug;
        const d = await post(`/api/projects/${encodeURIComponent(s)}/docs`, { title: 'תרשימים', content });
        return d.ok ? s : null;
      }, [NAME, DOC]);
      if (!slug) { console.error('FATAL הכנת המסמך נכשלה'); await browser.close(); process.exit(1); }
      await p.reload({ waitUntil: 'load' });
      await p.waitForSelector('#dc-root', { timeout: 20000 });
      await p.waitForTimeout(2500);
    }

    await p.getByText(NAME).first().click();
    await p.waitForTimeout(6000);        // Mermaid מרנדר אסינכרונית

    const rendered = await p.evaluate(() => document.querySelectorAll('[data-mermaid] svg').length);
    const active = await p.evaluate(() => document.documentElement.getAttribute('data-theme'));
    check(`${theme}: כל התרשימים מצוירים`, rendered === 5, `${rendered}/5 · data-theme=${active}`);

    const bad = await p.evaluate(measure, MIN_RATIO);
    check(`${theme}: כל טקסט קריא על הרקע שמתחתיו`,
      bad.length === 0,
      bad.length ? JSON.stringify(bad.slice(0, 3)) : `סף ${MIN_RATIO}:1`);

    await p.screenshot({ path: `${SHOTS}/mermaid-${theme}.png`, fullPage: true });
    await ctx.close();
  }

  check('אין שגיאות קונסולה', errs.length === 0, errs.slice(0, 2).join(' | '));

  const cleanup = await browser.newContext();
  const c = await cleanup.newPage();
  await c.goto(BASE + '/', { waitUntil: 'load' });
  const removed = await c.evaluate(async ([email, password, s]) => {
    await fetch('/api/auth/login', { method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email, password }) });
    const r = await fetch('/api/projects/' + encodeURIComponent(s), { method: 'DELETE', credentials: 'same-origin' });
    return r.status;
  }, [EMAIL, PASSWORD, slug]);
  check('הפרויקט נמחק בסיום', removed === 204, 'status ' + removed);

  await browser.close();
  const failed = results.filter((r) => !r.ok);
  console.log(failed.length ? `\nנכשלו ${failed.length}` : '\nהכול עבר');
  process.exit(failed.length ? 1 : 0);
})().catch((e) => { console.error('FATAL', e.message); process.exit(1); });
