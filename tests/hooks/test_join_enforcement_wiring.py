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
from autoskillit.hooks._runtime._hook_constants import MANAGED_JOIN_PARENT_ID_ENV_VAR
from autoskillit.hooks._runtime._hook_settings import is_authenticated_top_level_cook
from autoskillit.hooks._session_binding import read_binding, resolve_binding_path, write_binding
from autoskillit.server.tools.tools_kitchen import _declare_join_batch as declare_module
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
    for name in (
        *_RETIRED_JOIN_ENV,
        "AUTOSKILLIT_LAUNCH_ID",
        "AUTOSKILLIT_HEADLESS",
        MANAGED_JOIN_PARENT_ID_ENV_VAR,
        "AUTOSKILLIT_STATE_ROOT",
    ):
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


def _skill_load_payload(
    worktree: Path,
    *,
    session_id: str,
    activation_source: str,
) -> dict[str, object]:
    if activation_source == "slash":
        return {
            "hook_event_name": "UserPromptExpansion",
            "expansion_type": "slash_command",
            "command_name": "autoskillit:join-bearing",
            "session_id": session_id,
            "cwd": str(worktree),
        }
    return {
        "hook_event_name": "PostToolUse",
        "tool_name": "Skill",
        "tool_input": {"skill": "join-bearing"},
        "session_id": session_id,
        "cwd": str(worktree),
    }


def _load_join_bearing_skill(
    tmp_path: Path,
    *,
    session_id: str = "session-1",
    activation_source: str = "PostToolUse",
    cook_launch_id: str = "",
    resumed: bool = False,
) -> Path:
    worktree, _ = _load_join_bearing_skill_with_context(
        tmp_path,
        session_id=session_id,
        activation_source=activation_source,
        cook_launch_id=cook_launch_id,
        resumed=resumed,
    )
    return worktree


def _load_join_bearing_skill_with_context(
    tmp_path: Path,
    *,
    session_id: str = "session-1",
    activation_source: str = "PostToolUse",
    cook_launch_id: str = "",
    resumed: bool = False,
) -> tuple[Path, str]:
    """Drive the projected skill-load hook so the binding is production-shaped."""
    worktree = tmp_path / "worktree"
    (worktree / ".autoskillit").mkdir(parents=True)
    projection_root, skill_load_hook = copy_projected_hook(tmp_path)
    write_projection_manifest(projection_root)
    registry_before: bytes | None = None
    if cook_launch_id:
        _write_cook_registry(
            worktree,
            launch_id=cook_launch_id,
            session_id=session_id if resumed else None,
        )
        if resumed:
            registry_before = (
                worktree / ".autoskillit" / "temp" / "session_registry.json"
            ).read_bytes()

    completed = _run_hook(
        tmp_path,
        skill_load_hook,
        _skill_load_payload(
            worktree,
            session_id=session_id,
            activation_source=activation_source,
        ),
        cwd=worktree,
        env_overrides=({"AUTOSKILLIT_LAUNCH_ID": cook_launch_id} if cook_launch_id else None),
    )
    assert completed.returncode == 0, completed.stderr
    output = _stdout_json(completed)
    if activation_source == "PostToolUse":
        context = output.get("additionalContext", "")
    else:
        hook_output = output.get("hookSpecificOutput")
        context = hook_output.get("additionalContext", "") if isinstance(hook_output, dict) else ""
    assert isinstance(context, str)
    binding = read_binding(resolve_binding_path(str(worktree), session_id))
    assert binding is not None and binding.join_required
    if registry_before is not None:
        assert (
            worktree / ".autoskillit" / "temp" / "session_registry.json"
        ).read_bytes() == registry_before
    return worktree, context


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


def _write_cook_registry(worktree: Path, *, launch_id: str, session_id: str | None) -> None:
    registry_path = worktree / ".autoskillit" / "temp" / "session_registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                launch_id: {
                    "session_type": "cook",
                    "claude_session_id": session_id,
                }
            }
        ),
        encoding="utf-8",
    )


