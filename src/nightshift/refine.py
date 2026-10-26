"""Refine step: improve an unclear issue without inventing, and ask specific questions.

The model must quote its sources. Every acceptance criterion needs an exact quote from the
issue; every code fact needs an exact quote from a file it was shown. Items whose quotes
cannot be found are dropped (and reported), so nothing ungrounded reaches the human as if it
were part of the issue. Ideas beyond the issue live in a separate "suggestions" list. Jev
then runs an independent "did it add requirements?" check on the improved text.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nightshift.context import Context, gather_context
from nightshift.decider import Decider, YesNo
from nightshift.llm import LLM
from nightshift.triage import Issue

INVENTION_FLAG_FROM = 0.5
REFINEMENT_MARKER = "<!-- nightshift:refinement -->"
KIND_ORDER = {"business_decision": 0, "scope": 1, "technical": 2, "acceptance": 3}

SYSTEM = """You prepare vague GitHub issues so a coding agent can implement them. You work for the repository owner.

HARD RULES
1. Never invent. Do not add features, requirements, behaviours or acceptance criteria the issue does not state. If something is unclear or undecided, it is a QUESTION, not a guess.
2. Every acceptance criterion must carry a `quote`: an exact substring copied from the issue title or body that it is based on. If you cannot quote the issue, it is not a criterion: turn it into a question.
3. Every code fact must carry `source` (a file path shown in the context) and `quote`: an exact substring of that file as shown. Do not describe code you were not shown.
4. Ideas beyond the issue go ONLY in `suggestions`, each optional. The owner reviews them. Never put them in criteria.
5. Ask as many specific, answerable questions as you need. There is no limit, and asking is better than guessing. Mark a question `blocking` whenever a wrong guess would be costly or hard to revert, or the decision is the owner's (naming, scope, product behaviour, prices, which model or provider: kind "business_decision"). If you are unsure whether a choice matters, ask. Only when a choice is obvious or unimportant (for example how to round a displayed number) may you make it yourself: put it in `decisions` with the choice and why, not in `questions`. The owner sees every decision in the digest and can revert it. A non-blocking question needs a `default`, used if the owner skips it. If you propose answer options, put them in `options`; they are suggestions and the owner may choose differently.
6. Set `possibly_already_done` only if the code shown clearly implements the request (with source and quote). Name `duplicate_of` only issue numbers listed under OTHER ISSUES.
7. Keep it short. Do not pad. Output a single JSON object, no markdown fences, no commentary.
8. If a file you need is not shown (for example the file that obviously implements the area), list up to 6 paths from the FILE TREE in `files_wanted`. You then get one more pass with those files included; do not guess about code you have not seen.

