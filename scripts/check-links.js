/* עריכת קישור, ואייקון השמירה.
 *
 *   node scripts/check-links.js http://127.0.0.1:8070
 *
 * הכתובת היא ארגומנט ואינה קבועה בקוד. גרסה קודמת קיבעה אותה בזמן
 * שכותרת השימוש הבטיחה פרמטר, כלומר הרצה מול שרת אחר הייתה מדווחת
 * ירוק על השרת המקומי — בדיקה שמודדת משהו אחר ממה שביקשו ממנה.
 *
 * שני הדברים נבדקים יחד כי שניהם נגעו באותה שורת כפתורים.
 *
 * העדכון נמדד גם מול השרת וגם מול המסך, ושני המדדים אינם כפילות: טופס
 * שמראה את הערך החדש נראה תקין גם כשהוא יצר קישור שני במקום לעדכן את
 * הראשון (רק ספירת הקישורים מבדילה), ולהיפך — שרת שעודכן כראוי לא
 * מבטיח שהשורה במסך התרעננה.
 *
 * אייקון השמירה נבדק לפי נקודת הקוד ולא לפי המראה. גליף חסר מרונדר
 * כריבוע במכשיר אחד ומרונדר כרגיל במכשיר אחר, ולכן מה שנבדק הוא שהתו
 * הוא זה שנבחר — U+2713 מגוש Dingbats — ולא איך הוא נראה במכולה הזאת.
 *
 * הניקוי יושב ב-finally: בדיקה שנופלת באמצע השאירה פרויקטים במסד ואת
 * הדפדפן פתוח, וההרצה הבאה מצאה מסך עמוס בשאריות של הקודמת.
 */

const BASE = (process.argv[2] || '').replace(/\/+$/, '');
if (!BASE) {
  console.error('שימוש: node scripts/check-links.js <כתובת-בסיס>');
  process.exit(1);
}

