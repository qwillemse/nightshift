"""Issue comment threads: who is trusted, what Nightshift already said, which round we are in.

On a public repo anyone can comment, so only comments from the owner, members and
collaborators are treated as the owner's words. Everything else is dropped from the model's
input (counted, never shown), which also blocks instructions smuggled in through comments.
GitHub issue comments are flat, so a "reply" is simply the next comment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

TRUSTED_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}
MARKER_PREFIX = "<!-- nightshift:refinement"
_ROUND = re.compile(r"<!-- nightshift:refinement(?: round=(\d+))?(?: mode=\w+)? -->")


def marker(round_no: int, mode: str = "full") -> str:
    suffix = "" if mode == "full" else f" mode={mode}"
    return f"{MARKER_PREFIX} round={round_no}{suffix} -->"


@dataclass
class Comment:
    author: str
    association: str
    body: str
    created_at: str = ""
    url: str = ""

    @property
    def trusted(self) -> bool:
        return self.association in TRUSTED_ASSOCIATIONS

    @property
    def is_refinement(self) -> bool:
        """A Nightshift refinement: has the marker AND comes from a trusted account."""
        return self.trusted and MARKER_PREFIX in self.body


@dataclass
class Entry:
    source: str  # "owner" or "nightshift"
    text: str
    round: int | None = None


@dataclass
class Thread:
    entries: list[Entry] = field(default_factory=list)
    excluded: int = 0  # comments from untrusted accounts, never shown to the model
    round: int = 0  # number of Nightshift refinement comments so far
    new_human_comments: bool = False  # owner comments after the latest refinement

    @property
    def needs_new_round(self) -> bool:
        return self.round == 0 or self.new_human_comments

    @property
    def owner_text(self) -> str:
        return "\n".join(e.text for e in self.entries if e.source == "owner")

    def as_state(self) -> list[dict[str, str]]:
        return [{"from": e.source, "text": e.text} for e in self.entries]


def build_thread(comments: list[Comment]) -> Thread:
    thread = Thread()
    for comment in comments:
        if not comment.trusted:
            thread.excluded += 1
            continue
        if comment.is_refinement:
            found = _ROUND.search(comment.body)
            thread.round += 1
            number = int(found.group(1)) if found and found.group(1) else thread.round
            text = _ROUND.sub("", comment.body).strip()
            thread.entries.append(Entry("nightshift", text, number))
            thread.new_human_comments = False
        else:
            thread.entries.append(Entry("owner", comment.body.strip()))
            thread.new_human_comments = True
    return thread
