from __future__ import annotations

import json

import pytest

from nightshift.context import Context
from nightshift.decider import Answer, FakeDecider
from nightshift.llm import FakeLLM
from nightshift.refine import (
    RefineError,
    ground,
    parse_json,
    refine_issue,
    render_markdown,
)
from nightshift.triage import Issue

from .test_llm_context import FILES, make_repo

ISSUE = Issue("Be able to change amount of servings", "", number=3)

MODEL_REPLY = {
    "understood_request": "The user wants to change the number of servings.",
    "code_facts": [
        {
            "statement": "Recipe has a servings field defaulting to 2",
            "source": "src/app/servings.py",
            "quote": "def scale_servings(recipe, servings)",
        },
        {"statement": "Made up fact", "source": "src/app/servings.py", "quote": "does not exist"},
        {"statement": "Wrong file", "source": "src/app/ghost.py", "quote": "x"},
    ],
    "likely_files": ["src/app/servings.py", "src/app/invented.py"],
    "acceptance_criteria": [
        {"text": "User can change servings", "quote": "change amount of servings"},
        {"text": "Amounts scale automatically", "quote": "this is not in the issue"},
    ],
    "questions": [
        {"question": "Which max servings?", "kind": "scope", "blocking": False},
        {
            "question": "Should the choice be remembered?",
            "why": "app is stateless",
            "kind": "business_decision",
            "blocking": True,
            "options": ["yes", "no"],
        },
        {"question": "Round to whole units?", "kind": "technical", "blocking": True},
    ],
    "suggestions": [{"text": "Add a half-serving step", "reason": "common in recipes"}],
    "possibly_already_done": None,
    "duplicate_of": [7, 99],
}


def refine(tmp_path, reply=MODEL_REPLY, jev_p=0.1):
    repo = make_repo(tmp_path, FILES)
    llm = FakeLLM(json.dumps(reply))
    decider = FakeDecider({"adds_requirements": Answer("noul", noul=jev_p)})
    result = refine_issue(
        ISSUE, repo, llm, decider, model="claude-sonnet-5-5", other_issues=[(7, "Add servings")]
    )
    return result, llm, decider


def test_parse_json_tolerates_fences_and_chatter():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('Here you go: {"a": 1} done') == {"a": 1}
    with pytest.raises(RefineError):
        parse_json("no json here")
    with pytest.raises(RefineError):
        parse_json("{broken")


def test_ungrounded_items_are_dropped_and_reported(tmp_path):
    result, _, _ = refine(tmp_path)
    assert [c.text for c in result.criteria] == ["User can change servings"]
    assert [f.statement for f in result.facts] == ["Recipe has a servings field defaulting to 2"]
    assert result.likely_files == ["src/app/servings.py"]
    assert result.duplicate_of == [7]
    reasons = {(kind, reason) for kind, _, reason in result.dropped}
    assert ("criterion", "quote not found in the issue") in reasons
    assert any(k == "code fact" and "not shown" in r for k, r in reasons)
    assert any(k == "code fact" and "quote not found" in r for k, r in reasons)
    assert ("duplicate", "not a listed issue number") in reasons


def test_questions_sorted_blocking_then_business_first(tmp_path):
    result, _, _ = refine(tmp_path)
    assert [q.question for q in result.questions] == [
        "Should the choice be remembered?",
        "Round to whole units?",
        "Which max servings?",
    ]


def test_suggestions_stay_separate_from_criteria(tmp_path):
    result, _, _ = refine(tmp_path)
    assert [s.text for s in result.suggestions] == ["Add a half-serving step"]
    assert all("half" not in c.text for c in result.criteria)


def test_model_sees_issue_context_and_rules(tmp_path):
    _, llm, _ = refine(tmp_path)
    system, user, model = llm.calls[0]
    assert "Never invent" in system and model == "claude-sonnet-5-5"
    assert "Title: Be able to change amount of servings" in user
    assert "## FILE src/app/servings.py" in user and "#7 Add servings" in user


def test_jev_invention_check_flags_high_scores(tmp_path):
    ok, _, _ = refine(tmp_path, jev_p=0.1)
    flagged, _, decider = refine(tmp_path / "again", jev_p=0.8)
    assert not ok.invention_flagged and flagged.invention_flagged
    state, _ = decider.calls[0]
    assert "change the number of servings" in state["improved"]
    assert "half-serving" not in state["improved"]  # suggestions are not part of the issue


def test_markdown_labels_everything_and_never_replaces_the_issue(tmp_path):
    result, _, _ = refine(tmp_path, jev_p=0.8)
    md = render_markdown(result, ISSUE)
    assert "original issue is unchanged" in md
    assert "**Questions for you**" in md and "business decision, blocking" in md
    assert 'from: "change amount of servings"' in md
    assert "Suggestions (optional, not part of the issue" in md
    assert "FLAGGED" in md and "Dropped because they could not be verified: 5" in md


def test_ground_handles_malformed_model_output():
    ctx = Context(text="", sources={}, tracked=[])
    result = ground({"acceptance_criteria": "oops", "questions": [1, {"x": 1}]}, ISSUE, ctx, set())
    assert result.criteria == [] and result.questions == []
