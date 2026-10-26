from __future__ import annotations

from nightshift.decider import Answer, FakeDecider
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


def test_unclear_issue_goes_to_refine_without_a_model_pick():
    decider = FakeDecider(make_answers(clarity=1.0))
    s = triage(Issue("vague"), decider)
    assert s.disposition == "refine"
    assert "executor" not in s.labels
    assert len(decider.calls) == 1
    assert s.refine_reasons == ["clarity is low"]


def test_low_size_confidence_also_triggers_refine():
    answers = make_answers()
    answers["size"] = Answer("choice", choice="small", probabilities={"small": 0.3}, confidence=0.3)
    s = triage(Issue("t"), FakeDecider(answers))
    assert s.disposition == "refine"
    assert "size confidence 0.30" in s.refine_reasons[0]


def test_model_pick_is_a_second_call_with_assessment_and_budget():
    decider = FakeDecider(make_answers(size="large", risks={"risk_auth": 0.9}))
    s = triage(Issue("t", "b"), decider, budget_usd=2.0)
    assert s.disposition == "proceed"
    assert len(decider.calls) == 2
    state, questions = decider.calls[1]
    assert list(questions) == ["executor"]
    assert state["budget_usd"] == 2.0
    assert state["assessment"] == {
        "size": "large",
        "risk": "high",
        "clarity": "high",
        "type": "bug",
    }
    assert "budget_usd" not in decider.calls[0][0]


def test_no_budget_key_when_none_given():
    decider = FakeDecider(make_answers())
    triage(Issue("t", "b"), decider)
    assert "budget_usd" not in decider.calls[1][0]


def test_force_executor_picks_a_model_even_for_unclear_issues():
    decider = FakeDecider(make_answers(clarity=1.0))
    s = triage(Issue("vague"), decider, force_executor=True)
    assert s.disposition == "refine"
    assert s.labels["executor"] == "claude-sonnet-5-5"


def test_baseline_table_only_names_known_executors():
    assert set(BASELINE_EXECUTOR.values()) <= set(EXECUTORS)
    assert set(BASELINE_EXECUTOR) == {"trivial", "small", "medium", "large"}


def test_discussion_is_sent_only_when_present():
    decider = FakeDecider(make_answers())
    triage(Issue("t", "b"), decider)
    triage(Issue("t", "b", discussion=[{"from": "owner", "text": "1: yes"}]), decider)
    assert "discussion" not in decider.calls[0][0]
    assert decider.calls[2][0]["discussion"] == [{"from": "owner", "text": "1: yes"}]
