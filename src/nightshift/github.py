"""GitHub access through the `gh` CLI."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable

from nightshift.store import Store
from nightshift.thread import Comment, Thread, build_thread
from nightshift.triage import GROUPS, Issue, Suggestion

Runner = Callable[..., subprocess.CompletedProcess]


class GhError(RuntimeError):
    pass


class Gh:
    def __init__(self, repo: str, run: Runner = subprocess.run):
        self.repo = repo
        self._run = run

    def _gh(self, *args: str) -> str:
        proc = self._run(["gh", *args], capture_output=True, text=True)
        if proc.returncode != 0:
            raise GhError(f"gh {' '.join(args[:2])} failed: {proc.stderr.strip()}")
        return proc.stdout

    def get_issue(self, number: int) -> Issue:
        out = self._gh(
            "issue", "view", str(number), "--repo", self.repo, "--json", "number,title,body,labels"
        )
        return _issue_from_json(json.loads(out))

    def list_issues(self, state: str = "open", limit: int = 100) -> list[Issue]:
        out = self._gh(
            "issue", "list", "--repo", self.repo, "--state", state, "--limit", str(limit),
            "--json", "number,title,body,labels",
        )  # fmt: skip
        return [_issue_from_json(i) for i in json.loads(out)]

    def ensure_label(self, name: str) -> None:
        self._gh("label", "create", name, "--repo", self.repo, "--force")

    def add_labels(self, number: int, names: list[str]) -> None:
        for name in names:
            self.ensure_label(name)
        self._gh("issue", "edit", str(number), "--repo", self.repo, "--add-label", ",".join(names))

    def comments(self, number: int) -> list[Comment]:
        out = self._gh("issue", "view", str(number), "--repo", self.repo, "--json", "comments")
        return [
            Comment(
                author=(c.get("author") or {}).get("login", ""),
                association=c.get("authorAssociation", "NONE"),
                body=c.get("body", ""),
                created_at=c.get("createdAt", ""),
                url=c.get("url", ""),
            )
            for c in json.loads(out).get("comments", [])
        ]

    def thread(self, number: int) -> Thread:
        return build_thread(self.comments(number))

    def comment(self, number: int, body: str) -> None:
        self._gh("issue", "comment", str(number), "--repo", self.repo, "--body", body)


def _issue_from_json(data: dict) -> Issue:
    return Issue(
        title=data["title"],
        body=data.get("body") or "",
        number=data["number"],
        labels=[label["name"] for label in data.get("labels", [])],
    )


def apply_suggestion(
    gh: Gh, issue: Issue, suggestion: Suggestion, store: Store | None
) -> list[str]:
    """Add suggested labels for groups the issue has no label in. Never overwrites a human."""
    if issue.number is None:
        raise ValueError("issue has no number")
    taken = {label.split(":", 1)[0] for label in issue.labels if ":" in label}
    to_add = []
    for group in GROUPS:
        value = suggestion.labels.get(group)
        if not value or group in taken:
            continue
        to_add.append(f"{group}:{value}")
        if store:
            store.record_suggestion(
                gh.repo, issue.number, group, value, suggestion.confidence.get(group)
            )
    if to_add:
        gh.add_labels(issue.number, to_add)
    return to_add
