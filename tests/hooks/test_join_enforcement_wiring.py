"""Production-shaped subprocess coverage for payload-derived join enforcement."""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

from autoskillit.hook_registry import HOOK_REGISTRY
from autoskillit.hooks._join_ledger import (
    OUTCOME_FAILURE,
    OUTCOME_SUCCESS,
    active_batch,
    claim_assignment,
    declare_batch,
    resolve_flag_dir,
    settle_assignment,
)
from autoskillit.hooks._session_binding import read_binding, resolve_binding_path
from tests._helpers import _EnvVarReadCollector
from tests.conftest import production_interpreter_env
from tests.hooks._session_binding_helpers import copy_projected_hook, write_projection_manifest

pytestmark = [pytest.mark.medium]


_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_GUARDS_DIR = _PROJECT_ROOT / "src" / "autoskillit" / "hooks" / "guards"
_HOOKS_DIR = _PROJECT_ROOT / "src" / "autoskillit" / "hooks"
_RETIRED_JOIN_ENV = frozenset(
    {
        "AUTOSKILLIT_JOIN_FLAG_PATH",
        "AUTOSKILLIT_JOIN_REQUIRED",
        "AUTOSKILLIT_HOOK_EVENT",
        "AUTOSKILLIT_SESSION_ID",
        "AUTOSKILLIT_JOIN_SESSION_ID",
        "AUTOSKILLIT_JOIN_PARENT",
    }
)
_SETTLEMENT_EXPECTATIONS: dict[str, tuple[dict[str, object], str]] = {
    "PostToolUse": ({"tool_response": "complete"}, OUTCOME_SUCCESS),
    "PostToolUseFailure": ({"error": "forced failure"}, OUTCOME_FAILURE),
}


def _settlement_event_cases() -> tuple[tuple[str, dict[str, object], str], ...]:
    """Derive the exercise matrix from the registered settlement hooks."""
    cases: list[tuple[str, dict[str, object], str]] = []
    for hook in HOOK_REGISTRY:
        event_name = hook.event_type
        if "guards/join_settle_guard.py" not in hook.scripts:
            continue
        if not isinstance(event_name, str):
            raise AssertionError("join_settle_guard must declare a concrete hook event")
        expected = _SETTLEMENT_EXPECTATIONS.get(event_name)
        if expected is None:
            raise AssertionError(
                f"join_settle_guard event {event_name!r} needs a settlement assertion"
            )
        cases.append((event_name, *expected))
    return tuple(cases)


_SETTLEMENT_EVENT_CASES = _settlement_event_cases()


def _child_env(tmp_path: Path, *, overrides: dict[str, str] | None = None) -> dict[str, str]:
    """Build a hook-process environment without retired authority channels."""
    env = production_interpreter_env()
    for name in (*_RETIRED_JOIN_ENV, "AUTOSKILLIT_STATE_ROOT"):
        env.pop(name, None)
    env.update(
        {
            "AUTOSKILLIT_AGENT_BACKEND": "claude-code",
            "AUTOSKILLIT_LOG_DIR": str(tmp_path / "logs"),
            "AUTOSKILLIT_SESSION_TYPE": "skill",
        }
    )
    if overrides:
        env.update(overrides)
    return env


