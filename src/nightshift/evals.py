"""Eval harness: compare suggested labels with expected labels."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from nightshift.decider import Decider
from nightshift.triage import BASELINE_EXECUTOR, EXECUTORS, Issue, Suggestion, triage

BINS = [(0.0, 0.5), (0.5, 0.8), (0.8, 1.0001)]


@dataclass
class Miss:
    case: str
    group: str
    expected: str
    got: str
    confidence: float | None


@dataclass
class Report:
    total: dict[str, int] = field(default_factory=dict)
    correct: dict[str, int] = field(default_factory=dict)
    misses: list[Miss] = field(default_factory=list)
    # (confidence, was_correct) for groups where the model reports confidence
    calibration: list[tuple[float, bool]] = field(default_factory=list)

    def accuracy(self, group: str) -> float:
        return self.correct.get(group, 0) / self.total[group] if self.total.get(group) else 0.0

    def calibration_table(self) -> list[tuple[str, int, float, float]]:
        rows = []
        for lo, hi in BINS:
            items = [(c, ok) for c, ok in self.calibration if lo <= c < hi]
            if items:
                avg_conf = sum(c for c, _ in items) / len(items)
                acc = sum(ok for _, ok in items) / len(items)
                rows.append((f"{lo:.1f}-{min(hi, 1.0):.1f}", len(items), avg_conf, acc))
        return rows

    def format(self) -> str:
        lines = []
        for group in sorted(self.total):
            lines.append(
                f"{group:<9} {self.correct.get(group, 0)}/{self.total[group]} "
                f"({self.accuracy(group):.0%})"
            )
        if self.misses:
            lines.append("\nmisses:")
            for m in self.misses:
                conf = f" conf {m.confidence:.2f}" if m.confidence is not None else ""
                lines.append(f"  {m.case}: {m.group} expected {m.expected}, got {m.got}{conf}")
        table = self.calibration_table()
        if table:
            lines.append("\ncalibration (confidence bin, n, mean confidence, accuracy):")
            for label, n, conf, acc in table:
                lines.append(f"  {label}  n={n}  conf {conf:.2f}  acc {acc:.2f}")
        return "\n".join(lines)


def load_cases(path: str | Path) -> list[dict]:
    return json.loads(Path(path).read_text())


def run_eval(cases: list[dict], decider: Decider) -> Report:
    """Each case: {"id", "title", "body", "expected": {group: value}}. Groups may be partial."""
    report = Report()
    for case in cases:
        suggestion: Suggestion = triage(Issue(case["title"], case.get("body", "")), decider)
        for group, expected in case["expected"].items():
            got = suggestion.labels.get(group, "")
            conf = suggestion.confidence.get(group)
            report.total[group] = report.total.get(group, 0) + 1
            ok = got == expected
            if ok:
                report.correct[group] = report.correct.get(group, 0) + 1
            else:
                report.misses.append(Miss(case["id"], group, expected, got, conf))
            if conf is not None:
                report.calibration.append((conf, ok))
    return report


@dataclass
class ExecutorRow:
    case: str
    expected_size: str
    baseline: str
    pick: str
    confidence: float | None


def compare_executors(
    cases: list[dict], decider: Decider, budget_usd: float | None = None
) -> list[ExecutorRow]:
    """Jev's model pick vs the static table, using the *expected* size for the baseline.

    There is no ground truth for which model is best; this only shows how Jev's intuition
    relates to the table. Real evidence needs run logs (outcome, cost, retries).
    """
    rows = []
    for case in cases:
        suggestion = triage(
            Issue(case["title"], case.get("body", "")), decider, budget_usd, force_executor=True
        )
        size = case["expected"]["size"]
        rows.append(
            ExecutorRow(
                case["id"],
                size,
                BASELINE_EXECUTOR[size],
                suggestion.labels["executor"],
                suggestion.confidence.get("executor"),
            )
        )
    return rows


def format_executor_comparison(title: str, rows: list[ExecutorRow]) -> str:
    rank = {name: i for i, name in enumerate(EXECUTORS)}  # cheapest -> strongest
    agree = sum(r.pick == r.baseline for r in rows)
    cheaper = sum(rank[r.pick] < rank[r.baseline] for r in rows)
    stronger = sum(rank[r.pick] > rank[r.baseline] for r in rows)
    lines = [
        f"== {title}",
        f"agrees with table {agree}/{len(rows)}, cheaper {cheaper}, stronger {stronger}",
    ]
    for size in ("trivial", "small", "medium", "large"):
        group = [r for r in rows if r.expected_size == size]
        if group:
            counts = {m: sum(r.pick == m for r in group) for m in EXECUTORS}
            picks = ", ".join(f"{m.replace('claude-', '')} {n}" for m, n in counts.items() if n)
            base = BASELINE_EXECUTOR[size].replace("claude-", "")
            lines.append(f"  {size:<8} n={len(group):<2} table={base:<12} jev: {picks}")
    return "\n".join(lines)
