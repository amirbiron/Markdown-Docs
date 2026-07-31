
מצאתי הכל. הנה סיכום קצר ואז כל הקוד

הפיצ'ר בנוי משני חלקים: **JS משותף אחד** (`rtl-code.js`) שמזהה עברית ומוסיף `direction`/מחלקת `rtl-code`, ו-**CSS שמשוכפל בשלושה הקשרי תצוגה** של מארקדאון בווב אפ: `#md-content` (דף תצוגת MD), `.markdown-preview-content` (דפדפן הריפו), ו-`.split-preview-content` (תצוגה חיה/מפוצלת). ה-`unicode-bidi` שקיים בקוד שייך לפיצ'ר אחר (ספריית snippets) ולא לזה — אציין אותו בנפרד בסוף.

---

## 1. ה-JS — הליבה המשותפת

`webapp/static/js/utils/rtl-code.js` (הקובץ המלא, verbatim):

```javascript
/**
 * זיהוי ויישור RTL לבלוקי קוד עם תוכן בעברית.
 * משותף ל-live-preview, repo-browser ו-md_preview.
 */
(function () {
  'use strict';

  var HEBREW_THRESHOLD = 0.3;

  function isHebrewMajority(text) {
    if (!text) return false;
    var cleaned = text.replace(/\s+/g, '');
    if (cleaned.length === 0) return false;
    var hebrewCount = 0;
    var letterCount = 0;
    for (var i = 0; i < cleaned.length; i++) {
      var c = cleaned.charCodeAt(i);
      if (c >= 0x0590 && c <= 0x05FF) {
        hebrewCount++;
        letterCount++;
      } else if ((c >= 0x0041 && c <= 0x005A) || (c >= 0x0061 && c <= 0x007A)) {
        // אותיות לטיניות (A-Z, a-z)
        letterCount++;
      }
      // סימנים, חיצים, מספרים וסימני פיסוק לא נספרים – כך הם לא מדללים את היחס
    }
    if (letterCount === 0) return false;
    return hebrewCount / letterCount > HEBREW_THRESHOLD;
  }

  function hasExplicitLanguage(block) {
    var cls = block.className || '';
    // שפות שאינן שפות תכנות אמיתיות – לא חוסמות זיהוי RTL
    return /\blanguage-(?!plaintext\b|text\b|nohighlight\b|none\b|txt\b)\S+/.test(cls);
  }

  /**
   * בודק אם בלוק קוד מכיל תוכן בעברית ללא שפת תכנות מוגדרת,
   * ומחיל direction: rtl + class rtl-code, או מסיר אותם אם לא.
   *
   * חשוב: יש לקרוא לפונקציה *לפני* hljs.highlightElement כדי שהבדיקה
   * תתבסס על ה-class המקורי ולא על זיהוי אוטומטי של hljs.
   */
  function applyRtlIfHebrew(block) {
    var parent = block.closest('pre');
    if (!parent) return false;

    var isHebrew = !hasExplicitLanguage(block) && isHebrewMajority(block.textContent);

    parent.style.direction = isHebrew ? 'rtl' : 'ltr';
    parent.style.textAlign = isHebrew ? 'right' : 'left';

    if (isHebrew) {
      parent.classList.add('rtl-code');
    } else {
      parent.classList.remove('rtl-code');
    }

    return isHebrew;
  }

  if (typeof window !== 'undefined') {
    window.RtlCode = {
      isHebrewMajority: isHebrewMajority,
      hasExplicitLanguage: hasExplicitLanguage,
      applyRtlIfHebrew: applyRtlIfHebrew,
    };
  }
})();
```

### נקודות הקריאה ל-JS (איפה זה מופעל)

`webapp/static/js/live-preview.js:299-305` (בתוך `highlightBlocks`):

```javascript
      root.querySelectorAll('pre code').forEach((block) => {
        try {
          const parent = block.closest('pre');
          // בדיקת עברית *לפני* hljs כדי שלא ישתנה ה-class
          if (window.RtlCode) {
            window.RtlCode.applyRtlIfHebrew(block);
          }
```

`webapp/templates/md_preview.html:2773-2780`:

```javascript
    // יישור RTL לבלוקי קוד עם תוכן בעברית – *לפני* hljs כדי שהבדיקה תתבסס על class מקורי
    try {
      if (window.RtlCode) {
        container.querySelectorAll('pre code').forEach(function(block) {
          window.RtlCode.applyRtlIfHebrew(block);
        });
      }
    } catch(_) {}
```

טעינת הסקריפט נעשית ב-`webapp/templates/md_preview.html:2104` וב-`webapp/templates/repo/base_repo.html:394`:

```html
<script src="{{ url_for('static', filename='js/utils/rtl-code.js') }}"></script>
```

---

## 2. ה-CSS — שלושה הקשרי תצוגה

### A. דף תצוגת MD — `#md-content` (בתוך `<style>` של `webapp/templates/md_preview.html`)

`md_preview.html:882-894` (code בתוך pre — `white-space` / `word-break` / `overflow-wrap`):

```css
#md-content pre code,
#md-content pre > code,
#md-content code.hljs {
  display: block;
  padding: 0;
  border-radius: 0;
  font-family: 'Fira Code', 'Consolas', monospace;
  font-size: 16px;
  line-height: 1.5;
  white-space: pre !important;
  word-break: normal !important;
  overflow-wrap: normal !important;
}
```

`md_preview.html:902-915` (ה-`pre` עצמו — `overflow-x`):

