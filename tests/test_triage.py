from __future__ import annotations

from nightshift.decider import FakeDecider
from nightshift.triage import BASELINE_EXECUTOR, EXECUTORS, Issue, clarity_value, risk_value, triage

from .conftest import make_answers


def test_labels_from_answers(fake_decider):
    s = triage(Issue("t", "b"), fake_decider)
    assert s.labels == {
        "size": "small",
        "clarity": "high",
        "risk": "low",
        "type": "bug",
        "priority": "medium",
        "executor": "claude-sonnet-5-5",
    }


def test_only_title_and_body_are_sent_to_the_model():
    decider = FakeDecider(make_answers())
    triage(Issue("title", "body", number=3, labels=["size:large", "priority:high"]), decider)
    state, _ = decider.calls[0]
    assert state == {"title": "title", "body": "body"}


def test_risk_driven_by_worst_category():
    decider = FakeDecider(make_answers(risks={"risk_schema": 0.45, "risk_auth": 0.1}))
    s = triage(Issue("t"), decider)
    assert s.labels["risk"] == "medium"
    assert s.detail["risk_driver"] == "risk_schema"


def test_high_risk_when_any_category_is_high():
    decider = FakeDecider(make_answers(risks={"risk_deploy": 0.9}))
    assert triage(Issue("t"), decider).labels["risk"] == "high"


def test_threshold_helpers():
    assert clarity_value(1.02) == "low"
    assert clarity_value(2.13) == "medium"
    assert clarity_value(3.04) == "high"
    assert risk_value(0.08) == "low"
    assert risk_value(0.32) == "medium"
    assert risk_value(0.96) == "high"


def test_budget_is_added_to_state_only_when_given():
    decider = FakeDecider(make_answers())
    triage(Issue("t", "b"), decider)
    triage(Issue("t", "b"), decider, budget_usd=0.5)
    assert "budget_usd" not in decider.calls[0][0]
    assert decider.calls[1][0]["budget_usd"] == 0.5


def test_baseline_table_only_names_known_executors():
    assert set(BASELINE_EXECUTOR.values()) <= set(EXECUTORS)
    assert set(BASELINE_EXECUTOR) == {"trivial", "small", "medium", "large"}
