/* בדיקת תחביר לבלוק הלוגיקה שבתוך index.html.
 *
 *   node scripts/check-syntax.js
 *
 * הקוד של הרכיב חי בתוך <script> ב-index.html, ו-dc-runtime מריץ אותו
 * דרך new Function. שגיאת תחביר שם לא מפילה את הדף בקול: ה-runtime תופס
 * אותה, מדפיס אזהרה לקונסולה, ומרנדר את התבנית עם props בלבד — כלומר
 * מסך שנראה כמעט תקין ולא מגיב לכלום.
 *
 * בדיקות הדפדפן כן תופסות את זה, כי הן נכשלות על שגיאת קונסולה. אבל הן
 * דורשות שרת, מסד נתונים ודפדפן, ולוקחות דקה — והבדיקה הזאת עושה את
 * אותו הדבר על סוגר חסר תוך פחות משנייה.
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FILE = path.join(__dirname, '..', 'index.html');
const START = 'class Component extends DCLogic';

const html = fs.readFileSync(FILE, 'utf8');
const from = html.indexOf(START);
if (from < 0) {
  console.error(`לא נמצא "${START}" ב-index.html — הבדיקה הזאת מסתמכת עליו כעוגן`);
  process.exit(1);
}
const to = html.indexOf('</script>', from);
if (to < 0) {
  console.error('לא נמצא סוף ה-script אחרי בלוק הלוגיקה');
  process.exit(1);
}

const source = html.slice(from, to);
/* מספר השורה שמדווח הוא יחסי לתחילת הבלוק. ההיסט מוסיף אליו את השורות
   שלפניו, כדי שהמספר יתאים לקובץ עצמו. */
const offset = html.slice(0, from).split('\n').length - 1;

try {
  new vm.Script(source, { filename: 'index.html' });
} catch (e) {
  const line = (e.stack || '').match(/index\.html:(\d+)/);
  console.error('שגיאת תחביר ב-index.html' + (line ? `:${offset + Number(line[1])}` : ''));
  console.error(e.message);
  process.exit(1);
}

console.log(`התחביר תקין (${source.split('\n').length} שורות לוגיקה)`);
