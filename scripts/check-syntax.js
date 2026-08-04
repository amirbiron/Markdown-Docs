/* בדיקת תחביר לבלוק הלוגיקה שבתוך index.html.
 *
 *   node scripts/check-syntax.js
 *
 * הקוד של הרכיב חי בתוך <script type="text/x-dc" data-dc-script>
 * ב-index.html, ו-dc-runtime מריץ אותו דרך new Function. שגיאת תחביר שם
 * לא מפילה את הדף בקול: ה-runtime תופס אותה, מדפיס אזהרה לקונסולה,
 * ומרנדר את התבנית עם props בלבד — כלומר מסך שנראה כמעט תקין ולא מגיב
 * לכלום.
 *
 * בדיקות הדפדפן כן תופסות את זה, כי הן נכשלות על שגיאת קונסולה. אבל הן
 * דורשות שרת, מסד נתונים ודפדפן, ולוקחות דקה — והבדיקה הזאת עושה את
 * אותו הדבר על סוגר חסר תוך פחות משנייה.
 */

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const FILE = path.join(__dirname, '..', 'index.html');
/* העוגן הוא תגית ה-script עצמה ולא `class Component`.

   העיגון על המחלקה נראה מדויק יותר, והוא היה חור: כל מה שמעליה באותו
   בלוק — הערכות, CHROME, SNIPPETS, MERMAID, הקבועים — לא נבדק כלל.
   שגיאת תחביר שם עוברת את ה-runtime באותה דרך בדיוק, כלומר מרנדרת דף
   שלא מגיב לכלום, ואת הבדיקה הזאת היא הייתה עוברת בשקט. */
const START = '<script type="text/x-dc" data-dc-script';

const html = fs.readFileSync(FILE, 'utf8');
const tag = html.indexOf(START);
if (tag < 0) {
  console.error(`לא נמצאה התגית ${START} ב-index.html — הבדיקה מסתמכת עליה כעוגן`);
  process.exit(1);
}
/* סוף תגית הפתיחה. data-props הוא JSON מקודד ב-entities ואין בו '>' גולמי,
   ולכן הסוגר הראשון שאחרי תחילת התגית הוא הנכון. */
const from = html.indexOf('>', tag) + 1;
if (from === 0) {
  console.error('תגית ה-script לא נסגרה');
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

console.log(`התחביר תקין (${source.split('\n').length} שורות לוגיקה, כל הבלוק)`);
