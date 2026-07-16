"""Focused behavior tests for the output-budget PreToolUse guard."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout

import pytest

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]


def _build_event(command: str, *, run_cmd: bool = False) -> dict:
    key = "cmd" if run_cmd else "command"
    tool = "mcp__autoskillit__local__autoskillit__run_cmd" if run_cmd else "Bash"
    return {"tool_name": tool, "tool_input": {key: command}}


def _run_hook(event: dict | None, monkeypatch, *, raw_stdin: str | None = None) -> str:
    from autoskillit.hooks.guards.output_budget_guard import main  # noqa: PLC0415

    stdin_text = raw_stdin if raw_stdin is not None else json.dumps(event)
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin_text))
    output = io.StringIO()
    with pytest.raises(SystemExit), redirect_stdout(output):
        main()
    return output.getvalue()


def _is_denied(output: str) -> bool:
    if not output:
        return False
    payload = json.loads(output)
    return payload["hookSpecificOutput"]["permissionDecision"] == "deny"


@pytest.mark.parametrize(
    "command",
    [
        'rg -n "pat" src/ tests/',
        'grep -r "pat" .',
        "rg -l pat src/",
        "rg --count pat src/",
        "rg -m 5 pat src/",
        "cat sessions.jsonl",
        "sed -n '1,300p' sessions.jsonl",
        "rg pat sessions.jsonl | head -n 200",
        "jq -r '.payload' sessions.jsonl",
        "find / -maxdepth 2 -name '*.py'",
        'bash -c "rg -n pat src/"',
    ],
)
def test_denies_unbounded_r1_r2_r3_shapes(command, monkeypatch, tmp_path):
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "tests").mkdir(exist_ok=True)
    assert _is_denied(_run_hook(_build_event(command), monkeypatch))


@pytest.mark.parametrize("run_cmd", [False, True])
def test_reads_both_command_input_keys(run_cmd, monkeypatch, tmp_path):
    (tmp_path / "src").mkdir()
    assert _is_denied(_run_hook(_build_event("rg pat src/", run_cmd=run_cmd), monkeypatch))


@pytest.mark.parametrize(
    "command",
    [
        "rg pat src/ 2>&1 | head -c 4000",
        "rg pat src/ |& head -c 4000",
        "jq -r .payload sessions.jsonl 2>&1 | head -c 12000",
        "rg -M 500 pat sessions.jsonl 2>&1 | tail -c 12000",
        "rg pat src/ >.autoskillit/temp/search.out 2>&1",
        "rg pat src/ 1>.autoskillit/temp/search.out 2>.autoskillit/temp/search.err",
        "find / -quit 2>.autoskillit/temp/find.err",
        "find / -quit 2>&1 | head -c 4000",
        "rg -n pat single_file.py",
        "git log --oneline -20",
    ],
)
def test_allows_proven_bounded_cases(command, monkeypatch, tmp_path):
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / ".autoskillit" / "temp").mkdir(parents=True, exist_ok=True)
    assert _run_hook(_build_event(command), monkeypatch) == ""


@pytest.mark.parametrize(
    "command",
    [
        "rg pat src/ | head -c 4000",
        "rg pat src/ && head -c 4000",
        "rg pat src/ 2>err.txt",
        "rg pat src/ 2>&1 >.autoskillit/temp/out",
        "rg pat src/ 2>&1 | head -c 12001",
        "rg pat src/ 2>&1 | head -c 0",
        "rg -q pat src/",
        "find / -quit",
        "rg pat src/ 2>&1 | tee .autoskillit/temp/copy | head -c 4000",
        "cat <(rg pat src/)",
        "rg pat src/ >$OUTPUT 2>&1",
    ],
)
def test_denies_false_bounds_and_unknown_shell_shapes(command, monkeypatch, tmp_path):
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / ".autoskillit" / "temp").mkdir(parents=True, exist_ok=True)
    assert _is_denied(_run_hook(_build_event(command), monkeypatch))


def test_small_jsonl_exception_is_narrow(monkeypatch, tmp_path):
    path = tmp_path / "small.jsonl"
    path.write_text('{"value": "small"}\n')

    assert _run_hook(_build_event(f"cat {path.name}"), monkeypatch) == ""
    assert _run_hook(_build_event(f"wc -l {path.name}"), monkeypatch) == ""
    assert _is_denied(_run_hook(_build_event(f"jq -r .value {path.name}"), monkeypatch))
    assert _is_denied(
        _run_hook(_build_event(f"cat {path.name} | awk '{{while (1) print}}'"), monkeypatch)
    )


def test_large_jsonl_is_denied(monkeypatch, tmp_path):
    path = tmp_path / "large.jsonl"
    path.write_text('{"value": "' + ("x" * 5_100) + '"}\n')
    assert _is_denied(_run_hook(_build_event(f"cat {path.name}"), monkeypatch))


def test_symlink_jsonl_cannot_use_small_file_exception(monkeypatch, tmp_path):
    target = tmp_path / "target.jsonl"
    target.write_text("{}\n")
    link = tmp_path / "link.jsonl"
    link.symlink_to(target)
    assert _is_denied(_run_hook(_build_event(f"cat {link.name}"), monkeypatch))


def test_config_disable_and_overlay_priority(monkeypatch, tmp_path):
    config_dir = tmp_path / ".autoskillit" / "temp"
    config_dir.mkdir(parents=True)
    base = config_dir / ".hook_config.json"
    overlay = config_dir / ".hook_config_overlay.json"
    base.write_text(json.dumps({"output_budget_policy": {"disabled": True}}))

    assert _run_hook(_build_event("rg pat src/"), monkeypatch) == ""

    overlay.write_text(json.dumps({"output_budget_policy": {"disabled": False}}))
    assert _is_denied(_run_hook(_build_event("rg pat src/"), monkeypatch))


def test_guard_fires_without_headless_gate(monkeypatch, tmp_path):
    monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)
    (tmp_path / "src").mkdir()
    assert _is_denied(_run_hook(_build_event("rg pat src/"), monkeypatch))


def test_malformed_json_fails_open(monkeypatch):
    assert _run_hook(None, monkeypatch, raw_stdin="not json {{{") == ""


def test_deny_reason_has_concrete_rewrite(monkeypatch, tmp_path):
    from autoskillit.hooks.guards.output_budget_guard import (  # noqa: PLC0415
        OUTPUT_BUDGET_DENY_TRIGGER,
    )

    (tmp_path / "src").mkdir()
    output = _run_hook(_build_event("rg pat src/"), monkeypatch)
    reason = json.loads(output)["hookSpecificOutput"]["permissionDecisionReason"]
    assert OUTPUT_BUDGET_DENY_TRIGGER in reason
    assert "2>&1 | head -c 4000" in reason
    assert ".autoskillit/temp/" in reason
