"""Tests for the resource_exhaustion_guard PreToolUse hook."""

import io
import json
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]

# The exact busy-loop leak from issue #4678 Incident B: backgrounded infinite
# loops whose PIDs are captured only as shell job-control specs (`%N`), which
# silently fail to kill anything under a non-interactive `sh -c` because job
# control is disabled there.
_INCIDENT_B_COMMAND = (
    "for i in $(seq 6); do timeout 60 nice -n 0 sh -c "
    "'for j in 1 2 3 4; do (while :; do :; done) & done; "
    "sleep 25; kill %1 %2 %3 %4 2>/dev/null' & done"
)


def _run_guard(cmd: str, raw_stdin: str | None = None) -> str:
    """Run the guard's main() in-process with run_cmd format, return captured stdout."""
    from autoskillit.hooks.guards.resource_exhaustion_guard import main

    tool_input = {"cmd": cmd, "cwd": "/some/path"}
    stdin_content = (
        raw_stdin
        if raw_stdin is not None
        else json.dumps(
            {
                "tool_name": "mcp__autoskillit__local__autoskillit__run_cmd",
                "tool_input": tool_input,
            }
        )
    )
    buf = io.StringIO()
    with patch("sys.stdin", io.StringIO(stdin_content)):
        with redirect_stdout(buf):
            try:
                main()
            except SystemExit:
                pass
    return buf.getvalue()


def _run_bash_guard(cmd: str, raw_stdin: str | None = None) -> str:
    """Run the guard's main() in-process with Bash tool format, return captured stdout."""
    from autoskillit.hooks.guards.resource_exhaustion_guard import main

    tool_input = {"command": cmd, "cwd": "/some/path"}
    stdin_content = (
        raw_stdin
        if raw_stdin is not None
        else json.dumps(
            {
                "tool_name": "Bash",
                "tool_input": tool_input,
            }
        )
    )
    buf = io.StringIO()
    with patch("sys.stdin", io.StringIO(stdin_content)):
        with redirect_stdout(buf):
            try:
                main()
            except SystemExit:
                pass
    return buf.getvalue()


def _is_denied(output: str) -> bool:
    if not output:
        return False
    data = json.loads(output)
    return data.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


class TestIncidentBExactCommand:
    """Issue #4678 Incident B's exact command must be denied by both tool shapes."""

    def test_run_guard_denies_incident_b(self) -> None:
        assert _is_denied(_run_guard(_INCIDENT_B_COMMAND))

    def test_bash_guard_denies_incident_b(self) -> None:
        assert _is_denied(_run_bash_guard(_INCIDENT_B_COMMAND))


_BACKGROUNDED_LOOP_DENIED: list[tuple[str, str]] = [
    ("while :; do :; done &", "colon-backgrounded"),
    ("while true; do :; done &", "true-backgrounded"),
    ("(while :; do sleep 1; done) &", "colon-subshell-backgrounded"),
    ("(while true; do sleep 1; done) &", "true-subshell-backgrounded"),
    ("bash -c 'while true; do :; done &'", "nested-bash-c-backgrounded"),
    ("sh -c 'while :; do :; done &'", "nested-sh-c-backgrounded"),
    ("eval 'while true; do :; done &'", "eval-backgrounded"),
]


@pytest.mark.parametrize(
    "cmd", [c[0] for c in _BACKGROUNDED_LOOP_DENIED], ids=[c[1] for c in _BACKGROUNDED_LOOP_DENIED]
)
def test_run_guard_denies_backgrounded_infinite_loop(cmd: str) -> None:
    assert _is_denied(_run_guard(cmd)), f"run_cmd should deny: {cmd!r}"


@pytest.mark.parametrize(
    "cmd", [c[0] for c in _BACKGROUNDED_LOOP_DENIED], ids=[c[1] for c in _BACKGROUNDED_LOOP_DENIED]
)
def test_bash_guard_denies_backgrounded_infinite_loop(cmd: str) -> None:
    assert _is_denied(_run_bash_guard(cmd)), f"Bash tool should deny: {cmd!r}"


