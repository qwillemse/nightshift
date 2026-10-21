"""Tiny .env loader and decider factory."""

from __future__ import annotations

import os
from pathlib import Path

from nightshift.decider import JevDecider

ROOT = Path(__file__).resolve().parents[2]


def load_env(path: Path | None = None) -> None:
    env_file = path or ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def jev_from_env() -> JevDecider:
    load_env()
    key = os.environ.get("TYPESAFE_API_KEY")
    if not key:
        raise SystemExit("TYPESAFE_API_KEY not set (put it in .env, see .env.example)")
    return JevDecider(
        api_key=key,
        base_url=os.environ.get("JEV_BASE_URL", "https://api.typesafe.ai"),
        model=os.environ.get("JEV_MODEL", "jev-latest"),
    )