```css
#md-content pre {
  overflow-x: auto;
  max-width: 100%;
  width: 100%;
  margin: 1rem 0;
  background: var(--md-code-bg);
  color: var(--md-code-text);
  border: 0;
  border-radius: 12px;
  border-top: 1px solid var(--md-code-border);
  padding: 1.25rem clamp(1.5rem, 6vw, 2.25rem) 1.5rem;
  box-shadow: var(--md-code-shadow);
  box-sizing: border-box;
}
```

`md_preview.html:916-926` (העטיפה `.code-block` — ברירת מחדל LTR):

```css
#md-content .code-block {
  margin: 1.5rem 0;
  border: 1px solid var(--md-code-border);
  border-radius: 12px;
  overflow: hidden;
  background: var(--md-code-shell-bg, var(--md-code-bg));
  box-shadow: var(--md-code-shadow);
  position: relative;
  direction: ltr;
  text-align: left;
}
```

`md_preview.html:927-931` (**מחלקת ה-RTL**):

```css
/* בלוקי קוד עם תוכן בעברית – יישור לימין */
#md-content pre.rtl-code {
  direction: rtl;
  text-align: right;
}
```

`md_preview.html:939` (`.hljs` — `overflow-x`):

```css
#md-content .hljs { overflow-x: auto; display: block; }
```

> הערה: בקובץ הזה ה-`direction: ltr`/`text-align: left` של מצב ברירת המחדל לבלוק נקבע ב-JS דרך `parent.style` (ראה `applyRtlIfHebrew`), לא ב-CSS של `#md-content pre`. ה-CSS רק מספק את ה-override ל-RTL דרך `pre.rtl-code`.

### B. דפדפן הריפו — `.markdown-preview-content` (`webapp/static/css/repo-browser.css`)

`repo-browser.css:2739-2774`:

```css
/* בלוקי קוד */
.markdown-preview-content pre {
    background: var(--code-bg, #22272e);
    border: 1px solid var(--border-color);
    border-radius: 6px;
    padding: 16px;
    overflow-x: auto;
    direction: ltr; /* קוד תמיד LTR */
    text-align: left;
}

/* בלוקי קוד עם תוכן בעברית – יישור לימין */
.markdown-preview-content pre.rtl-code {
    direction: rtl;
    text-align: right;
}

.markdown-preview-content pre code {
    font-family: 'JetBrains Mono', 'Fira Code', monospace;
    font-size: 0.9em;
    color: #adbac7;
    background: transparent !important;
    padding: 0;
}

.markdown-preview-content code {
    font-family: 'JetBrains Mono', 'Fira Code', monospace;
    font-size: 0.9em;
}

.markdown-preview-content :not(pre) > code {
    background: var(--bg-secondary);
    padding: 2px 6px;
    border-radius: 4px;
    color: #f47067;
}
```

`repo-browser.css:2828-2833` (code.hljs בתוך pre — `overflow-x`):

```css
.markdown-preview-content pre code.hljs {
    display: block;
    overflow-x: auto;
    padding: 0; /* padding is on parent pre element */
    background: transparent !important;
}
```

### C. תצוגה חיה / מפוצלת — `.split-preview-content` (`webapp/static/css/split-view.css`)

`split-view.css:447-478`:

```css
.split-preview-content :not(pre) > code,
.split-preview-content code.inline-code {
  display: inline-block;
  padding: 0.15em 0.35em;
  border-radius: 6px;
  font-size: 0.94em;
  background: var(--split-preview-code-bg);
  color: var(--split-preview-code-text);
  word-break: break-word;
}
```

```css
.split-preview-content pre,
.split-preview-content code {
  direction: ltr;
  text-align: left;
  font-family: 'JetBrains Mono', 'Fira Code', Consolas, monospace;
}

html[dir="rtl"] .split-preview-content pre,
html[dir="rtl"] .split-preview-content code {
  direction: ltr;
  text-align: left;
}

/* בלוקי קוד עם תוכן בעברית – יישור לימין */
.split-preview-content pre.rtl-code,
.split-preview-content pre.rtl-code code,
html[dir="rtl"] .split-preview-content pre.rtl-code,
html[dir="rtl"] .split-preview-content pre.rtl-code code {
  direction: rtl;
  text-align: right;
}
```

---

## הערה: `unicode-bidi` — מחוץ לפיצ'ר הזה

המאפיין היחיד `unicode-bidi` בכל הווב אפ נמצא ב-`webapp/static/css/snippets.css:19-21`, והוא שייך ל**ספריית ה-snippets** (`.code-block` / `.snippet-code`) ולא לבלוקי קוד עברי במארקדאון. הוא דווקא **כופה LTR** קבוע, בלי שום זיהוי עברית:

```css
  direction: ltr; /* force code area to stay LTR even בתוך דף RTL */
  text-align: left;
  unicode-bidi: isolate;
```

אם בפרויקט היעד אתה רוצה גם בלוקי קוד שתמיד נשארים LTR (כמו ה-snippets), זה הדפוס — אבל **הוא לא חלק ממנגנון זיהוי העברית** שביקשת. מנגנון הזיהוי לא משתמש ב-`unicode-bidi` בכלל.

---

**הערה אחת לתשומת לבך לקראת ההעברה:** המחלקה `rtl-code` ב-JS מסתמכת על שני דברים בצד הקבלה — שהבלוק עטוף ב-`<pre>` (כי `block.closest('pre')`), ושהקריאה ל-`applyRtlIfHebrew` קורית **לפני** `hljs.highlightElement` (אחרת hljs מוסיף `language-*` ו-`hasExplicitLanguage` יחזיר true). שניהם מתועדים ב-comments של הקוד עצמו, אז אם תשמור על אותו סדר — זה יעבוד כמו שהוא.

לא שיניתי כלום — הכול verbatim מהמקור.
