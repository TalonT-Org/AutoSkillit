"""Focused behavior tests for the output-budget PreToolUse guard."""

# ruff: noqa: E501 -- verbatim incident commands intentionally preserve long lines.

from __future__ import annotations

import io
import json
import shutil
from contextlib import redirect_stdout
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]

INCIDENT_LOG_SEARCH = r"""for base in /home/talon/.local/share/autoskillit/logs \
            /home/talon/Library/Application\ Support/autoskillit/logs; do
  if [ -d "$base" ]; then
    rg -n -F \
      -e '019f669e-dce8-72a0-9d40-c564681bd145' \
      -e '019f66ba-4c32-7800-b8cf-787d47abaf61' \
      -e 'remediation-4226' \
      "$base/sessions.jsonl" "$base" \
      --glob '*.jsonl' \
      --glob '!codex-sessions/**' 2>/dev/null |
      head -n 200
  fi
done"""

INCIDENT_RESOLVE_REVIEW_SEARCH = """nl -ba src/autoskillit/skills_extended/resolve-review/SKILL.md |
  sed -n '1,300p' &&
rg -n \
  "resolve-review|expected_output_patterns|verdict" \
  src/autoskillit/skills_extended/resolve-review \
  src/autoskillit/recipes \
  src/autoskillit/recipe \
  tests/contracts \
  tests/server \
  --glob '*.yaml' \
  --glob '*.md' \
  --glob '*.json' \
  --glob '*.py'"""


def _build_event(command: str, *, run_cmd: bool = False) -> dict:
    key = "cmd" if run_cmd else "command"
    tool = "mcp__autoskillit__local__autoskillit__run_cmd" if run_cmd else "Bash"
    return {"tool_name": tool, "tool_input": {key: command}}


def _run_hook(event: dict | None, monkeypatch, *, raw_stdin: str | None = None) -> str:
    from autoskillit.hooks.guards.output_budget_guard import main  # noqa: PLC0415

    monkeypatch.setenv("AUTOSKILLIT_AGENT_BACKEND", "codex")
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


@pytest.mark.parametrize("command", [INCIDENT_LOG_SEARCH, INCIDENT_RESOLVE_REVIEW_SEARCH])
@pytest.mark.parametrize("run_cmd", [False, True])
def test_denies_verbatim_incident_commands_with_concrete_rewrite(command, run_cmd, monkeypatch):
    from autoskillit.hooks.guards.output_budget_guard import (  # noqa: PLC0415
        OUTPUT_BUDGET_DENY_TRIGGER,
    )

    output = _run_hook(_build_event(command, run_cmd=run_cmd), monkeypatch)
    assert _is_denied(output)
    reason = json.loads(output)["hookSpecificOutput"]["permissionDecisionReason"]
    assert OUTPUT_BUDGET_DENY_TRIGGER in reason
    assert reason.startswith("[AutoSkillit")


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


@pytest.mark.parametrize("line_flag", ["-l", "--lines"])
def test_wc_lines_allows_literal_in_project_jsonl_files(line_flag, monkeypatch, tmp_path):
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    plain = tmp_path / "plain.txt"
    first.write_bytes(b"x" * 2_000)
    second.write_bytes(b"y" * 3_000)
    plain.write_text("plain\n")

    assert _run_hook(_build_event(f"wc {line_flag} {first.name}"), monkeypatch) == ""
    assert _run_hook(_build_event(f"wc {line_flag} {plain.name}"), monkeypatch) == ""
    assert (
        _run_hook(
            _build_event(f"wc {line_flag} -- {first.name} {second.name}"),
            monkeypatch,
        )
        == ""
    )


def test_wc_lines_denies_nonliteral_or_unsafe_jsonl_operands(monkeypatch, tmp_path):
    small = tmp_path / "small.jsonl"
    small.write_text("{}\n")
    plain = tmp_path / "plain.txt"
    plain.write_text("plain\n")
    outside = tmp_path.parent / f"{tmp_path.name}-outside.jsonl"
    outside.write_text("{}\n")
    symlink = tmp_path / "link.jsonl"
    symlink.symlink_to(small)
    oversized = tmp_path / "oversized.jsonl"
    oversized.write_bytes(b"x" * 5_001)
    aggregate_a = tmp_path / "aggregate-a.jsonl"
    aggregate_b = tmp_path / "aggregate-b.jsonl"
    aggregate_a.write_bytes(b"a" * 2_501)
    aggregate_b.write_bytes(b"b" * 2_500)

    commands = [
        "wc -l missing.jsonl",
        f"wc -l {outside}",
        f"wc -l {symlink.name}",
        "wc -l *.jsonl",
        "wc -l -",
        "wc -l",
        "wc -l $TARGET.jsonl",
        f"wc -l {small.name} {plain.name}",
        f"wc -l --bytes {small.name}",
        f"wc -c {small.name}",
        f"wc -l {oversized.name}",
        f"wc --lines {aggregate_a.name} {aggregate_b.name}",
    ]
    for command in commands:
        assert _is_denied(_run_hook(_build_event(command), monkeypatch)), command


