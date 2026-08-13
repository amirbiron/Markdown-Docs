"""בדיקות על קוד המקור עצמו, ולא על התנהגותו."""

from __future__ import annotations

from pathlib import Path

import pytest

# תווי בקרה דו-כיווניים של יוניקוד. הם משנים את סדר ההצגה של טקסט בלי
# להיראות, ולכן קוד יכול להיראות לקורא אחרת ממה שהמפרש קורא — זו
# התקפת Trojan Source, שמוכרת כ-CVE-2021-42574.
#
# בפרויקט הזה ההערות בעברית, וזו בדיוק הסביבה שבה מסמנים אותם בתום לב:
# הערה שמתחילה במילה לטינית בתוך פסקה עברית מתיישרת "הפוך", והפיתוי הוא
# להוסיף RLM כדי לתקן. הפתרון הוא לנסח מחדש — הערה שנשענת על תו
# בלתי-נראה כדי להיקרא נכון שבירה ממילא: היא נשברת בהעתקה, ב-diff
# ובעורך שמנקה רווחים.
#
# הבדיקה כאן היא הסיבה שהתו לא יחזור בהערה העברית הבאה.
# מוגדרים כ-escape ולא כתווים ממשיים, אחרת הקובץ הזה נכשל בבדיקה של
# עצמו — וזה בדיוק מה שקרה בהרצה הראשונה.
BIDI_CONTROLS = {
    "\u200e": "LRM",
    "\u200f": "RLM",
    "\u202a": "LRE",
    "\u202b": "RLE",
    "\u202c": "PDF",
    "\u202d": "LRO",
    "\u202e": "RLO",
    "\u2066": "LRI",
    "\u2067": "RLI",
    "\u2068": "FSI",
    "\u2069": "PDI",
}

SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", ".ruff_cache", ".pytest_cache"}

REPO_ROOT = Path(__file__).resolve().parent.parent


def _sources() -> list[Path]:
    return [
        path
        for path in sorted(REPO_ROOT.rglob("*.py"))
        if not SKIP_DIRS & set(path.relative_to(REPO_ROOT).parts)
    ]


def test_there_are_sources_to_scan():
    """שומר על הבדיקה עצמה: איסוף ריק היה עובר בשקט ולא בודק כלום."""
    assert len(_sources()) > 20


@pytest.mark.parametrize("path", _sources(), ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_no_bidi_control_characters(path: Path):
    findings = [
        f"{path.relative_to(REPO_ROOT)}:{number} — {BIDI_CONTROLS[char]}"
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        for char in line
        if char in BIDI_CONTROLS
    ]
    assert not findings, (
        "נמצאו תווי בקרה דו-כיווניים. הם בלתי-נראים ומשנים את סדר ההצגה, "
        "כלומר הקוד עלול להיראות אחרת ממה שהוא עושה. נסחו מחדש את ההערה "
        "במקום להוסיף אותם:\n" + "\n".join(findings)
    )
