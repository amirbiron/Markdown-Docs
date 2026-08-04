#!/usr/bin/env bash
# מוריד את הגופנים מ-Google Fonts אל assets/fonts, ובונה גיליון סגנון
# מקומי שמצביע עליהם.
#
# למה: זה היה המקור החיצוני האחרון שנשאר בדף. כל שאר הצד-השלישי כבר
# יורד לריפו (scripts/vendor.sh), וגיליון סגנון אחד מ-fonts.googleapis.com
# פירושו שכל טעינת דף מדווחת לצד שלישי מי קורא מה ומתי, ושהאתר לא נטען
# כמו שצריך בלי אינטרנט או מאחורי חסימה. הגופנים בריפו — הדף שלם בעצמו.
#
# אין כאן CHECKSUMS כמו ב-vendor.sh, וזה מכוון: הכתובות של gstatic כוללות
# חתימת תוכן משלהן, והקבצים שיורדים כאן נכנסים לריפו ונשארים בו. הקובץ
# שנכנס ל-git הוא הנעילה. הסקריפט רץ בשדרוג גופנים בלבד, לא בכל build.
#
# שינוי רשימת הגופנים: ערכו את FONTS_URL כאן והריצו מחדש.
set -euo pipefail
cd "$(dirname "$0")/.."

# המשפחות מסודרות לפי התמה שצורכת אותן, כדי שהוספת תמה תהיה תוספת
# במקום עריכה של מחרוזת אחת ארוכה:
#   dark  — Heebo, JetBrains Mono
#   light — Assistant, Frank Ruhl Libre, IBM Plex Mono
#   dim   — Rubik, Source Code Pro
FONTS_URL="https://fonts.googleapis.com/css2?family=Heebo:wght@300;400;500;600;700;800&family=Assistant:wght@300;400;500;600;700&family=Frank+Ruhl+Libre:wght@400;500;700;800&family=JetBrains+Mono:wght@400;500;700&family=IBM+Plex+Mono:wght@400;500;600&family=Rubik:wght@300;400;500;600;700;800&family=Source+Code+Pro:wght@400;500;600;700&display=swap"

# Google מגיש פורמט לפי ה-User-Agent. בלי UA של דפדפן מודרני מתקבל TTF
# ישן ומנופח פי כמה, בלי unicode-range — כלומר גם כבד יותר וגם בלי
# פיצול תת-הקבוצות שמונע הורדת עברית ממי שקורא אנגלית.
UA="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

OUT=assets/fonts
mkdir -p "$OUT"

TMP=$(mktemp -d "$OUT/.tmp.XXXXXX")
trap 'rm -rf "$TMP"' EXIT

echo "  · גיליון הסגנון"
curl -fsS -A "$UA" "$FONTS_URL" -o "$TMP/source.css"

# רשימת קובצי הגופן. sort -u כי אותו קובץ מופיע בכמה הצהרות @font-face.
mapfile -t URLS < <(grep -oE 'https://fonts\.gstatic\.com/[^)]+' "$TMP/source.css" | sort -u)
[ "${#URLS[@]}" -gt 0 ] || { echo "לא נמצאו קובצי גופן בגיליון — האם המבנה השתנה?"; exit 1; }

echo "  · ${#URLS[@]} קובצי גופן"
cp "$TMP/source.css" "$TMP/fonts.css"

# השמות נאספים כאן ומשמשים שוב בלולאת ההעברה. חישוב הנוסחה פעמיים היה
# מתפצל בשקט ברגע שכלל השמות משתנה במקום אחד בלבד.
NAMES=()

for url in "${URLS[@]}"; do
  # s/heebo/v26/AbC.woff2 → heebo-v26-AbC.woff2. שטוח כדי שהגיליון יוכל
  # להצביע על ספרייה אחת, וייחודי כי הנתיב המקורי כבר ייחודי.
  name=$(printf '%s' "${url#https://fonts.gstatic.com/s/}" | tr '/' '-')
  NAMES+=("$name")
  curl -fsS "$url" -o "$TMP/$name"

  # שם הקובץ לבדו, בלי נתיב. url() ב-CSS נפתר יחסית לקובץ ה-CSS עצמו,
  # ולכן הגיליון עובד מכל נקודת עגינה — גם אם /assets יעבור מחר למקום אחר.
  # ההחלפה היא על הכתובת המלאה ולא על תבנית; הפרדה ב-| ובריחה מהתו הזה.
  escaped=${url//\|/\\|}
  sed -i "s|${escaped}|${name}|g" "$TMP/fonts.css"
done

# ודא שלא נשארה אף כתובת חיצונית. זו הבדיקה שמצדיקה את כל הסקריפט —
# קובץ אחד שלא הוחלף מחזיר בשקט את התלות שרצינו להסיר.
if grep -qE 'https?://' "$TMP/fonts.css"; then
  echo "נשארו כתובות חיצוניות בגיליון:"
  grep -oE 'https?://[^)]+' "$TMP/fonts.css" | sort -u
  exit 1
fi

{
  echo "/* גופנים מקומיים. נוצר על ידי scripts/vendor-fonts.sh — אל תערכו ביד. */"
  cat "$TMP/fonts.css"
} > "$TMP/final.css"

mv "$TMP/final.css" "$OUT/fonts.css"
for name in "${NAMES[@]}"; do
  mv "$TMP/$name" "$OUT/$name"
done

echo
echo "  $OUT/fonts.css"
du -sh "$OUT" | awk '{print "  סך הכול: " $1}'