@pytest.mark.parametrize(
    "command",
    [
        "wc -l missing.jsonl 2>&1 | head -c 4000",
        "wc --lines *.jsonl 1>.autoskillit/temp/wc.out 2>.autoskillit/temp/wc.err",
    ],
)
def test_wc_lines_unsafe_operands_can_use_complete_byte_bound(command, monkeypatch, tmp_path):
    (tmp_path / ".autoskillit" / "temp").mkdir(parents=True)
    assert _run_hook(_build_event(command), monkeypatch) == ""


def test_large_jsonl_is_denied(monkeypatch, tmp_path):
    path = tmp_path / "large.jsonl"
    path.write_text('{"value": "' + ("x" * 5_100) + '"}\n')
    assert _is_denied(_run_hook(_build_event(f"cat {path.name}"), monkeypatch))


def test_real_large_jsonl_fixture_is_exercised_through_guard(monkeypatch, tmp_path):
    source = Path(__file__).parents[1] / "fixtures" / "codex" / "large_embedded_payload_v1.jsonl"
    target = tmp_path / source.name
    shutil.copyfile(source, target)
    assert target.stat().st_size > 5_000

    for command in (
        f"rg -n payload {target.name}",
        f"cat {target.name}",
        f"jq -r .payload {target.name}",
    ):
        assert _is_denied(_run_hook(_build_event(command), monkeypatch)), command
    assert (
        _run_hook(
            _build_event(f"jq -r .payload {target.name} 2>&1 | head -c 4000"),
            monkeypatch,
        )
        == ""
    )


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


def test_guard_scope_is_codex_only(monkeypatch, tmp_path):
    """Guard fires only on Codex sessions (#4286)."""
    from autoskillit.hooks.guards.output_budget_guard import main  # noqa: PLC0415

    (tmp_path / "src").mkdir()
    event = _build_event("rg pat src/")

    def _raw_run(env_backend, payload_extra=None):
        monkeypatch.delenv("AUTOSKILLIT_AGENT_BACKEND", raising=False)
        if env_backend is not None:
            monkeypatch.setenv("AUTOSKILLIT_AGENT_BACKEND", env_backend)
        data = {**event}
        if payload_extra:
            data.update(payload_extra)
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(data)))
        output = io.StringIO()
        with pytest.raises(SystemExit), redirect_stdout(output):
            main()
        return output.getvalue()

    assert _raw_run(None) == "", "no backend env, no turn_id → allow"
    assert _raw_run("claude_code") == "", "claude_code backend → allow"
    assert _is_denied(_raw_run("codex")), "codex backend → deny"
    assert _is_denied(_raw_run(None, {"turn_id": "turn-1"})), "turn_id in payload → deny"


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
    assert "`" in reason


def test_deny_reason_carries_provenance(monkeypatch, tmp_path):
    (tmp_path / "src").mkdir()
    output = _run_hook(_build_event("rg pat src/"), monkeypatch)
    reason = json.loads(output)["hookSpecificOutput"]["permissionDecisionReason"]
    assert reason.startswith("[AutoSkillit hook output_budget_guard v2")
    assert "R1-R3" not in reason


@pytest.mark.parametrize(
    "command",
    [
        "find . -type f 2>&1 | wc -l | head -c 4000",
        "rg -n pat src/ tests/ 2>&1 | sort | uniq -c | head -c 3000",
        "jq -c 'keys' x.jsonl 2>&1 | head -1 | head -c 1000",
        "rg -n pat src/",
    ],
)
def test_suggested_rewrite_is_classifier_validated(command, monkeypatch, tmp_path):
    from autoskillit.hooks._command_classification import (  # noqa: PLC0415
        classify_command_output_budget,
    )
    from autoskillit.hooks.guards.output_budget_guard import (  # noqa: PLC0415
        _producer_classifier,
    )

    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "tests").mkdir(exist_ok=True)
    output = _run_hook(_build_event(command), monkeypatch)
    assert _is_denied(output)
    reason = json.loads(output)["hookSpecificOutput"]["permissionDecisionReason"]

    import re as _re  # noqa: PLC0415

    backtick_match = _re.search(r"`([^`]+)`", reason)
    if backtick_match:
        suggestion = backtick_match.group(1)

        def classify(tokens):
            return _producer_classifier(tokens, cwd=tmp_path, small_file_max_bytes=5000)

        disposition = classify_command_output_budget(
            suggestion, classify, max_inline_bytes=12000, cwd=str(tmp_path)
        )
        from autoskillit.hooks._command_classification import (  # noqa: PLC0415
            CommandBudgetDisposition,
        )

        assert disposition is CommandBudgetDisposition.BOUNDED