def _configure_non_cook_shape(
    worktree: Path,
    *,
    session_id: str,
    case: str,
) -> tuple[dict[str, object], str, dict[str, str]]:
    launch_id = "cook-launch"
    payload_session_id = session_id
    binding_session_id = session_id
    payload: dict[str, object] = {
        "session_id": payload_session_id,
        "cwd": str(worktree),
    }
    env = {"AUTOSKILLIT_LAUNCH_ID": launch_id}
    registry_path = worktree / ".autoskillit" / "temp" / "session_registry.json"

    if case == "missing_registry_identity":
        registry_path.unlink(missing_ok=True)
    elif case == "malformed_registry":
        registry_path.parent.mkdir(parents=True, exist_ok=True)
        registry_path.write_text("not-json", encoding="utf-8")
    elif case == "ambiguous_registry_identity":
        _write_cook_registry(worktree, launch_id=launch_id, session_id=session_id)
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["duplicate-launch"] = {
            "session_type": "cook",
            "claude_session_id": session_id,
        }
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
    elif case == "launch_session_mismatch":
        _write_cook_registry(worktree, launch_id=launch_id, session_id="other-session")
    elif case == "headless":
        _write_cook_registry(worktree, launch_id=launch_id, session_id=session_id)
        env["AUTOSKILLIT_HEADLESS"] = "1"
    elif case in {"non_cook_interactive", "order"}:
        _write_cook_registry(worktree, launch_id=launch_id, session_id=session_id)
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry[launch_id]["session_type"] = (
            "skill" if case == "non_cook_interactive" else "order"
        )
        registry_path.write_text(json.dumps(registry), encoding="utf-8")
    elif case == "descendant":
        _write_cook_registry(worktree, launch_id=launch_id, session_id=session_id)
        payload["agent_id"] = "child-agent"
    elif case == "managed_leaf":
        launch_id = session_id
        payload_session_id = "codex-thread"
        payload["session_id"] = payload_session_id
        _write_cook_registry(worktree, launch_id=launch_id, session_id=None)
        binding_path = resolve_binding_path(str(worktree), session_id)
        binding = read_binding(binding_path)
        assert binding is not None
        write_binding(
            binding_path,
            binding._replace(
                managed_parent_id=session_id,
                managed_leaf_id="managed-leaf",
                managed_route="leaf",
                managed_config_digest="managed-config",
            ),
        )
        env = {
            "AUTOSKILLIT_AGENT_BACKEND": "codex",
            "AUTOSKILLIT_LAUNCH_ID": launch_id,
            MANAGED_JOIN_PARENT_ID_ENV_VAR: session_id,
        }
    else:
        raise AssertionError(f"unknown non-cook case: {case}")
    return payload, binding_session_id, env


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


