#!/usr/bin/env bash
# מוריד את ספריות הצד-שלישי ל-assets/vendor.
#
# למה בכלל: ה-autoloader של Prism מוריד קובצי דקדוק בזמן ריצה, לפי השפה
# שנתקל בה. הכתובות נבנות דינמית ולכן אי אפשר לחתום אותן ב-integrity —
# וזה בדיוק המסלול שרץ בכל פעם שנפתח מסמך עם בלוק קוד. במקום לחתום את
# מה שאפשר ולהשאיר את השאר פתוח, הכול יורד לריפו.
#
# שדרוג גרסה: שנו את המספרים כאן והריצו מחדש.
set -euo pipefail
cd "$(dirname "$0")/.."

PRISM=1.29.0
MERMAID=10.9.1

# כתובות React נגזרות מ-assets/support.js ולא נכתבות כאן שוב. ה-runtime
# הוא זה שמחליט איזו גרסה הוא מבקש, ומספר גרסה שנכתב פעמיים מתפצל בשקט
# בשדרוג הבא — ואז index.html ממפה כתובת שאף אחד כבר לא מבקש, ה-mapping
# לא נתפס, ו-React נטען מ-unpkg בלי שאף אחד ישים לב.
REACT_URL=$(grep -oE 'https://unpkg\.com/react@[^"]+' assets/support.js | head -1)
REACT_DOM_URL=$(grep -oE 'https://unpkg\.com/react-dom@[^"]+' assets/support.js | head -1)
[ -n "$REACT_URL" ] && [ -n "$REACT_DOM_URL" ] || { echo "לא נמצאו כתובות React ב-assets/support.js"; exit 1; }

OUT=assets/vendor
mkdir -p "$OUT"

# הורדה לקבצים זמניים, והעברה למקום הסופי רק אחרי שהכול הצליח. בלי זה,
# curl שנכשל באמצע משאיר קובץ חתוך שהאתר ממשיך להגיש.
TMP=$(mktemp -d "$OUT/.tmp.XXXXXX")
trap 'rm -rf "$TMP"' EXIT

# סדר הטעינה של Prism הוא תלותי: core, ואז markup ו-clike, ורק אז השפות
# שנשענות עליהן. שרשור בסדר שגוי נכשל בשקט — השפה פשוט לא נרשמת.
PRISM_PARTS=(
  components/prism-core.min.js
  components/prism-markup.min.js
  components/prism-clike.min.js
  components/prism-javascript.min.js
  components/prism-typescript.min.js
  components/prism-jsx.min.js
  components/prism-tsx.min.js
  components/prism-css.min.js
  components/prism-python.min.js
  components/prism-bash.min.js
  components/prism-sql.min.js
  components/prism-json.min.js
  components/prism-yaml.min.js
  components/prism-markdown.min.js
)

echo "/* Prism ${PRISM} — חבילה ארוזה מראש. נוצר על ידי scripts/vendor.sh */" > "$TMP/prism.js"
for part in "${PRISM_PARTS[@]}"; do
  echo "  · $part"
  curl -fsS "https://cdn.jsdelivr.net/npm/prismjs@${PRISM}/${part}" >> "$TMP/prism.js"
  printf '\n;\n' >> "$TMP/prism.js"
done

echo "  · mermaid"
curl -fsS "https://cdn.jsdelivr.net/npm/mermaid@${MERMAID}/dist/mermaid.min.js" -o "$TMP/mermaid.js"

echo "  · react"
curl -fsS "$REACT_URL" -o "$TMP/react.js"
curl -fsS "$REACT_DOM_URL" -o "$TMP/react-dom.js"

# ── אימות מול הסכומים השמורים ────────────────────────────────────────
# רק אחרי שכל ההורדות הצליחו. הקובץ CHECKSUMS מקובע בריפו, ולכן CDN
# שמגיש תוכן אחר נתפס כאן במקום להיכנס בשקט.
# נתיב מוחלט שנגזר מ-$OUT, ולא נתיב שנכתב שוב ביד ועוד אחד שמחושב
# מחדש דרך `cd ..` מתוך $TMP. שתי הצורות ההן הניחו ש-$TMP יושב בדיוק
# בתוך $OUT, ושינוי אחד ב-mktemp היה משתיק את האימות בלי סימן — כלומר
# מבטל בדיוק את מה שהאימות נועד לתפוס.
CHECKSUMS="$(cd "$OUT" && pwd)/CHECKSUMS"
if [ -f "$CHECKSUMS" ] && [ "${REFRESH_CHECKSUMS:-0}" != "1" ]; then
  echo
  echo "מאמת מול $CHECKSUMS"
  ( cd "$TMP" && sha256sum -c "$CHECKSUMS" ) || {
    echo
    echo "אימות נכשל. אם זה שדרוג גרסה מכוון: REFRESH_CHECKSUMS=1 $0"
    exit 1
  }
fi

for f in prism.js mermaid.js react.js react-dom.js; do
  mv "$TMP/$f" "$OUT/$f"
done

( cd "$OUT" && sha256sum prism.js mermaid.js react.js react-dom.js > CHECKSUMS )

echo
# find ולא `ls | awk`: העמודות של ls נחתכות לפי רווחים, ושם קובץ עם רווח
# מזיז את כולן. -printf מחזיר את השם והגודל ישירות.
find "$OUT" -maxdepth 1 -type f ! -name '.*' -printf '  %-16f %s\n' | sort
