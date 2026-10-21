"""Command line entry point: `nightshift <command>`."""

from __future__ import annotations

import argparse
import json

from nightshift.config import jev_from_env
from nightshift.evals import load_cases, run_eval
from nightshift.github import Gh, apply_suggestion
from nightshift.store import Store
from nightshift.triage import triage


def cmd_triage(args: argparse.Namespace) -> None:
    gh = Gh(args.repo)
    issue = gh.get_issue(args.issue)
    suggestion = triage(issue, jev_from_env())
    print(json.dumps({"labels": suggestion.labels, "confidence": suggestion.confidence,
                      "detail": suggestion.detail}, indent=2))  # fmt: skip
    if args.apply:
        store = Store(args.db)
        added = apply_suggestion(gh, issue, suggestion, store)
        store.log_run("triage", {"added": added}, args.repo, args.issue)
        print(f"applied: {added or 'nothing (all groups already labeled)'}")


def cmd_eval(args: argparse.Namespace) -> None:
    report = run_eval(load_cases(args.file), jev_from_env())
    print(report.format())


def cmd_corrections(args: argparse.Namespace) -> None:
    issue = Gh(args.repo).get_issue(args.issue)
    found = Store(args.db).detect_corrections(args.repo, args.issue, issue.labels)
    if not found:
        print("no corrections")
    for c in found:
        print(f"{c.group}: suggested {c.suggested}, human set {c.actual}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="nightshift")
    parser.add_argument("--db", default="nightshift.db", help="SQLite run log path")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("triage", help="suggest labels for one GitHub issue")
    p.add_argument("--repo", required=True, help="owner/name")
    p.add_argument("--issue", type=int, required=True)
    p.add_argument("--apply", action="store_true", help="write labels to the issue")
    p.set_defaults(func=cmd_triage)

    p = sub.add_parser("eval", help="score triage against a file of labeled cases")
    p.add_argument("file")
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser("corrections", help="show labels a human changed after we suggested")
    p.add_argument("--repo", required=True)
    p.add_argument("--issue", type=int, required=True)
    p.set_defaults(func=cmd_corrections)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
