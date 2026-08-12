/* עריכת קישור, ואייקון השמירה.
 *
 *   node scripts/check-links.js http://127.0.0.1:8070 <תיקיית-צילומים>
 *
 * שני הדברים נבדקים יחד כי שניהם נגעו באותה שורת כפתורים.
 *
 * העדכון נמדד מול השרת ולא מול המסך: טופס שמראה את הערך החדש נראה תקין
 * גם כשהוא יצר קישור שני במקום לעדכן את הראשון, ורק ספירת הקישורים
 * מבדילה בין השניים.
 *
 * אייקון השמירה נבדק לפי נקודת הקוד ולא לפי המראה. גליף חסר מרונדר
 * כריבוע במכשיר אחד ומרונדר כרגיל במכשיר אחר, ולכן מה שנבדק הוא שהתו
 * הוא זה שנבחר — U+2713 מגוש Dingbats — ולא איך הוא נראה במכולה הזאת.
 */

const { chromium } = require('playwright');
const B='http://127.0.0.1:8070', SP=process.argv[2];
const R=[]; const ok=(l,v,x)=>{R.push(v);console.log(`  ${v?'✓':'✗'}  ${l}${x?'  — '+x:''}`)};
(async () => {
  const br = await chromium.launch();
  const p = await (await br.newContext({viewport:{width:1400,height:1000}})).newPage();
  p.on('pageerror', e => console.log('PAGEERROR', e.message));
  p.on('console', m => { if (m.type()==='error') console.log('CONSOLE', m.text()); });
  await p.goto(B+'/', {waitUntil:'load'}); await p.waitForSelector('#dc-root'); await p.waitForTimeout(2400);
  await p.getByRole('button', {name:'כניסה'}).click(); await p.waitForTimeout(600);
  await p.fill('input[type="email"]','admin@example.com');
  await p.fill('input[type="password"]','correct-horse-battery');
  await p.getByRole('button',{name:'כניסה',exact:true}).last().click(); await p.waitForTimeout(2400);
  const slug = await p.evaluate(async () => {
    const post=(u,b)=>fetch(u,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});
    const s=(await (await post('/api/projects',{name:'קישורים '+Math.floor(performance.now())})).json()).slug;
    await post(`/api/projects/${encodeURIComponent(s)}/docs`,{title:'מסמך',content:'# מסמך\n\nטקסט.'});
    await post(`/api/projects/${encodeURIComponent(s)}/links`,{title:'שם ישן',url:'https://old.example.com/a'});
    return s;
  });
  await p.reload({waitUntil:'load'}); await p.waitForSelector('#dc-root'); await p.waitForTimeout(2400);
  await p.locator('a[href="#top"]').filter({hasText:'קישורים'}).first().click(); await p.waitForTimeout(1600);

  // אייקון השמירה
  const before = await p.locator('button[title="עריכה"]').last().textContent();
  await p.locator('button[title="עריכה"]').last().click(); await p.waitForTimeout(1400);
  const saveGlyph = await p.evaluate(()=>{const b=document.querySelector('button[title="שמירה"]');
    if(!b) return null; const r=b.getBoundingClientRect();
    return {glyph:b.textContent.trim(), code:'U+'+b.textContent.trim().codePointAt(0).toString(16).toUpperCase(), w:Math.round(r.width)};});
  ok('אייקון השמירה אינו הדיסקט הישן', saveGlyph && saveGlyph.code==='U+2713', JSON.stringify(saveGlyph));
  await p.locator('button[title="שמירה"]').click(); await p.waitForTimeout(1200);

  // עריכת קישור
  const pencils = await p.locator("button[title=\"עריכת הקישור\"]").count();
  ok("יש כפתור עיפרון ליד הקישור", pencils === 1, pencils + ' כפתורי עריכה בעמוד');
  await p.locator("button[title=\"עריכת הקישור\"]").first().click(); await p.waitForTimeout(700);
  const loaded = await p.evaluate(()=>({
    title:document.querySelector('input[placeholder="שם הקישור"]').value,
    url:document.querySelector('input[placeholder="https://…"]').value,
    btn:[...document.querySelectorAll('button')].some(b=>b.textContent.trim()==='עדכון'),
    cancel:[...document.querySelectorAll('button')].some(b=>b.textContent.trim()==='ביטול'),
  }));
  ok('הלחיצה טוענת את הקישור לטופס', loaded.title==='שם ישן' && loaded.url==='https://old.example.com/a', JSON.stringify(loaded));
  ok('הכפתור הפך ל"עדכון" ונוסף ביטול', loaded.btn && loaded.cancel);

  await p.fill('input[placeholder="שם הקישור"]','שם חדש');
  await p.fill('input[placeholder="https://…"]','https://new.example.com/b');
  await p.locator('button:has-text("עדכון")').click(); await p.waitForTimeout(2000);
  const links = await p.evaluate(async(s)=>{const r=await(await fetch('/api/projects/'+encodeURIComponent(s),{credentials:'same-origin'})).json();
    return r.links.map(l=>({t:l.title,u:l.url}));}, slug);
  ok('העדכון שינה ולא יצר חדש', links.length===1 && links[0].t==='שם חדש' && links[0].u==='https://new.example.com/b', JSON.stringify(links));
  const reset = await p.evaluate(()=>({t:document.querySelector('input[placeholder="שם הקישור"]').value,
    btn:[...document.querySelectorAll('button')].some(b=>b.textContent.trim()==='הוספה')}));
  ok('הטופס חזר למצב הוספה', reset.t==='' && reset.btn, JSON.stringify(reset));

  // ביטול
  await p.locator("button[title=\"עריכת הקישור\"]").first().click(); await p.waitForTimeout(600);
  await p.locator('button:has-text("ביטול")').click(); await p.waitForTimeout(600);
  const afterCancel = await p.evaluate(()=>({t:document.querySelector('input[placeholder="שם הקישור"]').value,
    btn:[...document.querySelectorAll('button')].some(b=>b.textContent.trim()==='הוספה')}));
  ok('ביטול מנקה וחוזר להוספה', afterCancel.t==='' && afterCancel.btn, JSON.stringify(afterCancel));

  await p.evaluate(async (s)=>{ await fetch('/api/projects/'+encodeURIComponent(s),{method:'DELETE',credentials:'same-origin'}); }, slug);
  await br.close();
  console.log(R.every(Boolean)?'\nהכול עבר':`\nנכשלו ${R.filter(x=>!x).length}`);
  process.exit(R.every(Boolean)?0:1);
})().catch(e=>{console.error('FATAL',e.message);process.exit(1)});
