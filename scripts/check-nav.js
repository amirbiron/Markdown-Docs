/* ניתוב, חזרה מהרפרנס, אישור מחיקה וקישורים חיצוניים.
 *
 *   node scripts/check-nav.js http://127.0.0.1:8070 <תיקיית-צילומים>
 *
 * ארבעת הדברים נבדקים יחד כי שלושה מהם נשענים על אותו מנגנון: הכתובת
 * היא מקור האמת למקום שבו המשתמש נמצא. רענון שמחזיר לאותו מסמך, כפתור
 * אחורה של הדפדפן, וחזרה מהרפרנס — כולם אותה שאלה.
 *
 * המחיקה נבדקת בשלושת השלבים ולא רק באחרון: שהאישור נפתח ומראה את שם
 * המסמך, שביטול באמת לא מוחק, ושאישור כן מוחק. בדיקה שמוודאת רק את
 * האחרון עוברת גם על מודאל שנפתח ומוחק בלי קשר לכפתור שנלחץ.
 */

const { chromium } = require('playwright');
const B='http://127.0.0.1:8070', SP=process.argv[2];
const R=[]; const ok=(l,v,x)=>{R.push(v);console.log(`  ${v?'✓':'✗'}  ${l}${x?'  — '+x:''}`)};
(async () => {
  const br = await chromium.launch();
  const ctx = await br.newContext({viewport:{width:1400,height:1000}});
  const p = await ctx.newPage();
  p.on('pageerror', e => console.log('PAGEERROR', e.message));
  p.on('console', m => { if (m.type()==='error') console.log('CONSOLE', m.text()); });
  const login = async () => {
    await p.getByRole('button', {name:'כניסה'}).click(); await p.waitForTimeout(600);
    await p.fill('input[type="email"]','admin@example.com');
    await p.fill('input[type="password"]','correct-horse-battery');
    await p.getByRole('button',{name:'כניסה',exact:true}).last().click(); await p.waitForTimeout(2400);
  };
  await p.goto(B+'/', {waitUntil:'load'}); await p.waitForSelector('#dc-root'); await p.waitForTimeout(2400);
  await login();
  const slug = await p.evaluate(async () => {
    const post=(u,b)=>fetch(u,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});
    const s=(await (await post('/api/projects',{name:'ניווט '+Math.floor(performance.now())})).json()).slug;
    await post(`/api/projects/${encodeURIComponent(s)}/docs`,{title:'ראשון',content:'# ראשון\n\n[קישור חיצוני](https://example.com) ו-[עוגן](#ראשון).'});
    await post(`/api/projects/${encodeURIComponent(s)}/docs`,{title:'שני',content:'# שני\n\nטקסט.'});
    return s;
  });
  await p.reload({waitUntil:'load'}); await p.waitForSelector('#dc-root'); await p.waitForTimeout(2400);
  await p.locator('a[href="#top"]').filter({hasText:'ניווט'}).first().click(); await p.waitForTimeout(1600);
  await p.getByText('שני', {exact:true}).first().click(); await p.waitForTimeout(1600);

  const hash1 = await p.evaluate(()=>location.hash);
  ok('הכתובת מכילה פרויקט ומסמך', /^#\/[^/]+\/[^/]+$/.test(decodeURI(hash1)), decodeURI(hash1));

  // 1. רענון חוזר לאותו מסמך
  await p.reload({waitUntil:'load'}); await p.waitForSelector('#dc-root'); await p.waitForTimeout(3200);
  const afterReload = await p.evaluate(()=>({h:location.hash, title:(document.querySelector('[data-doc] header h1')||{}).textContent}));
  ok('רענון חוזר לאותו מסמך', (afterReload.title||'').includes('שני'), JSON.stringify(afterReload.title));

  // 2. רפרנס + חזרה
  await p.getByRole('button',{name:'רפרנס'}).click(); await p.waitForTimeout(1400);
  const back = await p.locator('a:has-text("חזרה ל")').first();
  ok('ברפרנס יש כפתור חזרה', await back.isVisible(), (await back.textContent()||'').trim());
  await back.click(); await p.waitForTimeout(1400);
  ok('החזרה מגיעה למסמך', ((await p.evaluate(()=>(document.querySelector('[data-doc] header h1')||{}).textContent))||'').includes('שני'));

  // כפתור אחורה של הדפדפן
  await p.getByRole('button',{name:'רפרנס'}).click(); await p.waitForTimeout(1200);
  await p.goBack(); await p.waitForTimeout(1600);
  ok('אחורה של הדפדפן עובד', ((await p.evaluate(()=>(document.querySelector('[data-doc] header h1')||{}).textContent))||'').includes('שני'));

  // 4. קישורים
  await p.getByText('ראשון', {exact:true}).first().click(); await p.waitForTimeout(1600);
  const links = await p.evaluate(()=>[...document.querySelectorAll('[data-doc] p a')].map(a=>({href:a.getAttribute('href'),t:a.getAttribute('target')})));
  ok('קישור חיצוני נפתח בכרטיסייה חדשה', links.some(l=>/^https/.test(l.href)&&l.t==='_blank'), JSON.stringify(links));
  ok('עוגן פנימי נשאר באותה לשונית', links.some(l=>/^#/.test(l.href)&&!l.t), JSON.stringify(links));

  // 3. מודאל מחיקה
  const before = await p.evaluate(()=>[...document.querySelectorAll('[data-doc]')].length);
  await p.locator('button[title="מחיקה"]').first().click(); await p.waitForTimeout(700);
  const modal = await p.evaluate(()=>{const b=[...document.querySelectorAll('button')].find(x=>x.textContent.trim()==='מחיקה'&&x.offsetWidth>60);
    return {open:!!b, text:document.body.innerText.includes('למחוק את המסמך?'), name:document.body.innerText.includes('ראשון')};});
  ok('לחיצה על × פותחת אישור עם שם המסמך', modal.open && modal.text && modal.name, JSON.stringify(modal));
  await p.getByRole('button',{name:'ביטול'}).click(); await p.waitForTimeout(700);
  const still = await p.evaluate(()=>document.body.innerText.includes('ראשון'));
  ok('ביטול לא מוחק', still);
  await p.locator('button[title="מחיקה"]').first().click(); await p.waitForTimeout(600);
  await p.locator('button:has-text("מחיקה")').last().click(); await p.waitForTimeout(2000);
  const docsLeft = await p.evaluate(async(s)=>{const r=await (await fetch('/api/projects/'+encodeURIComponent(s),{credentials:'same-origin'})).json(); return r.documents.map(d=>d.title);}, slug);
  ok('אישור אכן מוחק', docsLeft.length===1, JSON.stringify(docsLeft));

  await p.evaluate(async (s)=>{ await fetch('/api/projects/'+encodeURIComponent(s),{method:'DELETE',credentials:'same-origin'}); }, slug);
  await br.close();
  console.log(R.every(Boolean) ? '\nהכול עבר' : `\nנכשלו ${R.filter(x=>!x).length}`);
  process.exit(R.every(Boolean)?0:1);
})().catch(e=>{console.error('FATAL',e.message);process.exit(1)});
