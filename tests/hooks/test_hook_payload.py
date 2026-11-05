"""Payload facts extracted by ``parse_hook_command`` in hooks/_runtime/_hook_payload.py.

Members: the Bash per-call ``workdir`` rows (absolute, relative and blank), the
payload-cwd rows that carry no ``workdir``, the run_cmd rows that ignore it, and
the github_mutation_guard decisions that separate ``RELATIVE_WORKDIR`` from the
session-cwd anomalies.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# The sys.path bootstrap for bare-name hook-sibling imports is centralized in
# tests/conftest.py.
from _hook_payload import (  # type: ignore[import-not-found]
    PayloadAnomaly,
    parse_hook_command,
)

from autoskillit.hooks.guards.github_mutation_guard import DenyTrigger, decide

from .conftest import _RUN_CMD_TOOL_DIRECT, _RUN_CMD_TOOL_MARKETPLACE, make_hook_event

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]

_COMMAND = "echo ok"


def test_absolute_workdir_is_the_bash_execution_cwd(tmp_path: Path) -> None:
    session = tmp_path / "session"
    workdir = tmp_path / "work"
    event = make_hook_event(
        tool="Bash",
        command=_COMMAND,
        payload_cwd=str(session),
        extra_tool_input={"workdir": str(workdir)},
    )

    parsed = parse_hook_command(event)

    assert parsed.tool_kind == "bash"
    assert parsed.command == _COMMAND
    assert parsed.execution_cwd == str(workdir)
    assert parsed.payload_cwd == str(session)
    assert parsed.anomalies == ()


@pytest.mark.parametrize("workdir", ["", "   ", "work", "./work", "../work", "~/work"], ids=repr)
def test_relative_or_blank_workdir_is_a_relative_workdir_anomaly(
    tmp_path: Path, workdir: str
) -> None:
    event = make_hook_event(
        tool="Bash",
        command=_COMMAND,
        payload_cwd=str(tmp_path),
        extra_tool_input={"workdir": workdir},
    )

    parsed = parse_hook_command(event)

    assert parsed.execution_cwd == ""
    assert parsed.payload_cwd == str(tmp_path)
    assert parsed.anomalies == (PayloadAnomaly.RELATIVE_WORKDIR,)


@pytest.mark.parametrize(
    ("payload_cwd", "execution_cwd", "anomalies"),
    [
        ("/session", "/session", ()),
        (None, "", ()),
        ("session", "", (PayloadAnomaly.RELATIVE_CWD,)),
        (7, "", (PayloadAnomaly.NON_STRING_CWD,)),
    ],
    ids=["absolute", "missing", "relative", "non-string"],
)
def test_bash_payload_without_workdir_uses_the_session_cwd(
    payload_cwd: object, execution_cwd: str, anomalies: tuple[PayloadAnomaly, ...]
) -> None:
    event = {"tool_name": "Bash", "tool_input": {"command": _COMMAND}, "cwd": payload_cwd}

    parsed = parse_hook_command(event)

    assert parsed.execution_cwd == execution_cwd
    assert parsed.anomalies == anomalies


@pytest.mark.parametrize(
    "tool_name", [_RUN_CMD_TOOL_MARKETPLACE, _RUN_CMD_TOOL_DIRECT], ids=["marketplace", "direct"]
)
@pytest.mark.parametrize("workdir", ["/elsewhere", "relative", ""], ids=repr)
def test_run_cmd_execution_cwd_ignores_workdir(tool_name: str, workdir: str) -> None:
    with_cwd = make_hook_event(
        tool="run_cmd",
        command=_COMMAND,
        payload_cwd="/session",
        tool_cwd="/target",
        extra_tool_input={"workdir": workdir},
        run_cmd_tool_name=tool_name,
    )
    without_cwd = make_hook_event(
        tool="run_cmd",
        command=_COMMAND,
        payload_cwd="/session",
        extra_tool_input={"workdir": workdir},
        run_cmd_tool_name=tool_name,
    )

    parsed_with_cwd = parse_hook_command(with_cwd)
    parsed_without_cwd = parse_hook_command(without_cwd)

    assert parsed_with_cwd.tool_kind == "run_cmd"
    assert parsed_with_cwd.execution_cwd == "/target"
    assert parsed_with_cwd.anomalies == ()
    assert parsed_without_cwd.execution_cwd == ""
    assert parsed_without_cwd.anomalies == ()


def test_relative_workdir_does_not_trigger_the_github_guard_malformed_cwd_denial(
    tmp_path: Path,
) -> None:
    event = make_hook_event(
        tool="Bash",
        command=_COMMAND,
        payload_cwd=str(tmp_path),
        extra_tool_input={"workdir": "relative"},
    )
    parsed = parse_hook_command(event)

    decision = decide(parsed)

    assert parsed.anomalies == (PayloadAnomaly.RELATIVE_WORKDIR,)
    assert decision.allow is True
    assert decision.trigger is None


@pytest.mark.parametrize(
    "payload_cwd", ["relative", 7], ids=["relative-session-cwd", "non-string-session-cwd"]
)
def test_session_cwd_anomalies_keep_the_github_guard_malformed_cwd_denial(
    payload_cwd: object,
) -> None:
    event = {"tool_name": "Bash", "tool_input": {"command": _COMMAND}, "cwd": payload_cwd}

    decision = decide(parse_hook_command(event))

    assert decision.allow is False
    assert decision.trigger is DenyTrigger.MALFORMED_CWD
