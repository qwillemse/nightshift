from __future__ import annotations

import json
import subprocess

from nightshift.github import Gh, apply_suggestion
from nightshift.store import Store
from nightshift.triage import Issue, Suggestion


class FakeRun:
    """Stands in for subprocess.run; records commands and replies with canned stdout."""

    def __init__(self, stdout: str = ""):
        self.stdout = stdout
        self.commands: list[list[str]] = []

    def __call__(self, cmd, **kwargs):
        self.commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=self.stdout, stderr="")


def suggestion() -> Suggestion:
    return Suggestion(
        labels={
            "size": "small",
            "clarity": "high",
            "risk": "low",
            "type": "bug",
            "priority": "high",
        },
        confidence={"size": 0.9, "clarity": 0.8, "type": 0.7, "priority": 0.6, "risk": None},
        detail={},
    )


def test_get_issue_parses_gh_json():
    data = {"number": 7, "title": "T", "body": None, "labels": [{"name": "size:small"}]}
    gh = Gh("o/r", run=FakeRun(json.dumps(data)))
    issue = gh.get_issue(7)
    assert (issue.number, issue.title, issue.body, issue.labels) == (7, "T", "", ["size:small"])


def test_apply_adds_all_labels_when_none_exist():
    run = FakeRun()
    added = apply_suggestion(Gh("o/r", run=run), Issue("t", number=1), suggestion(), None)
    assert sorted(added) == [
        "clarity:high",
        "priority:high",
        "risk:low",
        "size:small",
        "type:bug",
    ]
    assert any("--add-label" in c for c in run.commands)


def test_apply_never_overwrites_human_labels():
    run = FakeRun()
    issue = Issue("t", number=1, labels=["size:large", "priority:low", "bug"])
    added = apply_suggestion(Gh("o/r", run=run), issue, suggestion(), None)
    assert "size:small" not in added and "priority:high" not in added
    assert "type:bug" in added


def test_apply_does_nothing_when_everything_labeled():
    run = FakeRun()
    labels = ["size:a", "clarity:a", "risk:a", "type:a", "priority:a"]
    added = apply_suggestion(
        Gh("o/r", run=run), Issue("t", number=1, labels=labels), suggestion(), None
    )
    assert added == [] and run.commands == []


def test_corrections_detected_after_human_change(tmp_path):
    store = Store(tmp_path / "ns.db")
    gh = Gh("o/r", run=FakeRun())
    apply_suggestion(gh, Issue("t", number=5), suggestion(), store)

    now = ["size:medium", "clarity:high", "risk:low", "type:bug", "priority:high"]
    found = store.detect_corrections("o/r", 5, now)
    assert [(c.group, c.suggested, c.actual) for c in found] == [("size", "small", "medium")]
    assert store.detect_corrections("o/r", 5, []) == []


def test_log_run(tmp_path):
    store = Store(tmp_path / "ns.db")
    store.log_run("triage", {"x": 1}, "o/r", 1)
    assert store.db.execute("SELECT kind, payload FROM runs").fetchall() == [("triage", '{"x": 1}')]


def test_comments_returns_bodies():
    data = {"comments": [{"body": "first"}, {"body": "<!-- nightshift:refinement -->\nhi"}]}
    gh = Gh("o/r", run=FakeRun(json.dumps(data)))
    assert gh.comments(3) == ["first", "<!-- nightshift:refinement -->\nhi"]