@pytest.mark.parametrize(
    ("activation_source", "resumed"),
    (("slash", False), ("PostToolUse", True)),
    ids=("fresh-slash", "resumed-post-tool-use"),
)
def test_authenticated_top_level_cook_full_join_sequence_never_opens_a_wave(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    activation_source: str,
    resumed: bool,
) -> None:
    session_id = "interactive-cook"
    launch_id = "cook-launch"
    monkeypatch.setenv("AUTOSKILLIT_LAUNCH_ID", launch_id)
    monkeypatch.setenv("AUTOSKILLIT_AGENT_BACKEND", "claude-code")
    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "skill")
    monkeypatch.setenv("AUTOSKILLIT_LOG_DIR", str(tmp_path / "logs"))
    worktree, context = _load_join_bearing_skill_with_context(
        tmp_path,
        session_id=session_id,
        activation_source=activation_source,
        cook_launch_id=launch_id,
        resumed=resumed,
    )
    assert "JOIN DECLARATION AUTHORITY" in context
    assert "cook_bypass" in context

    env = {"AUTOSKILLIT_LAUNCH_ID": launch_id}
    declared = declare_module._declare_join_batch_handler(
        skill_name="join-bearing",
        assignments=["a", "b", "c", "d"],
        session_id=session_id,
        project_root=worktree,
    )
    assert declared["success"] is True
    assert declared["status"] == "cook_bypass"
    assert declared["join_batch_id"] is None
    assert declared["wave"] is None
    # Assert the documented bypass phrase from _COOK_BYPASS_MESSAGE rather than
    # the loose "cook" substring (which would match many unrelated messages).
    assert "no join wave was opened" in str(declared["message"])

    guard_events = (
        (
            "join_claim_guard.py",
            _agent_payload(worktree, session_id=session_id, tool_use_id="agent-1"),
        ),
        (
            "join_settle_guard.py",
            {
                **_agent_payload(worktree, session_id=session_id, tool_use_id="agent-1"),
                "hook_event_name": "PostToolUse",
                "tool_response": "complete",
            },
        ),
        (
            "join_followup_guard.py",
            {
                "tool_name": "Bash",
                "tool_input": {"command": "true"},
                "session_id": session_id,
                "cwd": str(worktree),
            },
        ),
        (
            "join_stop_guard.py",
            {"session_id": session_id, "cwd": str(worktree)},
        ),
    )
    for script_name, payload in guard_events[:2]:
        completed = _run_hook(
            tmp_path,
            _GUARDS_DIR / script_name,
            payload,
            cwd=worktree,
            env_overrides=env,
        )
        assert completed.returncode == 0, (script_name, completed.stderr)
        assert not completed.stdout, script_name

    redeclared = declare_module._declare_join_batch_handler(
        skill_name="join-bearing",
        assignments=["a", "b", "c", "d"],
        session_id=session_id,
        project_root=worktree,
    )
    assert redeclared["success"] is True
    assert redeclared["status"] == "cook_bypass"
    assert redeclared["join_batch_id"] is None
    assert redeclared["wave"] is None

    for script_name, payload in guard_events[2:]:
        completed = _run_hook(
            tmp_path,
            _GUARDS_DIR / script_name,
            payload,
            cwd=worktree,
            env_overrides=env,
        )
        assert completed.returncode == 0, (script_name, completed.stderr)
        assert not completed.stdout, script_name

    assert (
        active_batch(
            resolve_flag_dir(worktree),
            session_id=session_id,
            top_level_parent="top_level",
        )
        is None
    )
    diagnostics_path = tmp_path / "logs" / "join_diagnostics.jsonl"
    diagnostics = [json.loads(line) for line in diagnostics_path.read_text().splitlines()]
    assert all(record["status"] == "cook_bypass" for record in diagnostics)
    assert {record["gate"] for record in diagnostics} == {
        "skill_load_post_hook",
        "declare_join_batch",
        "join_claim_guard",
        "join_settle_guard",
        "join_followup_guard",
        "join_stop_guard",
    }
    assert sum(record["gate"] == "declare_join_batch" for record in diagnostics) == 2
    assert all(record["managed_parent_id"] == "top_level" for record in diagnostics)


def test_cook_redeclaration_is_not_refused_by_a_pre_fix_stranded_wave(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = "stranded-cook"
    launch_id = "cook-launch"
    monkeypatch.setenv("AUTOSKILLIT_LAUNCH_ID", launch_id)
    monkeypatch.setenv("AUTOSKILLIT_AGENT_BACKEND", "claude-code")
    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "skill")
    monkeypatch.setenv("AUTOSKILLIT_LOG_DIR", str(tmp_path / "logs"))
    worktree = _load_join_bearing_skill(
        tmp_path,
        session_id=session_id,
        cook_launch_id=launch_id,
    )
    flag_dir = _declare_one_assignment(worktree, session_id=session_id)
    legacy_batch = active_batch(
        flag_dir,
        session_id=session_id,
        top_level_parent="top_level",
    )
    assert legacy_batch is not None

    result = declare_module._declare_join_batch_handler(
        skill_name="join-bearing",
        assignments=["a", "b", "c", "d"],
        session_id=session_id,
        project_root=worktree,
    )
    assert result["success"] is True
    assert result["status"] == "cook_bypass"

    env = {"AUTOSKILLIT_LAUNCH_ID": launch_id}
    events = (
        (
            "join_claim_guard.py",
            _agent_payload(worktree, session_id=session_id, tool_use_id="agent-1"),
        ),
        (
            "join_settle_guard.py",
            {
                **_agent_payload(worktree, session_id=session_id, tool_use_id="agent-1"),
                "hook_event_name": "PostToolUse",
                "tool_response": "complete",
            },
        ),
        (
            "join_followup_guard.py",
            {
                "tool_name": "Bash",
                "tool_input": {"command": "true"},
                "session_id": session_id,
                "cwd": str(worktree),
            },
        ),
        ("join_stop_guard.py", {"session_id": session_id, "cwd": str(worktree)}),
    )
    for script_name, payload in events:
        completed = _run_hook(
            tmp_path,
            _GUARDS_DIR / script_name,
            payload,
            cwd=worktree,
            env_overrides=env,
        )
        assert completed.returncode == 0, (script_name, completed.stderr)
        assert not completed.stdout, script_name

    unchanged = active_batch(
        flag_dir,
        session_id=session_id,
        top_level_parent="top_level",
    )
    assert unchanged is not None
    assert unchanged == legacy_batch
    assert unchanged["wave_outcome"] == "pending"
    assert unchanged["assignments"][0]["tool_use_id"] is None