JSON SCHEMA
{
  "understood_request": "1-3 sentences restating what the issue asks, using only the issue's own content",
  "code_facts": [{"statement": "...", "source": "path", "quote": "exact text from that file"}],
  "likely_files": ["path"],
  "acceptance_criteria": [{"text": "...", "quote": "exact text from the issue"}],
  "questions": [{"question": "...", "why": "...", "kind": "business_decision|scope|technical|acceptance", "blocking": true, "default": "assumption if not blocking", "options": ["..."]}],
  "decisions": [{"decision": "...", "why": "..."}],
  "files_wanted": ["path"],
  "suggestions": [{"text": "...", "reason": "..."}],
  "possibly_already_done": null,
  "duplicate_of": []
}"""  # noqa: E501


class RefineError(RuntimeError):
    pass


@dataclass
class Fact:
    statement: str
    source: str
    quote: str


@dataclass
class Criterion:
    text: str
    quote: str


@dataclass
class Question:
    question: str
    why: str = ""
    kind: str = "scope"
    blocking: bool = False
    default: str = ""
    options: list[str] = field(default_factory=list)


@dataclass
class Decision:
    text: str
    why: str = ""


@dataclass
class Idea:
    text: str
    reason: str = ""


@dataclass
class Caution:
    """How careful to be, from triage: the riskier or bigger the issue, the less is left to guess."""

    risk: str = "low"
    size: str = "small"
    size_confidence: float | None = None

    def importance_threshold(self) -> float:
        """A statement counts as important (must be a question) when its score reaches this."""
        threshold = {"low": 0.5, "medium": 0.35, "high": 0.2}.get(self.risk, 0.5)
        if self.size == "large":
            threshold -= 0.1
        if self.size_confidence is not None and self.size_confidence < 0.4:
            threshold -= 0.1
        return max(threshold, 0.05)


@dataclass
class RefineResult:
    understood_request: str = ""
    facts: list[Fact] = field(default_factory=list)
    likely_files: list[str] = field(default_factory=list)
    criteria: list[Criterion] = field(default_factory=list)
    questions: list[Question] = field(default_factory=list)
    suggestions: list[Idea] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)
    possibly_done: Fact | None = None
    duplicate_of: list[int] = field(default_factory=list)
    dropped: list[tuple[str, str, str]] = field(default_factory=list)  # (kind, text, reason)
    invention_p: float | None = None
    invention_items: dict[str, float] = field(default_factory=dict)
    invention_statements: dict[str, str] = field(default_factory=dict)
    importance_items: dict[str, float] = field(default_factory=dict)
    importance_threshold: float = 0.5
    serious: list[str] = field(default_factory=list)  # invented AND important
    small: list[str] = field(default_factory=list)  # invented but unimportant: disclosed
    invention_before: float | None = None
    revised: bool = False
    model: str = ""
    cost_usd: float | None = None
    files_shown: list[str] = field(default_factory=list)
    files_requested: list[str] = field(default_factory=list)
    rounds: int = 1
    tokens_in: int = 0
    tokens_out: int = 0

    @property
    def invention_flagged(self) -> bool:
        return bool(self.serious)


def _norm(text: str) -> str:
    return " ".join(str(text).lower().split())


def parse_json(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < start:
        raise RefineError(f"no JSON object in model reply: {text[:200]!r}")
    try:
        return json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as e:
        raise RefineError(f"invalid JSON from model: {e}") from e


def _list(data: dict[str, Any], key: str) -> list[Any]:
    value = data.get(key)
    return value if isinstance(value, list) else []


def _verified_fact(raw: Any, ctx: Context) -> tuple[Fact | None, str]:
    if not isinstance(raw, dict):
        return None, "not an object"
    fact = Fact(
        str(raw.get("statement", "")), str(raw.get("source", "")), str(raw.get("quote", ""))
    )
    shown = ctx.sources.get(fact.source)
    if shown is None:
        return None, f"source {fact.source!r} was not shown to the model"
    if not fact.quote or _norm(fact.quote) not in _norm(shown):
        return None, f"quote not found in {fact.source}"
    return fact, ""


def ground(
    data: dict[str, Any], issue: Issue, ctx: Context, known_issue_numbers: set[int]
) -> RefineResult:
    """Keep only items whose quotes can be verified; report everything dropped."""
    result = RefineResult(understood_request=str(data.get("understood_request", "")).strip())
    issue_text = _norm(f"{issue.title}\n{issue.body}")

    for raw in _list(data, "acceptance_criteria"):
        text = str(raw.get("text", "")) if isinstance(raw, dict) else ""
        quote = str(raw.get("quote", "")) if isinstance(raw, dict) else ""
        if quote and _norm(quote) in issue_text:
            result.criteria.append(Criterion(text, quote))
        else:
            result.dropped.append(("criterion", text, "quote not found in the issue"))

    for raw in _list(data, "code_facts"):
        fact, reason = _verified_fact(raw, ctx)
        if fact:
            result.facts.append(fact)
        else:
            text = str(raw.get("statement", "")) if isinstance(raw, dict) else str(raw)
            result.dropped.append(("code fact", text, reason))

    done = data.get("possibly_already_done")
    if done:
        fact, reason = _verified_fact(done, ctx)
        if fact:
            result.possibly_done = fact
        else:
            text = str(done.get("statement", "")) if isinstance(done, dict) else str(done)
            result.dropped.append(("already-done claim", text, reason))

    for raw in _list(data, "questions"):
        if isinstance(raw, dict) and raw.get("question"):
            options = [str(o) for o in raw.get("options") or []]
            result.questions.append(
                Question(
                    str(raw["question"]),
                    str(raw.get("why", "")),
                    str(raw.get("kind", "scope")),
                    bool(raw.get("blocking", False)),
                    str(raw.get("default", "")),
                    options,
                )
            )
    result.questions.sort(key=lambda q: (not q.blocking, KIND_ORDER.get(q.kind, 9)))

    for raw in _list(data, "suggestions"):
        if isinstance(raw, dict) and raw.get("text"):
            result.suggestions.append(Idea(str(raw["text"]), str(raw.get("reason", ""))))

    for raw in _list(data, "decisions"):
        if isinstance(raw, dict) and raw.get("decision"):
            result.decisions.append(Decision(str(raw["decision"]), str(raw.get("why", ""))))

    for path in _list(data, "likely_files"):
        if path in ctx.tracked:
            result.likely_files.append(path)
        else:
            result.dropped.append(("file", str(path), "not a file in the repo"))
    for n in _list(data, "duplicate_of"):
        if isinstance(n, int) and n in known_issue_numbers:
            result.duplicate_of.append(n)
        else:
            result.dropped.append(("duplicate", str(n), "not a listed issue number"))
    return result


def improved_text(result: RefineResult) -> str:
    """The part of the output that claims to be the issue: request + criteria only."""
    lines = [result.understood_request] if result.understood_request else []
    lines += [f"- {c.text}" for c in result.criteria]
    return "\n".join(lines)


def invention_check(
    decider: Decider, issue: Issue, result: RefineResult
) -> tuple[float, dict[str, float], dict[str, float], dict[str, str]]:
    """Scores from Jev: (overall invented, invented per statement, important per statement, statements).

    Invented = adds something the issue does not state. Important = a wrong guess would be costly or
    hard to revert, or it is a decision only the owner can make.
    """
    statements: dict[str, str] = {}
    if result.understood_request:
        statements["request"] = result.understood_request
    for i, c in enumerate(result.criteria, 1):
        statements[f"criterion_{i}"] = c.text
    questions: dict[str, YesNo] = {
        "overall": YesNo(
            "Does the improved text add any requirement, feature, behaviour or acceptance "
            "criterion that the original issue does not state?"
        )
    }
    for key in statements:
        questions[key] = YesNo(
            f"Does statements.{key} add any requirement, feature, behaviour or restriction "
            "that the original issue does not state?"
        )
        questions[f"important_{key}"] = YesNo(
            f"Would a wrong guess about statements.{key} be costly or hard to revert, or is it a "
            "decision only the product owner can make (naming, scope, user-visible behaviour, "
            "pricing, data, security)?"
        )
    state = {
        "original": {"title": issue.title, "body": issue.body},
        "improved": improved_text(result),
        "statements": statements,
    }
    answers = decider.ask(state, questions)
    invented = {key: answers[key].noul or 0.0 for key in statements}
    important = {key: answers[f"important_{key}"].noul or 0.0 for key in statements}
    return answers["overall"].noul or 0.0, invented, important, statements


def serious_statements(result: RefineResult) -> list[tuple[str, str, float, float]]:
    return [
        (k, result.invention_statements[k], result.invention_items[k], result.importance_items[k])
        for k in result.serious
    ]


def revise_prompt(
    base_user: str, previous_raw: str, serious: list[tuple[str, str, float, float]]
) -> str:
    lines = "\n".join(
        f'- {key}: "{text}" (adds something: {inv:.2f}, important: {imp:.2f})'
        for key, text, inv, imp in serious
    )
    return (
        f"{base_user}\n\nYOUR PREVIOUS ANSWER\n{previous_raw}\n\nREVISE\n"
        "An independent checker judged that these statements add requirements, features, "
        "behaviours or restrictions that the issue does not state, and that getting them wrong "
        f"would matter:\n{lines}\n"
        "Revise your answer. Remove each flagged statement, or rewrite it so that it only "
        "restates what the issue says, and turn what needs deciding into a question. Keep "
        "what was fine. Return the full JSON object again."
    )


def build_user_prompt(issue: Issue, ctx: Context, requested: list[str] | None = None) -> str:
    number = f" #{issue.number}" if issue.number else ""
    body = issue.body.strip() or "(empty)"
    return (
        f"ISSUE{number}\nTitle: {issue.title}\nBody:\n{body}\n\n"
        f"CONTEXT (repository files; line numbers are not part of the files)\n{ctx.text}"
        + (
            f"\nNOTE: you asked for {', '.join(requested)}; they are included above. "
            "This is your final answer: do not ask for more files.\n"
            if requested
            else ""
        )
    )


MAX_FILES_WANTED = 6


class _Usage:
    def __init__(self) -> None:
        self.tokens_in = 0
        self.tokens_out = 0
        self.cost: float | None = 0.0

    def add(self, llm: LLM) -> None:
        usage = getattr(llm, "last_usage", {}) or {}
        self.tokens_in += usage.get("input_tokens", 0)
        self.tokens_out += usage.get("output_tokens", 0)
        call_cost = llm.last_cost_usd
        self.cost = None if (self.cost is None or call_cost is None) else self.cost + call_cost


def _checked(
    result: RefineResult, issue: Issue, decider: Decider | None, caution: Caution
) -> RefineResult:
    if decider is None:
        return result
    overall, invented, important, statements = invention_check(decider, issue, result)
    result.invention_p, result.invention_items = overall, invented
    result.importance_items, result.invention_statements = important, statements
    result.importance_threshold = caution.importance_threshold()
    for key in statements:
        if invented[key] >= INVENTION_FLAG_FROM:
            if important[key] >= result.importance_threshold:
                result.serious.append(key)
            else:
                result.small.append(key)
    return result


def refine_issue(
    issue: Issue,
    repo_path: str | Path,
    llm: LLM,
    decider: Decider | None,
    *,
    model: str,
    other_issues: list[tuple[int, str]] | None = None,
    revise: bool = True,
    caution: Caution | None = None,
) -> RefineResult:
    """One pass, one more if the model asks for files, and one revise pass if Jev flags inventions."""
    known = {n for n, _ in other_issues or []}
    caution = caution or Caution()
    ctx = gather_context(repo_path, issue.title, issue.body, other_issues)
    requested: list[str] = []
    usage = _Usage()
    for round_no in (1, 2):
        user = build_user_prompt(issue, ctx, requested)
        raw = llm.complete(SYSTEM, user, model=model)
        usage.add(llm)
        data = parse_json(raw)
        wanted = [
            f
            for f in _list(data, "files_wanted")
            if isinstance(f, str) and f in ctx.tracked and f not in ctx.sources
        ][:MAX_FILES_WANTED]
        if not wanted or round_no == 2:
            break
        requested = wanted
        ctx = gather_context(
            repo_path, issue.title, issue.body, other_issues, extra_files=requested
        )
    result = _checked(ground(data, issue, ctx, known), issue, decider, caution)

    if revise and decider is not None and result.invention_flagged:
        first_p = result.invention_p
        raw2 = llm.complete(
            SYSTEM, revise_prompt(user, raw, serious_statements(result)), model=model
        )
        usage.add(llm)
        try:
            revised = _checked(ground(parse_json(raw2), issue, ctx, known), issue, decider, caution)
        except RefineError:
            revised = None  # keep the first answer, still flagged
        if revised is not None:
            revised.revised, revised.invention_before = True, first_p
            result = revised

    result.model = model
    result.cost_usd = usage.cost
    result.files_shown = ctx.files_used
    result.files_requested = requested
    result.rounds = round_no
    result.tokens_in, result.tokens_out = usage.tokens_in, usage.tokens_out
    return result


def render_markdown(result: RefineResult, issue: Issue) -> str:
    out = [
        f"### Nightshift refinement for: {issue.title}",
        f"_Draft for review. The original issue is unchanged. Model: {result.model}._",
        "",
        "**Understood request**",
        result.understood_request or "_(none)_",
        "",
    ]
    if result.questions:
        out.append("**Questions for you**")
        for i, q in enumerate(result.questions, 1):
            tags = [q.kind.replace("_", " ")] + (["blocking"] if q.blocking else [])
            line = f"{i}. [{', '.join(tags)}] {q.question}"
            if q.why:
                line += f" _(why: {q.why})_"
            out.append(line)
            if q.default:
                out.append(f"   - proceeds with this unless you say otherwise: {q.default}")
            if q.options:
                out.append(f"   - suggested options: {' / '.join(q.options)}")
        out.append("")
    if result.criteria:
        out.append("**Acceptance criteria (each taken from the issue text)**")
        for i, c in enumerate(result.criteria, 1):
            tag = (
                "  _(small addition, not in the issue; revert if you disagree)_"
                if f"criterion_{i}" in result.small
                else ""
            )
            out.append(f'- [ ] {c.text}  _(from: "{c.quote}")_{tag}')
        out.append("")
    if result.facts:
        out.append("**What the code shows**")
        out += [f"- {f.statement} (`{f.source}`)" for f in result.facts]
        out.append("")
    if result.likely_files:
        out += ["**Likely files**", ", ".join(f"`{f}`" for f in result.likely_files), ""]
    if result.possibly_done:
        d = result.possibly_done
        out += ["**Possibly already done**", f"- {d.statement} (`{d.source}`)", ""]
    if result.duplicate_of:
        out += ["**Possible duplicates**", ", ".join(f"#{n}" for n in result.duplicate_of), ""]
    if result.decisions:
        out.append(
            "**Decisions Nightshift made itself (obvious or unimportant; revert if you disagree)**"
        )
        out += [f"- {d.text}" + (f" _(why: {d.why})_" if d.why else "") for d in result.decisions]
        out.append("")
    if result.suggestions:
        out.append("**Suggestions (optional, not part of the issue; keep / drop / alter)**")
        out += [
            f"- {s.text}" + (f" _(why: {s.reason})_" if s.reason else "")
            for s in result.suggestions
        ]
        out.append("")
    out.append("**Checks**")
    if result.invention_p is None:
        out.append("- Invention check (Jev): not run")
    else:
        line = (
            f"- Invention check (Jev): total {result.invention_p:.2f}; "
            f"serious (adds something important): {len(result.serious)}; "
            f"small additions disclosed: {len(result.small)}"
        )
        if result.revised and result.invention_before is not None:
            line += f" (after one revise pass; total was {result.invention_before:.2f})"
        out.append(line)
        out.append(
            f"  - importance threshold {result.importance_threshold:.2f} (lower = more cautious)"
        )
        if result.invention_flagged:
            out.append(
                "  - STILL FLAGGED, review these:"
                if result.revised
                else "  - FLAGGED, review these:"
            )
            out += [
                f'    - {key}: "{text[:100]}" (adds something {inv:.2f}, important {imp:.2f})'
                for key, text, inv, imp in serious_statements(result)
            ]
    if result.dropped:
        out.append(f"- Dropped because they could not be verified: {len(result.dropped)}")
        out += [f"  - {kind}: {text[:90]} ({reason})" for kind, text, reason in result.dropped]
    shown = f"{len(result.files_shown)} files shown"
    if result.files_requested:
        shown += f", {len(result.files_requested)} requested in a second pass"
    cost = f", about ${result.cost_usd:.3f}" if result.cost_usd else ""
    out.append(f"- {shown}; {result.tokens_in} tokens in, {result.tokens_out} out{cost}")
    return "\n".join(out)
