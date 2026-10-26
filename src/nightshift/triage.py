"""Turn an issue into suggested labels using a Decider.

Only the issue title and body are sent to the model, never its labels, so evals stay honest.
All thresholds are starting guesses, to be tuned with the eval.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from nightshift.decider import Answer, Choice, Decider, Question, Score, YesNo

SIZE = Choice(
    "How large is the work needed to resolve this issue?",
    {
        "trivial": "Typo, comment or one-line change; no behaviour decisions needed",
        "small": "Localised bug fix or tweak in one or two files",
        "medium": "Feature or change spanning several files, tests and maybe a migration",
        "large": "New subsystem, cross-cutting refactor, or anything touching auth/infra at scale",
    },
)
CLARITY = Score(
    "How clearly is this issue specified for someone who has not seen the codebase?",
    [
        "Unusable: no concrete problem or outcome described",
        "Vague: problem named but scope and expected result are guesswork",
        "Partial: goal is clear but key acceptance details are missing",
        "Good: clear goal and mostly testable outcome",
        "Excellent: precise problem, repro or spec, and testable outcome",
    ],
)
TYPE = Choice(
    "What kind of work is this?",
    {
        "bug": "Something that exists is broken or behaves incorrectly",
        "feature": "New or changed user-visible capability",
        "chore": "Maintenance, refactor, docs, tooling or dependency work",
        "research": "Investigation whose result is a written recommendation, not a code change",
    },
)
PRIORITY = Choice(
    "How urgent is it to resolve this issue, judging only from the text?",
    {
        "low": "Nice to have; no one is blocked or harmed",
        "medium": "Noticeable problem or useful improvement; no immediate harm",
        "high": "Blocks users, loses data, breaks core functionality, or is security relevant",
    },
)
# One question per risk category: a combined question missed a DB migration in the spike.
RISK_QUESTIONS: dict[str, YesNo] = {
    "risk_schema": YesNo("Does resolving this change the database schema or require a migration?"),
    "risk_auth": YesNo(
        "Does resolving this touch authentication, authorization, sessions or secrets?"
    ),
    "risk_deps": YesNo("Does resolving this add, remove or upgrade a dependency?"),
    "risk_deploy": YesNo(
        "Does resolving this change deployment, infrastructure or CI configuration?"
    ),
}
NEEDS_RUNNING_APP = YesNo(
    "Would verifying the result require running the app and exercising it manually, "
    "beyond automated tests?"
)

# Coding models Jev can choose between. Descriptions are intuition only, not measured
# performance; relative prices are from third-party sources (Sonnet $2/$10, Opus $4/$20
# per million tokens). Replace with run-log history once it exists.
EXECUTORS = {
    "claude-haiku-5-5": "Cheapest and fastest. Good for small, well-specified changes; "
    "weaker on large or ambiguous work.",
    "claude-sonnet-5-5": "Mid price, strong all-rounder for everyday features and bug fixes "
    "across a few files.",
    "claude-opus-5-5": "Most capable, about twice Sonnet's price. Best for large, risky or "
    "cross-cutting changes where mistakes are costly.",
}
EXECUTOR = Choice(
    "Which model should implement this issue? Weigh the difficulty and size of the work "
    "against the budget in the state (budget_usd, if present): prefer a cheaper model when "
    "the work is easy or the budget is tight, a stronger one when the work is hard.",
    EXECUTORS,
)
# Static baseline: the table Jev's picks are compared against.
BASELINE_EXECUTOR = {
    "trivial": "claude-haiku-5-5",
    "small": "claude-sonnet-5-5",
    "medium": "claude-sonnet-5-5",
    "large": "claude-opus-5-5",
}

QUESTIONS: dict[str, Question] = {
    "size": SIZE,
    "clarity": CLARITY,
    "type": TYPE,
    "priority": PRIORITY,
    **RISK_QUESTIONS,
    "needs_running_app": NEEDS_RUNNING_APP,
}

# Label groups Nightshift fills. A label looks like "size:small".
GROUPS = ("size", "clarity", "risk", "type", "priority")

CLARITY_LOW_BELOW = 1.5
CLARITY_HIGH_FROM = 2.75
RISK_MEDIUM_FROM = 0.3
RISK_HIGH_FROM = 0.6
# Refine-first gate (starting guesses, to tune): refine when clarity is low or the model
# is unsure about the size.
SIZE_CONFIDENCE_MIN = 0.4


@dataclass
class Issue:
    title: str
    body: str = ""
    number: int | None = None
    labels: list[str] = field(default_factory=list)
    discussion: list[dict[str, str]] = field(default_factory=list)  # trusted comments only

    def state(self, budget_usd: float | None = None) -> dict[str, Any]:
        state: dict[str, Any] = {"title": self.title, "body": self.body}
        if self.discussion:
            state["discussion"] = self.discussion
        if budget_usd is not None:
            state["budget_usd"] = budget_usd
        return state


@dataclass
class Suggestion:
    labels: dict[str, str]  # group -> value, e.g. {"size": "small"}
    confidence: dict[str, float | None]  # group -> model confidence where available
    detail: dict[str, Any]  # raw numbers for logging and evals
    disposition: str = "proceed"  # "proceed" or "refine"
    refine_reasons: list[str] = field(default_factory=list)


def clarity_value(score: float) -> str:
    if score < CLARITY_LOW_BELOW:
        return "low"
    return "high" if score >= CLARITY_HIGH_FROM else "medium"


def risk_value(p: float) -> str:
    if p >= RISK_HIGH_FROM:
        return "high"
    return "medium" if p >= RISK_MEDIUM_FROM else "low"


def triage(
    issue: Issue,
    decider: Decider,
    budget_usd: float | None = None,
    force_executor: bool = False,
) -> Suggestion:
    """Pass 1 classifies. Pass 2 picks a model, but only once the issue is clear enough.

    Unclear issues get disposition "refine" and no executor: they should be improved and
    clarified first, then triaged again. `force_executor` runs pass 2 regardless (for evals).
    """
    answers: dict[str, Answer] = decider.ask(issue.state(), QUESTIONS)

    risk_scores = {k: answers[k].noul or 0.0 for k in RISK_QUESTIONS}
    worst = max(risk_scores, key=lambda k: risk_scores[k])
    labels = {
        "size": answers["size"].choice or "",
        "clarity": clarity_value(answers["clarity"].score or 0.0),
        "risk": risk_value(risk_scores[worst]),
        "type": answers["type"].choice or "",
        "priority": answers["priority"].choice or "",
    }
    confidence: dict[str, float | None] = {
        "size": answers["size"].confidence,
        "clarity": answers["clarity"].confidence,
        "type": answers["type"].confidence,
        "priority": answers["priority"].confidence,
        "risk": None,
    }
    detail: dict[str, Any] = {
        "clarity_score": answers["clarity"].score,
        "risk": risk_scores,
        "risk_driver": worst,
        "needs_running_app": answers["needs_running_app"].noul,
        "size_probabilities": answers["size"].probabilities,
    }

    reasons = []
    if labels["clarity"] == "low":
        reasons.append("clarity is low")
    size_conf = answers["size"].confidence
    if size_conf is not None and size_conf < SIZE_CONFIDENCE_MIN:
        reasons.append(f"size confidence {size_conf:.2f} is below {SIZE_CONFIDENCE_MIN}")
    disposition = "refine" if reasons else "proceed"

    if disposition == "proceed" or force_executor:
        assessment = {k: labels[k] for k in ("size", "risk", "clarity", "type")}
        state = {**issue.state(budget_usd), "assessment": assessment}
        pick = decider.ask(state, {"executor": EXECUTOR})["executor"]
        labels["executor"] = pick.choice or ""
        confidence["executor"] = pick.confidence
        detail["executor_probabilities"] = pick.probabilities

    return Suggestion(labels, confidence, detail, disposition, reasons)
