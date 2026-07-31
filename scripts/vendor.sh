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
REACT=18.3.1

OUT=assets/vendor
mkdir -p "$OUT"

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

echo "/* Prism ${PRISM} — חבילה ארוזה מראש. נוצר על ידי scripts/vendor.sh */" > "$OUT/prism.js"
for part in "${PRISM_PARTS[@]}"; do
  echo "  · $part"
  curl -fsS "https://cdn.jsdelivr.net/npm/prismjs@${PRISM}/${part}" >> "$OUT/prism.js"
  printf '\n;\n' >> "$OUT/prism.js"
done

echo "  · mermaid"
curl -fsS "https://cdn.jsdelivr.net/npm/mermaid@${MERMAID}/dist/mermaid.min.js" -o "$OUT/mermaid.js"

echo "  · react"
curl -fsS "https://unpkg.com/react@${REACT}/umd/react.production.min.js" -o "$OUT/react.js"
curl -fsS "https://unpkg.com/react-dom@${REACT}/umd/react-dom.production.min.js" -o "$OUT/react-dom.js"

echo
ls -la "$OUT" | awk 'NR>3 {printf "  %-16s %s\n", $9, $5}'
