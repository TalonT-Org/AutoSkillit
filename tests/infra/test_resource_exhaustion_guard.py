"""Tests for the resource_exhaustion_guard PreToolUse hook."""

import io
import json
from contextlib import redirect_stdout
from unittest.mock import patch

import pytest

from tests.hooks._evaluation_shape_matrix import EVALUATION_SHAPE_MATRIX

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


_SHAPE_TOOL_NAMES = {
    "run_cmd": "mcp__autoskillit__local__autoskillit__run_cmd",
    "bash": "Bash",
}
_SHAPE_INPUT_KEYS = {"run_cmd": "cmd", "bash": "command"}


def _run_capture_exit(
    cmd: str, *, shape: str, raw_stdin: str | None = None
) -> tuple[str, int | str | None]:
    """Run the guard's main() in-process for the given tool shape.

    Returns (captured_stdout, SystemExit.code or None if main() didn't exit).
    """
    from autoskillit.hooks.guards.resource_exhaustion_guard import main

    tool_input = {_SHAPE_INPUT_KEYS[shape]: cmd, "cwd": "/some/path"}
    stdin_content = (
        raw_stdin
        if raw_stdin is not None
        else json.dumps(
            {
                "tool_name": _SHAPE_TOOL_NAMES[shape],
                "tool_input": tool_input,
            }
        )
    )
    buf = io.StringIO()
    exit_code: int | str | None = None
    with patch("sys.stdin", io.StringIO(stdin_content)):
        with redirect_stdout(buf):
            try:
                main()
            except SystemExit as exc:
                exit_code = exc.code
    return buf.getvalue(), exit_code


def _run(cmd: str, *, shape: str, raw_stdin: str | None = None) -> str:
    """Run the guard's main() in-process for the given tool shape, return captured stdout."""
    output, _exit_code = _run_capture_exit(cmd, shape=shape, raw_stdin=raw_stdin)
    return output


def _is_denied(output: str) -> bool:
    if not output:
        return False
    data = json.loads(output)
    return data.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


class TestIncidentBExactCommand:
    """Issue #4678 Incident B's exact command must be denied by both tool shapes."""

    @pytest.mark.parametrize("shape", ["run_cmd", "bash"])
    def test_denies_incident_b(self, shape: str) -> None:
        assert _is_denied(_run(_INCIDENT_B_COMMAND, shape=shape))


_BACKGROUNDED_LOOP_DENIED: list[tuple[str, str]] = [
    ("while :; do :; done &", "colon-backgrounded"),
    ("while true; do :; done &", "true-backgrounded"),
    ("(while :; do sleep 1; done) &", "colon-subshell-backgrounded"),
    ("(while true; do sleep 1; done) &", "true-subshell-backgrounded"),
    ("bash -c 'while true; do :; done &'", "nested-bash-c-backgrounded"),
    ("sh -c 'while :; do :; done &'", "nested-sh-c-backgrounded"),
    ("eval 'while true; do :; done &'", "eval-backgrounded"),
]


@pytest.mark.parametrize("shape", ["run_cmd", "bash"])
@pytest.mark.parametrize(
    "cmd", [c[0] for c in _BACKGROUNDED_LOOP_DENIED], ids=[c[1] for c in _BACKGROUNDED_LOOP_DENIED]
)
def test_denies_backgrounded_infinite_loop(cmd: str, shape: str) -> None:
    assert _is_denied(_run(cmd, shape=shape)), f"{shape} should deny: {cmd!r}"


_FOREGROUND_LOOP_ALLOWED: list[tuple[str, str]] = [
    ("while :; do :; done", "colon-foreground"),
    ("while true; do :; done", "true-foreground"),
    ("while true; do echo hi; done", "true-foreground-echo"),
    ("timeout 5 bash -c 'while :; do :; done'", "timeout-bounded-foreground"),
    ("while read line; do echo $line; done < file.txt", "while-read-not-infinite"),
    ("(sleep 30 &)", "unrelated-background-not-a-loop"),
    ("nohup python script.py &", "nohup-not-a-loop"),
]


@pytest.mark.parametrize("shape", ["run_cmd", "bash"])
@pytest.mark.parametrize(
    "cmd", [c[0] for c in _FOREGROUND_LOOP_ALLOWED], ids=[c[1] for c in _FOREGROUND_LOOP_ALLOWED]
)
def test_allows_foreground_loop(cmd: str, shape: str) -> None:
    assert not _is_denied(_run(cmd, shape=shape)), f"{shape} should allow: {cmd!r}"


_KILL_JOBSPEC_DENIED: list[tuple[str, str]] = [
    ("kill %1", "kill-single-jobspec"),
    ("kill %1 %2 %3 %4", "kill-multiple-jobspecs"),
    ("kill -9 %1", "kill-signal-jobspec"),
    ("kill %1 2>/dev/null", "kill-jobspec-suppressed-stderr"),
    ("sh -c 'kill %1 %2'", "nested-kill-jobspec"),
]


@pytest.mark.parametrize("shape", ["run_cmd", "bash"])
@pytest.mark.parametrize(
    "cmd", [c[0] for c in _KILL_JOBSPEC_DENIED], ids=[c[1] for c in _KILL_JOBSPEC_DENIED]
)
def test_denies_kill_jobspec(cmd: str, shape: str) -> None:
    assert _is_denied(_run(cmd, shape=shape)), f"{shape} should deny: {cmd!r}"


_KILL_PID_ALLOWED: list[tuple[str, str]] = [
    ("kill $!", "kill-last-bg-pid"),
    ("kill 12345", "kill-literal-pid"),
    ("kill -9 $!", "kill-signal-last-bg-pid"),
    ("kill -TERM 12345", "kill-named-signal-literal-pid"),
]


