"""Command line entry point: `nightshift <command>`."""

from __future__ import annotations

import argparse
import json

from nightshift.config import DEFAULT_REFINE_MODEL, jev_from_env, llm_from_env
from nightshift.evals import (
    compare_executors,
    format_executor_comparison,
    load_cases,
    run_eval,
)
from nightshift.github import Gh, apply_suggestion
from nightshift.llm import LLMError
from nightshift.refine import Caution, ask_first_issue, refine_issue, render_markdown
from nightshift.store import Store
from nightshift.thread import marker
from nightshift.triage import Issue, triage


def cmd_triage(args: argparse.Namespace) -> None:
    gh = Gh(args.repo)
    issue = gh.get_issue(args.issue)
    suggestion = triage(issue, jev_from_env())
    print(json.dumps({"disposition": suggestion.disposition, "refine_reasons": suggestion.refine_reasons, "labels": suggestion.labels, "confidence": suggestion.confidence,
                      "detail": suggestion.detail}, indent=2))  # fmt: skip
    if args.apply:
        store = Store(args.db)
        added = apply_suggestion(gh, issue, suggestion, store)
        store.log_run("triage", {"added": added}, args.repo, args.issue)
        print(f"applied: {added or 'nothing (all groups already labeled)'}")


def cmd_eval(args: argparse.Namespace) -> None:
    report = run_eval(load_cases(args.file), jev_from_env())
    print(report.format())


def cmd_models(args: argparse.Namespace) -> None:
    cases = load_cases(args.file)
    jev = jev_from_env()
    for budget in args.budget or [None]:
        rows = compare_executors(cases, jev, budget)
        label = "no budget given" if budget is None else f"budget ${budget:g} per issue"
        print(format_executor_comparison(label, rows))
        if args.rows:
            for r in rows:
                conf = f"{r.confidence:.2f}" if r.confidence is not None else "-"
                print(
                    f"    {r.case:<12} {r.expected_size:<8} table={r.baseline:<18} jev={r.pick} ({conf})"
                )


def _refine_inputs(args: argparse.Namespace):
    """Yield (issue, thread, other_issues): from live GitHub (--issue) or a cases file (--case)."""
    if bool(args.case) == bool(args.issue):
        raise SystemExit("give either --issue (live GitHub, reads comments) or --case (cases file)")
    if args.issue:
        if not args.gh_repo:
            raise SystemExit("--issue needs --gh-repo owner/name")
        gh = Gh(args.gh_repo)
        open_issues = gh.list_issues("open")
        for number in args.issue:
            issue = gh.get_issue(number)
            thread = gh.thread(number)
            issue.discussion = thread.as_state()
            others = [(i.number, i.title) for i in open_issues if i.number != number and i.number]
            yield issue, thread, others
    else:
        if args.post:
            raise SystemExit("--post needs --issue, so the live comments are taken into account")
        cases = {c["id"]: c for c in load_cases(args.cases)}
        for case_id in args.case:
            case = cases[case_id]
            others = [
                (c["number"], c["title"])
                for c in cases.values()
                if c.get("on_github") and c.get("number") and c["id"] != case_id
            ]
            yield Issue(case["title"], case["body"], number=case.get("number")), None, others


def cmd_refine(args: argparse.Namespace) -> None:
    """Refine issues. Prints by default; --post adds a comment on GitHub (never edits the issue)."""
    llm, jev = llm_from_env(), jev_from_env()
    gh = Gh(args.gh_repo) if args.post else None
    for issue, thread, others in _refine_inputs(args):
        if thread is not None and not thread.needs_new_round:
            print(f"#{issue.number}: no new owner comments since the last refinement; skipped\n")
            continue
        quick = args.ask_first and (thread is None or thread.round == 0)
        try:
            if quick:
                result = ask_first_issue(
                    issue,
                    args.repo_path,
                    llm,
                    model=args.model,
                    other_issues=others,
                    thread=thread,
                )
            else:
                tri = triage(issue, jev)  # risk, size and confidence decide how cautious to be
                caution = Caution(
                    tri.labels["risk"], tri.labels["size"], tri.confidence.get("size")
                )
                result = refine_issue(
                    issue,
                    args.repo_path,
                    llm,
                    jev,
                    model=args.model,
                    other_issues=others,
                    caution=caution,
                    thread=thread,
                )
        except LLMError as e:
            raise SystemExit(
                f"{e}\nLog in by running `claude` and typing /login, "
                "or set ANTHROPIC_API_KEY in .env."
            ) from None
        text = render_markdown(result, issue, short=not args.long)
        if thread is not None and thread.excluded:
            text += f"\n- Ignored {thread.excluded} comment(s) from accounts that are not the owner or collaborators"
        print(text)
        if gh is not None and issue.number:
            gh.comment(issue.number, f"{marker(result.thread_round, result.mode)}\n{text}")
            print(f"(posted as round {result.thread_round} on #{issue.number})")
        print("\n" + "-" * 60 + "\n")


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

    p = sub.add_parser("models", help="compare Jev's model picks with the baseline table")
    p.add_argument("file")
    p.add_argument("--budget", type=float, action="append", help="USD per issue; repeatable")
    p.add_argument("--rows", action="store_true", help="show every case")
    p.set_defaults(func=cmd_models)

    p = sub.add_parser(
        "refine", help="refine issues from a cases file; prints, or posts with --post"
    )
    p.add_argument("--cases", help="cases JSON, e.g. evals/recipe_issues.json (with --case)")
    p.add_argument("--case", action="append", help="case id from the cases file, repeatable")
    p.add_argument(
        "--issue", type=int, action="append", help="live GitHub issue number, repeatable"
    )
    p.add_argument("--repo-path", required=True, help="local checkout of the target repo")
    p.add_argument("--model", default=DEFAULT_REFINE_MODEL)
    p.add_argument("--gh-repo", help="owner/name of the GitHub repo (for --issue and --post)")
    p.add_argument(
        "--ask-first",
        action="store_true",
        help="round 1 is a cheap questions-only pass (docs and file tree); later rounds read code",
    )
    p.add_argument(
        "--long",
        action="store_true",
        help="show request, criteria and code facts on top instead of folding them away",
    )
    p.add_argument(
        "--post", action="store_true", help="comment on the GitHub issue; never edits it"
    )
    p.set_defaults(func=cmd_refine)

    p = sub.add_parser("corrections", help="show labels a human changed after we suggested")
    p.add_argument("--repo", required=True)
    p.add_argument("--issue", type=int, required=True)
    p.set_defaults(func=cmd_corrections)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
