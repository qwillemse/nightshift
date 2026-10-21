"""Decision-model interface. Jev (TypeSafe) is one implementation, a fake is another."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


class DeciderError(RuntimeError):
    pass


@dataclass(frozen=True)
class Choice:
    instructions: str
    options: dict[str, str]


@dataclass(frozen=True)
class Score:
    """Ordered levels. Jev returns a probability-weighted score over 0-based level indexes."""

    instructions: str
    levels: list[str]


@dataclass(frozen=True)
class YesNo:
    instructions: str


Question = Choice | Score | YesNo


@dataclass
class Answer:
    kind: str  # "choice" | "score" | "noul"
    choice: str | None = None
    score: float | None = None
    noul: float | None = None  # probability of "yes"
    probabilities: dict[str, float] = field(default_factory=dict)
    confidence: float | None = None


class Decider(Protocol):
    def ask(self, state: Any, questions: dict[str, Question]) -> dict[str, Answer]: ...


def question_to_api(q: Question) -> dict[str, Any]:
    match q:
        case Choice():
            return {"type": "choice", "instructions": q.instructions, "criteria": q.options}
        case Score():
            return {"type": "score", "instructions": q.instructions, "criteria": q.levels}
        case YesNo():
            return {"type": "noul", "instructions": q.instructions}
    raise DeciderError(f"unknown question type: {q!r}")


def parse_answer(raw: dict[str, Any]) -> Answer:
    kind = raw.get("type")
    if kind == "choice":
        return Answer(
            "choice",
            choice=raw["choice"],
            probabilities=raw.get("probabilities", {}),
            confidence=raw.get("confidence"),
        )
    if kind == "score":
        return Answer(
            "score",
            score=raw["score"],
            probabilities=raw.get("probabilities", {}),
            confidence=raw.get("confidence"),
        )
    if kind == "noul":
        return Answer("noul", noul=raw["noul"])
    raise DeciderError(f"unknown answer type: {kind!r}")


Transport = Callable[[str, dict[str, str], bytes, float], tuple[int, bytes]]


def urllib_post(
    url: str, headers: dict[str, str], body: bytes, timeout: float
) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except urllib.error.URLError as e:
        raise DeciderError(f"network error calling {url}: {e.reason}") from e


class JevDecider:
    """TypeSafe Jev via POST {base_url}/v1/systemone."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.typesafe.ai",
        model: str = "jev-latest",
        timeout: float = 30,
        retries: int = 3,
        transport: Transport = urllib_post,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.api_key = api_key
        self.url = base_url.rstrip("/") + "/v1/systemone"
        self.model = model
        self.timeout = timeout
        self.retries = retries
        self.transport = transport
        self.sleep = sleep
        self.last_usage: dict[str, int] = {}

    def ask(self, state: Any, questions: dict[str, Question]) -> dict[str, Answer]:
        body = json.dumps(
            {
                "state": state,
                "model": self.model,
                "questions": {k: question_to_api(q) for k, q in questions.items()},
            }
        ).encode()
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        for attempt in range(self.retries + 1):
            status, payload = self.transport(self.url, headers, body, self.timeout)
            if status in (429, 529) and attempt < self.retries:
                self.sleep(2**attempt)
                continue
            if status != 200:
                raise DeciderError(f"Jev returned HTTP {status}: {payload[:300]!r}")
            data = json.loads(payload)
            self.last_usage = data.get("usage", {})
            return {k: parse_answer(v) for k, v in data["answers"].items()}
        raise DeciderError("unreachable")


class FakeDecider:
    """Returns canned answers; records every call. For tests and offline development."""

    def __init__(self, answers: dict[str, Answer] | Callable[[Any], dict[str, Answer]]):
        self._answers = answers
        self.calls: list[tuple[Any, dict[str, Question]]] = []

    def ask(self, state: Any, questions: dict[str, Question]) -> dict[str, Answer]:
        self.calls.append((state, questions))
        answers = self._answers(state) if callable(self._answers) else self._answers
        return {k: answers[k] for k in questions}
