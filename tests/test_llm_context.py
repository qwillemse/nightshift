from __future__ import annotations

import json
import subprocess

import pytest

from nightshift.context import gather_context, keywords
from nightshift.decider import DeciderError
from nightshift.llm import AnthropicLLM, ClaudeCLI, LLMError


def make_repo(tmp_path, files: dict[str, str]):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for rel, content in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-qm",
            "x",
        ],
        check=True,
    )
    return tmp_path


FILES = {
    "CLAUDE.md": "# Project\nRun tests with make test.\n",
    "README.md": "Readme text. Known limitations: no loading indicator.\n",
    ".github/ISSUE_TEMPLATE/task.md": "## What\n",
    "src/app/servings.py": "def scale_servings(recipe, servings):\n    return recipe\n",
    "src/app/other.py": "def unrelated():\n    pass\n",
    "uv.lock": "servings servings servings\n",
}


def test_keywords_stem_and_drop_stopwords():
    assert keywords("Be able to change amount of servings") == ["change", "amount", "serving"]


def test_context_has_docs_tree_and_the_relevant_file_only(tmp_path):
    repo = make_repo(tmp_path, FILES)
    ctx = gather_context(repo, "Change amount of servings", "", other_issues=[(3, "Servings")])
    assert "## FILE CLAUDE.md" in ctx.text and "## FILE README.md" in ctx.text
    assert ".github/ISSUE_TEMPLATE/task.md" in ctx.sources
    assert "## FILE TREE" in ctx.text and "src/app/other.py" in ctx.text
    assert "#3 Servings" in ctx.text
    assert "src/app/servings.py" in ctx.sources
    assert "src/app/other.py" not in ctx.sources  # in the tree, but its content is not shown
    assert "uv.lock" not in ctx.sources


def test_shown_sources_are_raw_text_without_line_numbers(tmp_path):
    repo = make_repo(tmp_path, FILES)
    ctx = gather_context(repo, "servings", "")
    assert ctx.sources["src/app/servings.py"] == FILES["src/app/servings.py"]
    assert "   1| def scale_servings" in ctx.text


def test_anthropic_llm_joins_text_blocks_and_retries():
    statuses = iter([529, 200])
    body = {
        "content": [{"type": "text", "text": "he"}, {"type": "text", "text": "llo"}],
        "usage": {},
    }
    sleeps = []

    def transport(url, headers, payload, timeout):
        assert headers["x-api-key"] == "k" and url.endswith("/v1/messages")
        assert json.loads(payload)["system"] == "sys"
        status = next(statuses)
        return status, json.dumps(body).encode() if status == 200 else b"busy"

    llm = AnthropicLLM("k", transport=transport, sleep=sleeps.append)
    assert llm.complete("sys", "hi", model="m") == "hello"
    assert sleeps == [1]


def test_anthropic_llm_error_status():
    llm = AnthropicLLM("k", retries=0, transport=lambda *a: (401, b"nope"))
    with pytest.raises(LLMError, match="401"):
        llm.complete("s", "u", model="m")


def test_anthropic_llm_network_error_is_wrapped():
    def boom(*a):
        raise DeciderError("network down")

    with pytest.raises(LLMError, match="network down"):
        AnthropicLLM("k", transport=boom).complete("s", "u", model="m")


def fake_run(stdout: str, returncode: int = 0):
    calls = []

    def run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr="")

    run.calls = calls
    return run


def test_claude_cli_builds_a_toolless_headless_command():
    out = json.dumps({"is_error": False, "result": "answer", "total_cost_usd": 0.02})
    run = fake_run(out)
    cli = ClaudeCLI(binary="claude", run=run)
    assert cli.complete("sys", "user text", model="claude-sonnet-5-5") == "answer"
    cmd, kwargs = run.calls[0]
    assert cmd[:2] == ["claude", "-p"]
    assert cmd[cmd.index("--tools") + 1] == ""
    assert "--no-session-persistence" in cmd
    assert cmd[cmd.index("--model") + 1] == "claude-sonnet-5-5"
    assert kwargs["input"] == "user text"
    assert cli.last_cost_usd == 0.02


def test_claude_cli_reports_login_errors():
    out = json.dumps({"is_error": True, "result": "Not logged in"})
    with pytest.raises(LLMError, match="Not logged in"):
        ClaudeCLI(binary="claude", run=fake_run(out)).complete("s", "u", model="m")


def test_claude_cli_non_json_output():
    with pytest.raises(LLMError, match="no JSON"):
        ClaudeCLI(binary="claude", run=fake_run("garbage", 1)).complete("s", "u", model="m")
