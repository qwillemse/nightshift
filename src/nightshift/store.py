"""SQLite run log: what Nightshift suggested, and what humans changed afterwards."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS suggestions (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    repo TEXT NOT NULL,
    issue INTEGER NOT NULL,
    grp TEXT NOT NULL,
    value TEXT NOT NULL,
    confidence REAL
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    kind TEXT NOT NULL,
    repo TEXT,
    issue INTEGER,
    payload TEXT NOT NULL
);
"""


@dataclass
class Correction:
    group: str
    suggested: str
    actual: str


class Store:
    def __init__(self, path: str | Path = "nightshift.db"):
        self.db = sqlite3.connect(str(path))
        self.db.executescript(SCHEMA)

    def record_suggestion(
        self, repo: str, issue: int, group: str, value: str, confidence: float | None
    ) -> None:
        self.db.execute(
            "INSERT INTO suggestions (ts, repo, issue, grp, value, confidence) VALUES (?,?,?,?,?,?)",
            (time.time(), repo, issue, group, value, confidence),
        )
        self.db.commit()

    def latest_suggestions(self, repo: str, issue: int) -> dict[str, str]:
        rows = self.db.execute(
            "SELECT grp, value FROM suggestions WHERE repo=? AND issue=? ORDER BY id",
            (repo, issue),
        ).fetchall()
        return dict(rows)  # later rows overwrite earlier ones

    def log_run(self, kind: str, payload: dict, repo: str | None = None, issue: int | None = None):
        self.db.execute(
            "INSERT INTO runs (ts, kind, repo, issue, payload) VALUES (?,?,?,?,?)",
            (time.time(), kind, repo, issue, json.dumps(payload)),
        )
        self.db.commit()

    def detect_corrections(
        self, repo: str, issue: int, current_labels: list[str]
    ) -> list[Correction]:
        """Groups where the label now on the issue differs from what Nightshift suggested."""
        current = {}
        for label in current_labels:
            if ":" in label:
                group, value = label.split(":", 1)
                current[group] = value
        out = []
        for group, suggested in self.latest_suggestions(repo, issue).items():
            actual = current.get(group)
            if actual is not None and actual != suggested:
                out.append(Correction(group, suggested, actual))
        return out
