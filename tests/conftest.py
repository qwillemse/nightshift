from __future__ import annotations

import pytest

from nightshift.decider import Answer, FakeDecider
from nightshift.triage import QUESTIONS


def make_answers(
    size: str = "small",
    clarity: float = 3.5,
    type_: str = "bug",
    priority: str = "medium",
    risks: dict[str, float] | None = None,
    needs_running_app: float = 0.1,
) -> dict[str, Answer]:
    risks = risks or {}
    answers = {
        "size": Answer("choice", choice=size, probabilities={size: 0.9}, confidence=0.9),
        "clarity": Answer("score", score=clarity, confidence=0.8),
        "type": Answer("choice", choice=type_, probabilities={type_: 0.9}, confidence=0.9),
        "priority": Answer(
            "choice", choice=priority, probabilities={priority: 0.9}, confidence=0.9
        ),
        "needs_running_app": Answer("noul", noul=needs_running_app),
    }
    for key in QUESTIONS:
        if key.startswith("risk_"):
            answers[key] = Answer("noul", noul=risks.get(key, 0.05))
    return answers


@pytest.fixture
def fake_decider() -> FakeDecider:
    return FakeDecider(make_answers())