@pytest.mark.parametrize("shape", ["run_cmd", "bash"])
@pytest.mark.parametrize(
    "cmd", [c[0] for c in _KILL_PID_ALLOWED], ids=[c[1] for c in _KILL_PID_ALLOWED]
)
def test_allows_kill_by_pid(cmd: str, shape: str) -> None:
    assert not _is_denied(_run(cmd, shape=shape)), f"{shape} should allow: {cmd!r}"


_STDIN_LITERAL_LOOP_DENIED: list[tuple[str, str]] = [
    ("bash <<'EOF'\nwhile :; do :; done &\nEOF", "bash-heredoc-backgrounded-loop"),
    ("cat <<'EOF' | bash\nwhile :; do :; done &\nEOF", "cat-heredoc-pipe-bash-loop"),
]


@pytest.mark.parametrize("shape", ["run_cmd", "bash"])
@pytest.mark.parametrize(
    "cmd",
    [c[0] for c in _STDIN_LITERAL_LOOP_DENIED],
    ids=[c[1] for c in _STDIN_LITERAL_LOOP_DENIED],
)
def test_denies_heredoc_delivered_backgrounded_loop(cmd: str, shape: str) -> None:
    """Rectify #4941 Part B: a SHELL-consumed heredoc body running the exact
    loop shape must deny -- previously a false negative, since the private
    strip_heredoc_bodies pre-pass erased every heredoc body before scanning."""
    assert _is_denied(_run(cmd, shape=shape)), f"{shape} should deny: {cmd!r}"


def test_allows_inert_heredoc_body_mentioning_loop_shape() -> None:
    """An INERT (cat, no pipe) heredoc body is prose, not an executed loop."""
    cmd = "cat > notes.md <<'EOF'\nwhile :; do :; done &\nEOF"
    assert not _is_denied(_run(cmd, shape="run_cmd"))


_MATRIX_APPLICABLE_SHAPES = [
    s for s in EVALUATION_SHAPE_MATRIX if s.executes and s.consumer != "python"
]
_MATRIX_INERT_SHAPES = [s for s in EVALUATION_SHAPE_MATRIX if not s.executes]
_MATRIX_PYTHON_SHAPES = [
    s for s in EVALUATION_SHAPE_MATRIX if s.executes and s.consumer == "python"
]


class TestResourceExhaustionGuardEvaluationShapeMatrix:
    """Deny family proven through every semantically executing, non-Python shape.

    A Python argv-list shape's `subprocess.run([...])` call never evaluates
    `while`/`done`/`&`/`kill %N` as shell syntax -- they are chopped into
    separate literal argv strings, so those shapes are excluded rather than
    inheriting a blanket deny expectation (rectify #4941 Part B, plan 1.1).
    """

    @pytest.mark.parametrize("shape", _MATRIX_APPLICABLE_SHAPES, ids=lambda s: s.id)
    def test_shell_shape_denies_backgrounded_loop(self, shape) -> None:
        cmd = shape.build("(while :; do :; done) &")
        assert _is_denied(_run(cmd, shape="run_cmd")), f"shape {shape.id!r} must deny"

    @pytest.mark.parametrize("shape", _MATRIX_APPLICABLE_SHAPES, ids=lambda s: s.id)
    def test_shell_shape_denies_kill_jobspec(self, shape) -> None:
        cmd = shape.build("kill %1")
        assert _is_denied(_run(cmd, shape="run_cmd")), f"shape {shape.id!r} must deny"

    @pytest.mark.parametrize("shape", _MATRIX_INERT_SHAPES, ids=lambda s: s.id)
    def test_inert_shape_allows_backgrounded_loop(self, shape) -> None:
        cmd = shape.build("(while :; do :; done) &")
        assert not _is_denied(_run(cmd, shape="run_cmd")), f"shape {shape.id!r} must allow"

    @pytest.mark.parametrize("shape", _MATRIX_PYTHON_SHAPES, ids=lambda s: s.id)
    def test_python_consumer_shape_allows_backgrounded_loop(self, shape) -> None:
        """Asserts the _MATRIX_APPLICABLE_SHAPES exclusion's assumption: a Python
        argv-list shape's subprocess.run([...]) call never evaluates shell
        metacharacters, so the loop text is inert here even though it denies
        via every shell-consumer shape."""
        cmd = shape.build("(while :; do :; done) &")
        assert not _is_denied(_run(cmd, shape="run_cmd")), f"shape {shape.id!r} must allow"

    @pytest.mark.parametrize("shape", _MATRIX_PYTHON_SHAPES, ids=lambda s: s.id)
    def test_python_consumer_shape_allows_kill_jobspec(self, shape) -> None:
        cmd = shape.build("kill %1")
        assert not _is_denied(_run(cmd, shape="run_cmd")), f"shape {shape.id!r} must allow"


class TestResourceExhaustionGuardEdgeCases:
    def test_malformed_json_fail_open(self) -> None:
        output, exit_code = _run_capture_exit(
            "irrelevant", shape="run_cmd", raw_stdin="not-json{{{"
        )
        assert output == ""
        assert exit_code == 0

    def test_missing_cmd_field_fail_open(self) -> None:
        stdin = json.dumps(
            {
                "tool_name": "mcp__autoskillit__local__autoskillit__run_cmd",
                "tool_input": {},
            }
        )
        output, exit_code = _run_capture_exit("irrelevant", shape="run_cmd", raw_stdin=stdin)
        assert output == ""
        assert exit_code == 0

    def test_unrelated_command_allowed(self) -> None:
        assert not _is_denied(_run("pytest tests/", shape="run_cmd"))
        assert not _is_denied(_run("git status", shape="bash"))