const { chromium } = require('playwright');
const B = BASE;
const R=[]; const ok=(l,v,x)=>{R.push(v);console.log(`  ${v?'✓':'✗'}  ${l}${x?'  — '+x:''}`)};
(async () => {
  const br = await chromium.launch();
  const p = await (await br.newContext({viewport:{width:1400,height:1000}})).newPage();
  const created = [];
  try {
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
  created.push(slug);
  await p.reload({waitUntil:'load'}); await p.waitForSelector('#dc-root'); await p.waitForTimeout(2400);
  await p.locator('a[href="#top"]').filter({hasText:'קישורים'}).first().click(); await p.waitForTimeout(1600);

  // אייקון השמירה
  await p.locator('button[title="עריכה"]').last().click(); await p.waitForTimeout(1400);
  const saveGlyph = await p.evaluate(()=>{const b=document.querySelector('button[title="שמירה"]');
    if(!b) return null; const r=b.getBoundingClientRect();
    return {glyph:b.textContent.trim(), code:'U+'+b.textContent.trim().codePointAt(0).toString(16).toUpperCase(), w:Math.round(r.width)};});
  ok('אייקון השמירה אינו הדיסקט הישן', saveGlyph && saveGlyph.code==='U+2713', JSON.stringify(saveGlyph));
  await p.locator('button[title="שמירה"]').click(); await p.waitForTimeout(1200);

  // עריכת קישור
  const pencils = await p.locator("button[title=\"עריכת הקישור\"]").count();
  ok("יש כפתור עיפרון ליד הקישור", pencils === 1, pencils + ' כפתורי עריכה בעמוד');
  // התווית הנגישה נושאת את שם הקישור: ברשימה כל השורות נושאות את אותם
  // שני כפתורים, ובלי השם קורא מסך מקריא אותן זהות.
  const pencilLabel = await p.locator('button[title="עריכת הקישור"]').first().getAttribute('aria-label');
  ok('התווית הנגישה כוללת את שם הקישור', pencilLabel === 'עריכת הקישור שם ישן', String(pencilLabel));
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
  // אותו עדכון, מהצד של המסך: שרת שעודכן ושורה שלא התרעננה הם שני
  // כשלים שונים, ומדד אחד לא רואה את השני.
  const row = await p.evaluate(()=>{
    const a=[...document.querySelectorAll('a[target="_blank"]')].find(x=>x.textContent.trim()==='שם חדש');
    return a ? {text:a.textContent.trim(), href:a.getAttribute('href')} : null;
  });
  ok('השורה במסך מציגה את הערכים החדשים',
    !!row && row.href==='https://new.example.com/b', JSON.stringify(row));
  const reset = await p.evaluate(()=>({t:document.querySelector('input[placeholder="שם הקישור"]').value,
    u:document.querySelector('input[placeholder="https://…"]').value,
    btn:[...document.querySelectorAll('button')].some(b=>b.textContent.trim()==='הוספה')}));
  ok('הטופס חזר למצב הוספה', reset.t==='' && reset.u==='' && reset.btn, JSON.stringify(reset));

  // ביטול
  await p.locator("button[title=\"עריכת הקישור\"]").first().click(); await p.waitForTimeout(600);
  await p.locator('button:has-text("ביטול")').click(); await p.waitForTimeout(600);
  const afterCancel = await p.evaluate(()=>({t:document.querySelector('input[placeholder="שם הקישור"]').value,
    u:document.querySelector('input[placeholder="https://…"]').value,
    btn:[...document.querySelectorAll('button')].some(b=>b.textContent.trim()==='הוספה')}));
  ok('ביטול מנקה וחוזר להוספה', afterCancel.t==='' && afterCancel.u==='' && afterCancel.btn, JSON.stringify(afterCancel));

  // ── מצב עריכה שלא שורד את מה שביטל אותו ───────────────────────────
  // editLinkId לבדו שרד מעבר לפרויקט אחר ומחיקה של הקישור שנערך, ואז
  // הטופס נשאר ב"עדכון" וה-PATCH יצא למזהה שאינו קיים. שני התרחישים
  // נבדקים כאן, כי הם מגיעים משני מסלולים שונים לגמרי.

  // א: מחיקת הקישור שנערך
  await p.locator('button[title="עריכת הקישור"]').first().click(); await p.waitForTimeout(600);
  // לפי aria-label ולא לפי title: כפתור המחיקה של המסמך נושא את אותו
  // title, ובחירה לפי title לבדו פתחה כאן את מודאל מחיקת המסמך.
  await p.locator('button[aria-label^="מחיקת הקישור"]').first().click(); await p.waitForTimeout(700);
  await p.locator('button:has-text("מחיקה")').last().click(); await p.waitForTimeout(2000);
  const afterDelete = await p.evaluate(()=>[...document.querySelectorAll('button')].some(b=>b.textContent.trim()==='הוספה'));
  ok('מחיקת הקישור שנערך מחזירה את הטופס להוספה', afterDelete);

  // ב: מעבר לפרויקט אחר תוך כדי עריכה
  const other = await p.evaluate(async () => {
    const post=(u,b)=>fetch(u,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)});
    const s=(await (await post('/api/projects',{name:'פרויקט שני '+Math.floor(performance.now())})).json()).slug;
    await post(`/api/projects/${encodeURIComponent(s)}/links`,{title:'קישור אחר',url:'https://other.example.com'});
    return s;
  });
  created.push(other);
  // התרחיש הקודם מחק את הקישור היחיד של הפרויקט הראשון, ולכן צריך
  // קישור חדש כדי שיהיה מה להתחיל לערוך כאן.
  await p.evaluate(async (s)=>{ await fetch('/api/projects/'+encodeURIComponent(s)+'/links',
    {method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},
     body:JSON.stringify({title:'קישור חוזר',url:'https://again.example.com'})}); }, slug);
  await p.reload({waitUntil:'load'}); await p.waitForSelector('#dc-root'); await p.waitForTimeout(2600);
  await p.locator('button[title="עריכת הקישור"]').first().click(); await p.waitForTimeout(600);
  await p.getByRole('button',{name:'פרויקטים',exact:true}).click(); await p.waitForTimeout(1400);
  await p.locator('a[href="#top"]').filter({hasText:'פרויקט שני'}).first().click(); await p.waitForTimeout(1800);
  const afterSwitch = await p.evaluate(()=>({
    add:[...document.querySelectorAll('button')].some(b=>b.textContent.trim()==='הוספה'),
    empty:document.querySelector('input[placeholder="שם הקישור"]').value === '',
  }));
  ok('מעבר פרויקט מנקה את מצב העריכה', afterSwitch.add && afterSwitch.empty, JSON.stringify(afterSwitch));

  } finally {
    // הניקוי רץ גם כשמשהו נפל באמצע. כשל מחיקה מדווח ואינו מסתיר את
    // השגיאה המקורית.
    for (const s of created.reverse()) {
      const st = await p.evaluate(async (x)=>{
        try { return (await fetch('/api/projects/'+encodeURIComponent(x),{method:'DELETE',credentials:'same-origin'})).status; }
        catch (e) { return 'שגיאה: '+e.message; }
      }, s).catch(e => 'שגיאה: '+e.message);
      if (st !== 204) console.log('  !  ניקוי הפרויקט ' + s + ' החזיר ' + st);
    }
    await br.close();
  }
  console.log(R.every(Boolean)?'\nהכול עבר':`\nנכשלו ${R.filter(x=>!x).length}`);
  process.exit(R.every(Boolean)?0:1);
})().catch(e=>{console.error('FATAL',e.message);process.exit(1)});
