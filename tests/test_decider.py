from __future__ import annotations

import json

import pytest

from nightshift.decider import Choice, DeciderError, JevDecider, Score, YesNo, question_to_api

RESPONSE = {
    "model": "jev-1.13.0",
    "answers": {
        "size": {
            "type": "choice",
            "choice": "small",
            "probabilities": {"small": 0.8, "medium": 0.2},
            "confidence": 0.7,
        },
        "clarity": {"type": "score", "score": 3.2, "probabilities": {"3": 1.0}, "confidence": 0.9},
        "risk": {"type": "noul", "noul": 0.4},
    },
    "usage": {"input_tokens": 100, "output_tokens": 10},
}
QUESTIONS = {
    "size": Choice("size?", {"small": "s", "medium": "m"}),
    "clarity": Score("clear?", ["bad", "good"]),
    "risk": YesNo("risky?"),
}


def test_question_to_api_shapes():
    assert question_to_api(QUESTIONS["size"])["criteria"] == {"small": "s", "medium": "m"}
    assert question_to_api(QUESTIONS["clarity"]) == {
        "type": "score",
        "instructions": "clear?",
        "criteria": ["bad", "good"],
    }
    assert question_to_api(QUESTIONS["risk"]) == {"type": "noul", "instructions": "risky?"}


def test_ask_parses_answers_and_sends_expected_request():
    seen = {}

    def transport(url, headers, body, timeout):
        seen.update(url=url, headers=headers, body=json.loads(body))
        return 200, json.dumps(RESPONSE).encode()

    jev = JevDecider("k", transport=transport)
    answers = jev.ask({"title": "t"}, QUESTIONS)

    assert seen["url"] == "https://api.typesafe.ai/v1/systemone"
    assert seen["headers"]["Authorization"] == "Bearer k"
    assert seen["body"]["model"] == "jev-latest"
    assert seen["body"]["state"] == {"title": "t"}
    assert answers["size"].choice == "small"
    assert answers["clarity"].score == pytest.approx(3.2)
    assert answers["risk"].noul == pytest.approx(0.4)
    assert jev.last_usage["input_tokens"] == 100


def test_retries_on_rate_limit_then_succeeds():
    statuses = iter([429, 529, 200])
    sleeps = []

    def transport(url, headers, body, timeout):
        status = next(statuses)
        return status, json.dumps(RESPONSE).encode() if status == 200 else b"slow down"

    jev = JevDecider("k", transport=transport, sleep=sleeps.append)
    jev.ask("s", QUESTIONS)
    assert sleeps == [1, 2]


def test_error_status_raises():
    jev = JevDecider("k", transport=lambda *a: (401, b"bad key"))
    with pytest.raises(DeciderError, match="401"):
        jev.ask("s", QUESTIONS)


def test_gives_up_after_retries():
    jev = JevDecider("k", retries=1, transport=lambda *a: (429, b""), sleep=lambda s: None)
    with pytest.raises(DeciderError, match="429"):
        jev.ask("s", QUESTIONS)
