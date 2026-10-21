"""Calls the real Jev API. Run explicitly: uv run pytest -m live"""

from __future__ import annotations

import pytest

from nightshift.config import jev_from_env
from nightshift.triage import Issue, triage

pytestmark = pytest.mark.live


def test_live_triage_returns_all_groups():
    s = triage(Issue("Typo in README", "It says 'instal' instead of 'install'."), jev_from_env())
    assert set(s.labels) == {"size", "clarity", "risk", "type", "priority"}
    assert s.labels["size"] in {"trivial", "small", "medium", "large"}
