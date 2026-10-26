"""Gather the repo context a refining model needs: docs, file tree, relevant files.

Deterministic on purpose: what the model sees is exactly what we can check its quotes against.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

DOC_FILES = ("CLAUDE.md", "README.md")
TEXT_SUFFIXES = {".py", ".html", ".css", ".js", ".md", ".toml", ".yml", ".yaml", ".txt", ".cfg"}
SKIP_NAMES = {"uv.lock", "package-lock.json", "LICENSE"}
STOPWORDS = {
    "able", "also", "have", "with", "that", "this", "from", "into", "when", "what", "which",
    "there", "their", "would", "should", "could", "make", "more", "some", "than", "then",
    "them", "they", "your", "about", "maybe", "like", "just", "only", "even", "need", "want",
    "does", "done", "being", "were", "will", "using", "used", "different", "certain",
}  # fmt: skip


@dataclass
class Context:
    text: str  # what goes into the prompt
    sources: dict[str, str] = field(default_factory=dict)  # path -> raw text shown to the model
    tracked: list[str] = field(default_factory=list)
    files_used: list[str] = field(default_factory=list)


def tracked_files(repo: Path) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(repo), "ls-files"], capture_output=True, text=True, check=True
    ).stdout
    return [line for line in out.splitlines() if line]


def keywords(text: str) -> list[str]:
    seen: list[str] = []
    for word in re.findall(r"[a-z][a-z_]{3,}", text.lower()):
        if word in STOPWORDS:
            continue
        stem = word[:-1] if word.endswith("s") and len(word) > 4 else word
        if stem not in seen:
            seen.append(stem)
    return seen


def _truncate(content: str, limit: int) -> str:
    if len(content) <= limit:
        return content
    cut = content[:limit].rsplit("\n", 1)[0]
    return cut + "\n[... truncated ...]"


def _numbered(content: str) -> str:
    return "\n".join(f"{i:>4}| {line}" for i, line in enumerate(content.splitlines(), 1))


def _read(repo: Path, rel: str) -> str | None:
    path = repo / rel
    try:
        return path.read_text()
    except (OSError, UnicodeDecodeError):
        return None


def gather_context(
    repo_path: str | Path,
    title: str,
    body: str,
    other_issues: list[tuple[int, str]] | None = None,
    max_chars: int = 45000,
    max_files: int = 6,
    per_file: int = 7000,
    extra_files: list[str] | None = None,
) -> Context:
    repo = Path(repo_path)
    tracked = tracked_files(repo)
    ctx = Context(text="", tracked=tracked)
    parts: list[str] = []
    used = 0

    def add_file(rel: str, limit: int) -> bool:
        nonlocal used
        content = _read(repo, rel)
        if content is None:
            return False
        shown = _truncate(content, limit)
        block = f"## FILE {rel}\n{_numbered(shown)}\n"
        if used + len(block) > max_chars:
            return False
        parts.append(block)
        ctx.sources[rel] = shown
        ctx.files_used.append(rel)
        used += len(block)
        return True

    docs = [f for f in DOC_FILES if f in tracked]
    docs += [f for f in tracked if f.startswith(".github/ISSUE_TEMPLATE/") and f.endswith(".md")]
    for rel in docs:
        add_file(rel, 6000)

    tree = "## FILE TREE\n" + "\n".join(tracked[:200]) + "\n"
    parts.append(tree)
    used += len(tree)

    if other_issues:
        listing = "## OTHER ISSUES\n" + "\n".join(f"#{n} {t}" for n, t in other_issues[:60]) + "\n"
        parts.append(listing)
        used += len(listing)

    for rel in extra_files or []:  # files the model asked for: shown first, not scored
        if rel in tracked and rel not in ctx.sources:
            add_file(rel, per_file)

    kws = keywords(f"{title} {title} {body}")
    scored: list[tuple[int, str]] = []
    for rel in tracked:
        if rel in ctx.sources or Path(rel).name in SKIP_NAMES:
            continue
        if Path(rel).suffix not in TEXT_SUFFIXES:
            continue
        content = _read(repo, rel)
        if content is None:
            continue
        lowered = content.lower()
        score = sum(lowered.count(k) for k in kws) + 5 * sum(k in rel.lower() for k in kws)
        if score:
            scored.append((score, rel))
    for _, rel in sorted(scored, reverse=True)[:max_files]:
        add_file(rel, per_file)

    ctx.text = "\n".join(parts)
    return ctx