def _run_hook(
    tmp_path: Path,
    hook: Path,
    payload: dict[str, object] | str,
    *,
    cwd: Path,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a real hook program with an isolated production-like environment."""
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(hook)],
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
        cwd=cwd,
        env=_child_env(tmp_path, overrides=env_overrides),
        timeout=10,
    )


def _load_join_bearing_skill(tmp_path: Path, *, session_id: str = "session-1") -> Path:
    """Drive the projected skill-load hook so the binding is production-shaped."""
    worktree = tmp_path / "worktree"
    (worktree / ".autoskillit").mkdir(parents=True)
    projection_root, skill_load_hook = copy_projected_hook(tmp_path)
    write_projection_manifest(projection_root)

    completed = _run_hook(
        tmp_path,
        skill_load_hook,
        {
            "tool_name": "Skill",
            "tool_input": {"skill": "join-bearing"},
            "session_id": session_id,
            "cwd": str(worktree),
        },
        cwd=worktree,
    )
    assert completed.returncode == 0, completed.stderr
    binding = read_binding(resolve_binding_path(str(worktree), session_id))
    assert binding is not None and binding.join_required
    return worktree


def _declare_one_assignment(worktree: Path, *, session_id: str) -> Path:
    """Declare one wave at the same state root the hook payload resolves."""
    flag_dir = resolve_flag_dir(worktree)
    declare_batch(
        flag_dir,
        session_id=session_id,
        top_level_parent="top_level",
        skill_name="join-bearing",
        artifact_digest="artdigest-1",
        assignments=("worker",),
    )
    return flag_dir


def _agent_payload(worktree: Path, *, session_id: str, tool_use_id: str) -> dict[str, object]:
    return {
        "tool_name": "Agent",
        "tool_input": {"prompt": "perform the declared work"},
        "session_id": session_id,
        "tool_use_id": tool_use_id,
        "cwd": str(worktree),
    }


def _stdout_json(completed: subprocess.CompletedProcess[str]) -> dict[str, object]:
    assert completed.stdout, completed.stderr
    parsed = json.loads(completed.stdout)
    assert isinstance(parsed, dict)
    return parsed


def test_claim_guard_denies_an_undeclared_agent_call_in_a_real_session(tmp_path: Path) -> None:
    session_id = "claim-deny"
    worktree = _load_join_bearing_skill(tmp_path, session_id=session_id)

    completed = _run_hook(
        tmp_path,
        _GUARDS_DIR / "join_claim_guard.py",
        _agent_payload(worktree, session_id=session_id, tool_use_id="agent-1"),
        cwd=worktree,
    )

    assert completed.returncode == 0
    output = _stdout_json(completed)["hookSpecificOutput"]
    assert isinstance(output, dict)
    assert output["permissionDecision"] == "deny"


def test_claim_guard_permits_an_agent_call_that_belongs_to_a_declared_wave(
    tmp_path: Path,
) -> None:
    session_id = "claim-permit"
    tool_use_id = "agent-1"
    worktree = _load_join_bearing_skill(tmp_path, session_id=session_id)
    flag_dir = _declare_one_assignment(worktree, session_id=session_id)

    completed = _run_hook(
        tmp_path,
        _GUARDS_DIR / "join_claim_guard.py",
        _agent_payload(worktree, session_id=session_id, tool_use_id=tool_use_id),
        cwd=worktree,
    )

    assert completed.returncode == 0
    assert not completed.stdout
    batch = active_batch(flag_dir, session_id=session_id, top_level_parent="top_level")
    assert batch is not None
    assert batch["assignments"][0]["tool_use_id"] == tool_use_id


@pytest.mark.parametrize(
    ("event_name", "event_data", "expected_outcome"),
    _SETTLEMENT_EVENT_CASES,
)
def test_settle_guard_maps_every_registered_event_type(
    tmp_path: Path,
    event_name: str,
    event_data: dict[str, object],
    expected_outcome: str,
) -> None:
    session_id = f"settle-{event_name}"
    tool_use_id = "agent-1"
    worktree = _load_join_bearing_skill(tmp_path, session_id=session_id)
    flag_dir = _declare_one_assignment(worktree, session_id=session_id)
    claim_assignment(
        flag_dir,
        session_id=session_id,
        top_level_parent="top_level",
        tool_use_id=tool_use_id,
    )
    payload = _agent_payload(worktree, session_id=session_id, tool_use_id=tool_use_id)
    payload.update({"hook_event_name": event_name, **event_data})

    completed = _run_hook(
        tmp_path,
        _GUARDS_DIR / "join_settle_guard.py",
        payload,
        cwd=worktree,
    )

    assert completed.returncode == 0, completed.stderr
    batch = active_batch(flag_dir, session_id=session_id, top_level_parent="top_level")
    assert batch is not None
    assert batch["assignments"][0]["outcome"] == expected_outcome


def test_stop_guard_blocks_on_an_unresolved_wave_using_payload_identity(tmp_path: Path) -> None:
    session_id = "stop-block"
    worktree = _load_join_bearing_skill(tmp_path, session_id=session_id)
    _declare_one_assignment(worktree, session_id=session_id)

    completed = _run_hook(
        tmp_path,
        _GUARDS_DIR / "join_stop_guard.py",
        {"session_id": session_id, "cwd": str(worktree)},
        cwd=worktree,
    )

    assert completed.returncode == 2
    assert _stdout_json(completed)["decision"] == "block"


def test_stop_guard_releases_when_the_wave_is_complete(tmp_path: Path) -> None:
    session_id = "stop-release"
    tool_use_id = "agent-1"
    worktree = _load_join_bearing_skill(tmp_path, session_id=session_id)
    flag_dir = _declare_one_assignment(worktree, session_id=session_id)
    claim_assignment(
        flag_dir,
        session_id=session_id,
        top_level_parent="top_level",
        tool_use_id=tool_use_id,
    )
    settle_assignment(
        flag_dir,
        session_id=session_id,
        top_level_parent="top_level",
        tool_use_id=tool_use_id,
        outcome=OUTCOME_SUCCESS,
    )

    completed = _run_hook(
        tmp_path,
        _GUARDS_DIR / "join_stop_guard.py",
        {"session_id": session_id, "cwd": str(worktree)},
        cwd=worktree,
    )

    assert completed.returncode == 0
    assert not completed.stdout


def test_stop_guard_blocks_on_a_malformed_payload(tmp_path: Path) -> None:
    worktree = tmp_path / "worktree"
    (worktree / ".autoskillit").mkdir(parents=True)

    completed = _run_hook(
        tmp_path,
        _GUARDS_DIR / "join_stop_guard.py",
        "not valid json",
        cwd=worktree,
        env_overrides={"AUTOSKILLIT_STATE_ROOT": str(worktree)},
    )

    assert completed.returncode == 2
    assert _stdout_json(completed)["decision"] == "block"


def test_followup_guard_blocks_a_followup_while_a_wave_is_unresolved(tmp_path: Path) -> None:
    session_id = "followup-block"
    worktree = _load_join_bearing_skill(tmp_path, session_id=session_id)
    _declare_one_assignment(worktree, session_id=session_id)

    completed = _run_hook(
        tmp_path,
        _GUARDS_DIR / "join_followup_guard.py",
        {
            "tool_name": "Bash",
            "tool_input": {"command": "true"},
            "session_id": session_id,
            "cwd": str(worktree),
        },
        cwd=worktree,
    )

    assert completed.returncode == 2
    assert _stdout_json(completed)["decision"] == "block"


def test_background_exec_guard_binds_without_any_join_env_var(tmp_path: Path) -> None:
    session_id = "background-bind"
    worktree = _load_join_bearing_skill(tmp_path, session_id=session_id)

    completed = _run_hook(
        tmp_path,
        _GUARDS_DIR / "background_exec_guard.py",
        {
            "tool_name": "Agent",
            "tool_input": {"prompt": "reviewer", "name": "reviewer"},
            "session_id": session_id,
            "cwd": str(worktree),
        },
        cwd=worktree,
    )

    assert completed.returncode == 0
    output = _stdout_json(completed)["hookSpecificOutput"]
    assert isinstance(output, dict)
    assert output["permissionDecision"] == "deny"


def test_no_guard_reads_a_retired_join_env_var() -> None:
    """No hook may retain an executable read from the retired join channel."""
    reads: dict[Path, set[str]] = {}
    for source in _HOOKS_DIR.rglob("*.py"):
        collector = _EnvVarReadCollector()
        collector.visit(ast.parse(source.read_text(encoding="utf-8"), filename=str(source)))
        retired_reads = collector.reads & _RETIRED_JOIN_ENV
        if retired_reads:
            reads[source.relative_to(_PROJECT_ROOT)] = retired_reads

    assert not reads, f"retired join environment reads remain: {reads}"
