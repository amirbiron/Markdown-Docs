"""עזרי העיצוב של תשובות ה-MCP.

בדיקות יחידה טהורות, בלי DB — הפונקציות האלה נמצאות בכל מסלול, ולכן
נפילה שלהן מפילה כלי שלם.
"""

from __future__ import annotations

import pytest

from app.mcp.formatting import clamp, did_you_mean, err, ok


# ── מעטפת ─────────────────────────────────────────────────────────────


def test_ok_and_err_are_distinguishable_without_parsing_text():
    assert ok(count=2) == {"ok": True, "count": 2}
    assert err("not_found", message="x") == {"ok": False, "error": "not_found", "message": "x"}


# ── חיתוך ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (5, 5),
        (0, 1),  # מתחת לרצפה
        (9999, 10),  # מעל התקרה
        ("7", 7),  # מחרוזת מספרית
        (3.7, 3),  # float נחתך כלפי מטה
    ],
)
def test_clamp_keeps_the_value_in_range(value, expected):
    assert clamp(value, 1, 10, 3) == expected


@pytest.mark.parametrize(
    "value",
    [None, "abc", [], {}, object()],
    ids=["None", "טקסט", "רשימה", "מילון", "אובייקט"],
)
def test_clamp_falls_back_to_default_on_junk(value):
    assert clamp(value, 1, 10, 3) == 3


@pytest.mark.parametrize(
    "value",
    [float("inf"), float("-inf"), float("nan")],
    ids=["אינסוף", "מינוס אינסוף", "NaN"],
)
def test_clamp_survives_non_finite_numbers(value):
    """json.loads מקבל את הליטרלים Infinity ו-NaN כברירת מחדל.

    כלומר limit: Infinity מגיע לכאן כ-float אמיתי, ו-int() עליו זורק
    OverflowError — לא ValueError. פונקציה שכל תפקידה לא ליפול על קלט
    חריג לא אמורה ליפול על הקלט החריג ביותר.
    """
    assert clamp(value, 1, 10, 3) == 3


# ── הצעות ─────────────────────────────────────────────────────────────


def test_did_you_mean_catches_a_typo():
    """הטעות הנפוצה היא שגיאת כתיב, לא שם שהומצא מאפס."""
    assert did_you_mean("instalation", ["installation", "roadmap"]) == ["installation"]


def test_did_you_mean_returns_nothing_for_an_unrelated_word():
    assert did_you_mean("zzzzz", ["installation", "roadmap"]) == []
