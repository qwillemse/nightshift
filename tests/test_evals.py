from __future__ import annotations

from nightshift.decider import FakeDecider
from nightshift.evals import run_eval

from .conftest import make_answers

CASES = [
    {"id": "a", "title": "A", "body": "", "expected": {"size": "small", "risk": "low"}},
    {"id": "b", "title": "B", "body": "", "expected": {"size": "large", "risk": "low"}},
]


def test_report_counts_and_misses():
    report = run_eval(CASES, FakeDecider(make_answers(size="small")))
    assert report.accuracy("size") == 0.5
    assert report.accuracy("risk") == 1.0
    assert [(m.case, m.group, m.expected, m.got) for m in report.misses] == [
        ("b", "size", "large", "small")
    ]


def test_calibration_table_bins_by_confidence():
    report = run_eval(CASES, FakeDecider(make_answers(size="small")))
    rows = report.calibration_table()
    assert rows and rows[0][1] == 2  # both size answers have confidence 0.9, one is right
    assert rows[0][3] == 0.5
    assert "misses:" in report.format()