_FOREGROUND_LOOP_ALLOWED: list[tuple[str, str]] = [
    ("while :; do :; done", "colon-foreground"),
    ("while true; do :; done", "true-foreground"),
    ("while true; do echo hi; done", "true-foreground-echo"),
    ("timeout 5 bash -c 'while :; do :; done'", "timeout-bounded-foreground"),
    ("while read line; do echo $line; done < file.txt", "while-read-not-infinite"),
    ("(sleep 30 &)", "unrelated-background-not-a-loop"),
    ("nohup python script.py &", "nohup-not-a-loop"),
]


@pytest.mark.parametrize(
    "cmd", [c[0] for c in _FOREGROUND_LOOP_ALLOWED], ids=[c[1] for c in _FOREGROUND_LOOP_ALLOWED]
)
def test_run_guard_allows_foreground_loop(cmd: str) -> None:
    assert not _is_denied(_run_guard(cmd)), f"run_cmd should allow: {cmd!r}"


@pytest.mark.parametrize(
    "cmd", [c[0] for c in _FOREGROUND_LOOP_ALLOWED], ids=[c[1] for c in _FOREGROUND_LOOP_ALLOWED]
)
def test_bash_guard_allows_foreground_loop(cmd: str) -> None:
    assert not _is_denied(_run_bash_guard(cmd)), f"Bash tool should allow: {cmd!r}"


_KILL_JOBSPEC_DENIED: list[tuple[str, str]] = [
    ("kill %1", "kill-single-jobspec"),
    ("kill %1 %2 %3 %4", "kill-multiple-jobspecs"),
    ("kill -9 %1", "kill-signal-jobspec"),
    ("kill %1 2>/dev/null", "kill-jobspec-suppressed-stderr"),
    ("sh -c 'kill %1 %2'", "nested-kill-jobspec"),
]


@pytest.mark.parametrize(
    "cmd", [c[0] for c in _KILL_JOBSPEC_DENIED], ids=[c[1] for c in _KILL_JOBSPEC_DENIED]
)
def test_run_guard_denies_kill_jobspec(cmd: str) -> None:
    assert _is_denied(_run_guard(cmd)), f"run_cmd should deny: {cmd!r}"


@pytest.mark.parametrize(
    "cmd", [c[0] for c in _KILL_JOBSPEC_DENIED], ids=[c[1] for c in _KILL_JOBSPEC_DENIED]
)
def test_bash_guard_denies_kill_jobspec(cmd: str) -> None:
    assert _is_denied(_run_bash_guard(cmd)), f"Bash tool should deny: {cmd!r}"


_KILL_PID_ALLOWED: list[tuple[str, str]] = [
    ("kill $!", "kill-last-bg-pid"),
    ("kill 12345", "kill-literal-pid"),
    ("kill -9 $!", "kill-signal-last-bg-pid"),
    ("kill -TERM 12345", "kill-named-signal-literal-pid"),
]


@pytest.mark.parametrize(
    "cmd", [c[0] for c in _KILL_PID_ALLOWED], ids=[c[1] for c in _KILL_PID_ALLOWED]
)
def test_run_guard_allows_kill_by_pid(cmd: str) -> None:
    assert not _is_denied(_run_guard(cmd)), f"run_cmd should allow: {cmd!r}"


@pytest.mark.parametrize(
    "cmd", [c[0] for c in _KILL_PID_ALLOWED], ids=[c[1] for c in _KILL_PID_ALLOWED]
)
def test_bash_guard_allows_kill_by_pid(cmd: str) -> None:
    assert not _is_denied(_run_bash_guard(cmd)), f"Bash tool should allow: {cmd!r}"


class TestResourceExhaustionGuardEdgeCases:
    def test_malformed_json_fail_open(self) -> None:
        output = _run_guard("irrelevant", raw_stdin="not-json{{{")
        assert output == ""

    def test_missing_cmd_field_fail_open(self) -> None:
        stdin = json.dumps(
            {
                "tool_name": "mcp__autoskillit__local__autoskillit__run_cmd",
                "tool_input": {},
            }
        )
        output = _run_guard("irrelevant", raw_stdin=stdin)
        assert output == ""

    def test_unrelated_command_allowed(self) -> None:
        assert not _is_denied(_run_guard("pytest tests/"))
        assert not _is_denied(_run_bash_guard("git status"))
