from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from autoskillit.hooks._exploration_request_record import (
    SUPPORTED_EXPLORATION_REQUEST_TOOLS,
    consume_exploration_request_record,
)

pytestmark = pytest.mark.medium

_SCRIPT = (
    Path(__file__).parents[2]
    / "src"
    / "autoskillit"
    / "hooks"
    / "guards"
    / "exploration_request_identity_guard.py"
)


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / ".autoskillit" / "temp").mkdir(parents=True)
    return root


def _run(
    payload: object,
    root: Path,
    *,
    headless: bool = False,
    backend: str | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["AUTOSKILLIT_STATE_ROOT"] = str(root)
    if headless:
        env["AUTOSKILLIT_HEADLESS"] = "1"
    else:
        env.pop("AUTOSKILLIT_HEADLESS", None)
    if backend is None:
        env.pop("AUTOSKILLIT_AGENT_BACKEND", None)
    else:
        env["AUTOSKILLIT_AGENT_BACKEND"] = backend
    return subprocess.run(
        [sys.executable, str(_SCRIPT)],
        input=payload if isinstance(payload, str) else json.dumps(payload),
        text=True,
        capture_output=True,
        timeout=5,
        env=env,
        check=False,
    )


@pytest.mark.parametrize(
    ("runtime_name", "short_name"),
    [
        ("mcp__autoskillit__enable_exploration", "enable_exploration"),
        ("mcp__dev_autoskillit_v2__submit_exploration_query", "submit_exploration_query"),
        ("mcp__autoskillit_local__get_exploration_page", "get_exploration_page"),
        ("mcp__decorated_autoskillit__resume_exploration_context", "resume_exploration_context"),
    ],
)
def test_guard_preserves_input_and_injects_consumable_native_identity(
    tmp_path: Path, runtime_name: str, short_name: str
) -> None:
    root = _project(tmp_path)
    result = _run(
        {
            "tool_name": runtime_name,
            "session_id": "native-session",
            "agent_id": "child-only-metadata",
            "cwd": str(root),
            "tool_input": {
                "existing": "value",
                "_autoskillit_exploration_request_token": "model-value",
            },
        },
        root,
        headless=False,
    )

    assert result.returncode == 0
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert output["hookEventName"] == "PreToolUse"
    assert output["permissionDecision"] == "allow"
    assert output["updatedInput"]["existing"] == "value"
    token = output["updatedInput"]["_autoskillit_exploration_request_token"]
    assert token != "model-value"
    assert consume_exploration_request_record(root, short_name, token) == "native-session"
    assert consume_exploration_request_record(root, short_name, token) is None


@pytest.mark.parametrize(
    "payload",
    [
        {
            "tool_name": "mcp__autoskillit__enable_exploration",
            "tool_input": {},
        },
        {
            "tool_name": "mcp__autoskillit__enable_exploration",
            "session_id": 42,
            "tool_input": {},
        },
        {
            "tool_name": "mcp__autoskillit__enable_exploration",
            "session_id": "native-session",
            "tool_input": "invalid",
        },
    ],
)
def test_supported_event_fails_closed_without_valid_identity(
    tmp_path: Path, payload: dict[str, object]
) -> None:
    result = _run(payload, _project(tmp_path))
    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    assert "EXPLORATION REQUEST IDENTITY UNAVAILABLE" in output["permissionDecisionReason"]


def test_supported_event_fails_closed_when_record_write_fails(tmp_path: Path) -> None:
    invalid_root = tmp_path / "not-a-directory"
    invalid_root.write_text("blocks request-record directory creation")

    result = _run(
        {
            "tool_name": "mcp__autoskillit__enable_exploration",
            "session_id": "native-session",
            "tool_input": {},
        },
        invalid_root,
    )

    output = json.loads(result.stdout)["hookSpecificOutput"]
    assert output["hookEventName"] == "PreToolUse"
    assert output["permissionDecision"] == "deny"
    assert output["permissionDecisionReason"] == (
        "EXPLORATION REQUEST IDENTITY UNAVAILABLE: "
        "the correlated one-shot record could not be written"
    )
    assert "exploration_request_identity_guard: request record write failed:" in result.stderr


def test_guard_allows_unparseable_and_unrelated_input(tmp_path: Path) -> None:
    root = _project(tmp_path)
    malformed = _run("not-json", root)
    assert malformed.stdout == ""
    assert "exploration_request_identity_guard: malformed hook input:" in malformed.stderr

    unrelated = _run(
        {
            "tool_name": "mcp__autoskillit__open_kitchen",
            "session_id": "native-session",
            "tool_input": {},
        },
        root,
    )
    assert unrelated.stdout == ""
    assert unrelated.stderr == ""


def test_guard_skips_headless_events(tmp_path: Path) -> None:
    root = _project(tmp_path)
    result = _run(
        {
            "tool_name": "mcp__autoskillit__enable_exploration",
            "session_id": "native-session",
            "tool_input": {},
        },
        root,
        headless=True,
    )

    assert result.stdout == ""


def test_guard_skips_codex_events(tmp_path: Path) -> None:
    root = _project(tmp_path)
    result = _run(
        {
            "tool_name": "mcp__autoskillit__enable_exploration",
            "session_id": "native-session",
            "tool_input": {},
        },
        root,
        backend="codex",
    )

    assert result.stdout == ""
    assert not (root / ".autoskillit" / "temp" / "exploration-requests").exists()


def test_registry_matcher_is_decorated_name_tolerant_and_exact() -> None:
    from autoskillit.hook_registry import HOOK_REGISTRY

    hook = next(
        definition
        for definition in HOOK_REGISTRY
        if "guards/exploration_request_identity_guard.py" in definition.scripts
    )
    matcher = re.compile(hook.matcher)
    alternatives = re.search(r"__\(([^()]+)\)\$$", hook.matcher)

    assert alternatives is not None
    assert frozenset(alternatives.group(1).split("|")) == SUPPORTED_EXPLORATION_REQUEST_TOOLS
    for short_name in SUPPORTED_EXPLORATION_REQUEST_TOOLS:
        assert matcher.fullmatch(f"mcp__dev_autoskillit_v2__{short_name}")
    assert not matcher.fullmatch("mcp__autoskillit__open_kitchen")
    assert not matcher.fullmatch("mcp__autoskillit__enable_exploration_extra")


def test_projected_plugin_keeps_guard_and_sibling_helper(tmp_path: Path) -> None:
    from autoskillit.workspace._projected_artifact.materialization import (
        _copy_non_skill_plugin_assets,
    )

    source = tmp_path / "source"
    guards = source / "hooks" / "guards"
    guards.mkdir(parents=True)
    (guards / "exploration_request_identity_guard.py").write_text("guard")
    (source / "hooks" / "_exploration_request_record.py").write_text("helper")
    destination = tmp_path / "destination"
    destination.mkdir()

    _copy_non_skill_plugin_assets(source, destination)

    assert (destination / "hooks" / "guards" / _SCRIPT.name).read_text() == "guard"
    assert (destination / "hooks" / "_exploration_request_record.py").read_text() == "helper"