def test_authenticated_cook_bypass_survives_an_absent_managed_scope(tmp_path: Path) -> None:
    session_id = "cook-without-binding"
    launch_id = "cook-launch"
    worktree = tmp_path / "worktree"
    (worktree / ".autoskillit").mkdir(parents=True)
    _write_cook_registry(worktree, launch_id=launch_id, session_id=session_id)
    env = {"AUTOSKILLIT_LAUNCH_ID": launch_id}
    events = (
        (
            "join_claim_guard.py",
            _agent_payload(worktree, session_id=session_id, tool_use_id="agent-1"),
        ),
        (
            "join_settle_guard.py",
            {
                **_agent_payload(worktree, session_id=session_id, tool_use_id="agent-1"),
                "hook_event_name": "PostToolUse",
                "tool_response": "complete",
            },
        ),
        (
            "join_followup_guard.py",
            {
                "tool_name": "Bash",
                "tool_input": {"command": "true"},
                "session_id": session_id,
                "cwd": str(worktree),
            },
        ),
        ("join_stop_guard.py", {"session_id": session_id, "cwd": str(worktree)}),
    )

    for script_name, payload in events:
        completed = _run_hook(
            tmp_path,
            _GUARDS_DIR / script_name,
            payload,
            cwd=worktree,
            env_overrides=env,
        )
        assert completed.returncode == 0, (script_name, completed.stderr)
        assert completed.stdout == ""

    diagnostics_path = tmp_path / "logs" / "join_diagnostics.jsonl"
    diagnostics = [json.loads(line) for line in diagnostics_path.read_text().splitlines()]
    bypasses = [record for record in diagnostics if record.get("status") == "cook_bypass"]
    assert len(bypasses) == 4
    assert all(record["managed_parent_id"] == "" for record in bypasses)
    assert all(record["managed_leaf_id"] == "" for record in bypasses)


@pytest.mark.parametrize(
    "case",
    (
        "non_cook_interactive",
        "order",
        "headless",
        "missing_registry_identity",
        "malformed_registry",
        "ambiguous_registry_identity",
        "launch_session_mismatch",
        "descendant",
        "managed_leaf",
    ),
)
def test_cook_authentication_rejects_non_top_level_shapes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    from autoskillit.hooks._runtime._hook_settings import (
        is_authenticated_top_level_cook_session,
    )

    session_id = f"non-cook-{case}"
    worktree = _load_join_bearing_skill(tmp_path, session_id=session_id)
    payload, binding_session_id, env = _configure_non_cook_shape(
        worktree,
        session_id=session_id,
        case=case,
    )
    monkeypatch.setenv("AUTOSKILLIT_AGENT_BACKEND", "claude-code")
    monkeypatch.setenv("AUTOSKILLIT_STATE_ROOT", str(worktree))
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    assert not is_authenticated_top_level_cook(
        payload,
        str(worktree),
        binding_session_id,
    )
    assert is_authenticated_top_level_cook_session(
        str(worktree),
        binding_session_id,
    ) is (case == "descendant")
    if case == "descendant":
        return

    claim = _run_hook(
        tmp_path,
        _GUARDS_DIR / "join_claim_guard.py",
        {
            **payload,
            "tool_name": "Agent",
            "tool_input": {"prompt": "declared work"},
            "tool_use_id": "agent-1",
        },
        cwd=worktree,
        env_overrides=env,
    )
    assert claim.returncode == 0
    claim_output = _stdout_json(claim)["hookSpecificOutput"]
    assert isinstance(claim_output, dict)
    assert claim_output["permissionDecision"] == "deny"

    stop = _run_hook(
        tmp_path,
        _GUARDS_DIR / "join_stop_guard.py",
        payload,
        cwd=worktree,
        env_overrides=env,
    )
    if case == "managed_leaf":
        assert stop.returncode == 0
        assert stop.stdout == ""
    else:
        assert stop.returncode == 2
        assert _stdout_json(stop)["decision"] == "block"
    monkeypatch.setenv("AUTOSKILLIT_LOG_DIR", str(tmp_path / "logs"))
    result = declare_module._declare_join_batch_handler(
        skill_name="join-bearing",
        assignments=["a", "b", "c", "d"],
        session_id=binding_session_id,
        project_root=worktree,
    )
    if case == "managed_leaf":
        assert result["success"] is False
        assert "does not attest fixed_set_join_capable" in str(result["error"])
    else:
        assert result["success"] is True
        assert result["status"] == "declared"
        assert (
            active_batch(
                resolve_flag_dir(worktree),
                session_id=binding_session_id,
                top_level_parent="top_level",
            )
            is not None
        )

    diagnostics_path = tmp_path / "logs" / "join_diagnostics.jsonl"
    if diagnostics_path.exists():
        diagnostics = [json.loads(line) for line in diagnostics_path.read_text().splitlines()]
        assert not [record for record in diagnostics if record.get("status") == "cook_bypass"]


def test_descendant_is_natively_exempt_but_never_authenticated_as_top_level_cook(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = "descendant-cook-shape"
    worktree = _load_join_bearing_skill(tmp_path, session_id=session_id)
    flag_dir = _declare_one_assignment(worktree, session_id=session_id)
    payload, binding_session_id, env = _configure_non_cook_shape(
        worktree,
        session_id=session_id,
        case="descendant",
    )
    monkeypatch.delenv(MANAGED_JOIN_PARENT_ID_ENV_VAR, raising=False)
    monkeypatch.setenv("AUTOSKILLIT_AGENT_BACKEND", "claude-code")
    monkeypatch.setenv("AUTOSKILLIT_STATE_ROOT", str(worktree))
    monkeypatch.setenv("AUTOSKILLIT_LAUNCH_ID", env["AUTOSKILLIT_LAUNCH_ID"])
    assert not is_authenticated_top_level_cook(payload, str(worktree), binding_session_id)

    events = (
        (
            "join_claim_guard.py",
            {
                **payload,
                "tool_name": "Agent",
                "tool_input": {"prompt": "nested work"},
                "tool_use_id": "nested-agent",
            },
        ),
        (
            "join_settle_guard.py",
            {
                **payload,
                "hook_event_name": "PostToolUse",
                "tool_name": "Agent",
                "tool_use_id": "nested-agent",
                "tool_response": "complete",
            },
        ),
        (
            "join_followup_guard.py",
            {**payload, "tool_name": "Bash", "tool_input": {"command": "true"}},
        ),
    )
    for script_name, event in events:
        completed = _run_hook(
            tmp_path,
            _GUARDS_DIR / script_name,
            event,
            cwd=worktree,
            env_overrides=env,
        )
        assert completed.returncode == 0, (script_name, completed.stderr)
        assert completed.stdout == ""

    batch = active_batch(flag_dir, session_id=session_id, top_level_parent="top_level")
    assert batch is not None
    assert batch["wave_outcome"] == "pending"
    assert batch["assignments"][0]["tool_use_id"] is None
    stop = _run_hook(
        tmp_path,
        _GUARDS_DIR / "join_stop_guard.py",
        payload,
        cwd=worktree,
        env_overrides=env,
    )
    assert stop.returncode == 2
    assert _stdout_json(stop)["decision"] == "block"


def test_claim_and_settle_guards_use_the_managed_binding_identity(tmp_path: Path) -> None:
    payload_session_id = "codex-thread-id"
    managed_join_id = "managed-join-id"
    worktree = _load_join_bearing_skill(tmp_path, session_id=payload_session_id)
    payload_binding = read_binding(resolve_binding_path(str(worktree), payload_session_id))
    assert payload_binding is not None
    write_binding(
        resolve_binding_path(str(worktree), managed_join_id),
        payload_binding._replace(
            session_id=managed_join_id,
            managed_parent_id=managed_join_id,
            managed_route="parent",
            managed_config_digest="managed-config",
        ),
    )
    flag_dir = resolve_flag_dir(worktree)
    declare_batch(
        flag_dir,
        session_id=managed_join_id,
        top_level_parent=managed_join_id,
        skill_name="join-bearing",
        artifact_digest="artdigest-1",
        assignments=("worker",),
    )
    env = {
        "AUTOSKILLIT_AGENT_BACKEND": "codex",
        MANAGED_JOIN_PARENT_ID_ENV_VAR: managed_join_id,
    }
    payload = _agent_payload(
        worktree,
        session_id=payload_session_id,
        tool_use_id="agent-1",
    )

    claimed = _run_hook(
        tmp_path,
        _GUARDS_DIR / "join_claim_guard.py",
        payload,
        cwd=worktree,
        env_overrides=env,
    )
    assert claimed.returncode == 0, claimed.stderr
    assert not claimed.stdout

    settled = _run_hook(
        tmp_path,
        _GUARDS_DIR / "join_settle_guard.py",
        {
            **payload,
            "hook_event_name": "PostToolUse",
            "tool_response": "complete",
        },
        cwd=worktree,
        env_overrides=env,
    )
    assert settled.returncode == 0, settled.stderr
    batch = active_batch(
        flag_dir,
        session_id=managed_join_id,
        top_level_parent=managed_join_id,
    )
    assert batch is not None
    assert batch["wave_outcome"] == "complete"


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


def test_stop_guard_blocks_an_invalid_managed_scope(tmp_path: Path) -> None:
    session_id = "stop-invalid-scope"
    worktree = _load_join_bearing_skill(tmp_path, session_id=session_id)
    binding_path = resolve_binding_path(str(worktree), session_id)
    binding = read_binding(binding_path)
    assert binding is not None
    write_binding(binding_path, binding._replace(binding_valid=False))

    completed = _run_hook(
        tmp_path,
        _GUARDS_DIR / "join_stop_guard.py",
        {"session_id": session_id, "cwd": str(worktree)},
        cwd=worktree,
    )

    assert completed.returncode == 2
    assert "required-join binding scope" in _stdout_json(completed)["reason"]


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


@pytest.mark.parametrize(
    "tool_name",
    (
        "mcp__autoskillit__declare_join_batch",
        "mcp__plugin_autoskillit_autoskillit__declare_join_batch",
    ),
)
def test_followup_guard_allows_exact_recovery_declaration_after_terminal_failure(
    tmp_path: Path,
    tool_name: str,
) -> None:
    session_id = "followup-recovery"
    worktree = _load_join_bearing_skill(tmp_path, session_id=session_id)
    flag_dir = _declare_one_assignment(worktree, session_id=session_id)
    claim_assignment(
        flag_dir,
        session_id=session_id,
        top_level_parent="top_level",
        tool_use_id="agent-1",
    )
    settle_assignment(
        flag_dir,
        session_id=session_id,
        top_level_parent="top_level",
        tool_use_id="agent-1",
        outcome=OUTCOME_FAILURE,
    )

    completed = _run_hook(
        tmp_path,
        _GUARDS_DIR / "join_followup_guard.py",
        {
            "tool_name": tool_name,
            "tool_input": {},
            "session_id": session_id,
            "cwd": str(worktree),
        },
        cwd=worktree,
    )

    assert completed.returncode == 0, completed.stderr
    assert not completed.stdout
    diagnostics_path = tmp_path / "logs" / "join_diagnostics.jsonl"
    diagnostics = [json.loads(line) for line in diagnostics_path.read_text().splitlines()]
    allowed = [
        record for record in diagnostics if record.get("status") == "recovery_declaration_allowed"
    ]
    assert len(allowed) == 1
    assert allowed[0]["gate"] == "join_followup_guard"
    assert allowed[0]["session_id"] == session_id
    assert allowed[0]["top_level_parent"] == "top_level"
    assert allowed[0]["join_batch_id"]
    assert allowed[0]["tool_name"] == tool_name


@pytest.mark.parametrize(
    "tool_name",
    (
        "Bash",
        "declare_join_batch",
        "mcp__autoskillit__declare_join_batch__suffix",
        "mcp__unrelated__declare_join_batch",
        "Stop",
    ),
)
def test_followup_guard_keeps_other_effects_blocked_after_terminal_failure(
    tmp_path: Path,
    tool_name: str,
) -> None:
    session_id = f"followup-terminal-block-{tool_name}"
    worktree = _load_join_bearing_skill(tmp_path, session_id=session_id)
    flag_dir = _declare_one_assignment(worktree, session_id=session_id)
    claim_assignment(
        flag_dir,
        session_id=session_id,
        top_level_parent="top_level",
        tool_use_id="agent-1",
    )
    settle_assignment(
        flag_dir,
        session_id=session_id,
        top_level_parent="top_level",
        tool_use_id="agent-1",
        outcome=OUTCOME_FAILURE,
    )

    completed = _run_hook(
        tmp_path,
        _GUARDS_DIR / "join_followup_guard.py",
        {
            "tool_name": tool_name,
            "tool_input": {},
            "session_id": session_id,
            "cwd": str(worktree),
        },
        cwd=worktree,
    )

    assert completed.returncode == 2
    assert _stdout_json(completed)["decision"] == "block"


def test_followup_guard_blocks_recovery_declaration_while_wave_is_pending(
    tmp_path: Path,
) -> None:
    session_id = "followup-pending-recovery"
    worktree = _load_join_bearing_skill(tmp_path, session_id=session_id)
    _declare_one_assignment(worktree, session_id=session_id)

    completed = _run_hook(
        tmp_path,
        _GUARDS_DIR / "join_followup_guard.py",
        {
            "tool_name": "mcp__autoskillit__declare_join_batch",
            "tool_input": {},
            "session_id": session_id,
            "cwd": str(worktree),
        },
        cwd=worktree,
    )

    assert completed.returncode == 2
    assert _stdout_json(completed)["decision"] == "block"


def test_followup_guard_blocks_recovery_declaration_when_ledger_is_corrupt(
    tmp_path: Path,
) -> None:
    session_id = "followup-corrupt-recovery"
    worktree = _load_join_bearing_skill(tmp_path, session_id=session_id)
    flag_dir = _declare_one_assignment(worktree, session_id=session_id)
    (flag_dir / "join_ledger.json").write_text("not-json", encoding="utf-8")

    completed = _run_hook(
        tmp_path,
        _GUARDS_DIR / "join_followup_guard.py",
        {
            "tool_name": "mcp__autoskillit__declare_join_batch",
            "tool_input": {},
            "session_id": session_id,
            "cwd": str(worktree),
        },
        cwd=worktree,
    )

    assert completed.returncode == 2
    assert _stdout_json(completed)["decision"] == "block"


def test_stop_guard_stays_blocked_after_terminal_failure(tmp_path: Path) -> None:
    session_id = "stop-terminal-failure"
    worktree = _load_join_bearing_skill(tmp_path, session_id=session_id)
    flag_dir = _declare_one_assignment(worktree, session_id=session_id)
    claim_assignment(
        flag_dir,
        session_id=session_id,
        top_level_parent="top_level",
        tool_use_id="agent-1",
    )
    settle_assignment(
        flag_dir,
        session_id=session_id,
        top_level_parent="top_level",
        tool_use_id="agent-1",
        outcome=OUTCOME_FAILURE,
    )

    completed = _run_hook(
        tmp_path,
        _GUARDS_DIR / "join_stop_guard.py",
        {"session_id": session_id, "cwd": str(worktree)},
        cwd=worktree,
    )

    assert completed.returncode == 2
    assert "settled non-success" in str(_stdout_json(completed)["reason"])


def test_followup_guard_prefers_the_managed_join_identity(tmp_path: Path) -> None:
    payload_session_id = "codex-thread-id"
    managed_join_id = "managed-join-id"
    worktree = _load_join_bearing_skill(tmp_path, session_id=payload_session_id)
    binding = read_binding(resolve_binding_path(str(worktree), payload_session_id))
    assert binding is not None
    write_binding(
        resolve_binding_path(str(worktree), managed_join_id),
        binding._replace(
            session_id=managed_join_id,
            managed_parent_id=managed_join_id,
            managed_route="parent",
            managed_guard_set=("join_followup_guard",),
            managed_config_digest="managed-config",
        ),
    )
    declare_batch(
        resolve_flag_dir(worktree),
        session_id=managed_join_id,
        top_level_parent=managed_join_id,
        skill_name="join-bearing",
        artifact_digest="artdigest-1",
        assignments=("worker",),
    )

    completed = _run_hook(
        tmp_path,
        _GUARDS_DIR / "join_followup_guard.py",
        {
            "tool_name": "Bash",
            "tool_input": {"command": "true"},
            "session_id": payload_session_id,
            "cwd": str(worktree),
        },
        cwd=worktree,
        env_overrides={
            "AUTOSKILLIT_AGENT_BACKEND": "codex",
            MANAGED_JOIN_PARENT_ID_ENV_VAR: managed_join_id,
        },
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
