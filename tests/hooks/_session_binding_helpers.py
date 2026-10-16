"""Shared projection fixtures for session-binding hook tests."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.hooks._join_ledger import (
    OUTCOME_SUCCESS,
    claim_assignment,
    declare_batch,
    resolve_flag_dir,
    settle_assignment,
)
from autoskillit.hooks._runtime._hook_constants import MANAGED_JOIN_PARENT_ID_ENV_VAR
from autoskillit.hooks._session_binding import (
    PROJECTION_MANIFEST_SCHEMA_VERSION,
    read_binding,
    resolve_binding_path,
)
from autoskillit.hooks._write_scope import WriteScope, WriteScopeKind, encode_write_scope
from tests._hook_protocol_oracle import (
    STATUS_COMPLETED,
    claude_verdict,
    codex_verdict,
    run_hook,
)

if TYPE_CHECKING:
    from autoskillit.hooks._runtime._hook_output import HookEmission

_HOOKS_SOURCE = Path(__file__).resolve().parents[2] / "src" / "autoskillit" / "hooks"
_RUNTIME_SOURCE = _HOOKS_SOURCE / "_runtime"

# Flat names live directly under hooks/; stdlib-only runtime siblings live under hooks/_runtime/
# (post-#4672 decomposition). The dispatcher bootstrap adds both paths to sys.path.
_PROJECTED_HOOK_FILES_FLAT = (
    "skill_load_post_hook.py",
    "_session_binding.py",
    "_write_scope.py",
)
_PROJECTED_HOOK_FILES_RUNTIME = (
    "_guard_decision_diagnostics.py",
    "_hook_log_dispatch.py",
    "_hook_output.py",
    "_hook_payload.py",
    "_session_registry_bridge.py",
    "_hook_settings.py",
)
_PROJECTED_HOOK_FILES = _PROJECTED_HOOK_FILES_FLAT + _PROJECTED_HOOK_FILES_RUNTIME

_SOURCE_FOR = {name: _HOOKS_SOURCE for name in _PROJECTED_HOOK_FILES_FLAT}
_SOURCE_FOR.update({name: _RUNTIME_SOURCE for name in _PROJECTED_HOOK_FILES_RUNTIME})


def copy_projected_hook(tmp_path: Path, name: str = "join-plugin") -> tuple[Path, Path]:
    """Copy the stdlib-only hook runtime under a projected plugin root.

    Runtime siblings such as `_hook_output.py`, `_hook_payload.py`, and
    `_hook_settings.py` live under `hooks/_runtime/`. The dispatcher bootstrap
    adds both `hooks/` and `hooks/_runtime/` to sys.path, so the test fixture
    mirrors that layout here.
    """
    projection_root = tmp_path / name
    hooks_dir = projection_root / "hooks"
    runtime_dir = hooks_dir / "_runtime"
    runtime_dir.mkdir(parents=True)
    for filename in _PROJECTED_HOOK_FILES:
        dest = (
            runtime_dir / filename
            if filename in _PROJECTED_HOOK_FILES_RUNTIME
            else hooks_dir / filename
        )
        shutil.copy2(_SOURCE_FOR[filename] / filename, dest)
    return projection_root, hooks_dir / "skill_load_post_hook.py"


def write_projection_manifest(
    projection_root: Path,
    *,
    skill_name: str = "join-bearing",
    join_required: bool = True,
    schema_version: int = PROJECTION_MANIFEST_SCHEMA_VERSION,
    artifact_digest: str = "artdigest-1",
    semantic_digest: str = "sem-1",
    adaptation_digest: str = "adapt-1",
    projected_digest: str = "proj-1",
    canonical_digest: str = "canon-1",
    child_spawn_cardinality: dict[str, object] | None = None,
    write_scope: WriteScope = WriteScope(WriteScopeKind.INHERIT),
) -> Path:
    """Write a schema-versioned projection sidecar beside a projected hook root."""
    manifest_path = projection_root.parent / (
        f".{projection_root.name}.autoskillit-projection.json"
    )
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": schema_version,
                "artifact_kind": "projection",
                "projection_version": 1,
                "semantic_key": "autoskillit@session-binding-test:1",
                "incarnation_id": "00000000000040008000000000000001",
                "artifact_digest": artifact_digest,
                "skills": {
                    skill_name: {
                        "join_required": join_required,
                        "child_spawn_cardinality": (
                            child_spawn_cardinality
                            if child_spawn_cardinality is not None
                            else {"explicit_slots": 4}
                        ),
                        "semantic_digest": semantic_digest,
                        "adaptation_digest": adaptation_digest,
                        "projected_digest": projected_digest,
                        "canonical_digest": canonical_digest,
                        "write_scope": encode_write_scope(write_scope),
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return manifest_path


RETIRED_JOIN_ENV = frozenset(
    {
        "AUTOSKILLIT_JOIN_FLAG_PATH",
        "AUTOSKILLIT_JOIN_REQUIRED",
        "AUTOSKILLIT_HOOK_EVENT",
        "AUTOSKILLIT_SESSION_ID",
        "AUTOSKILLIT_JOIN_SESSION_ID",
        "AUTOSKILLIT_JOIN_PARENT",
    }
)


JOIN_HOOK_UNSET = (
    *RETIRED_JOIN_ENV,
    "AUTOSKILLIT_LAUNCH_ID",
    MANAGED_JOIN_PARENT_ID_ENV_VAR,
    "AUTOSKILLIT_STATE_ROOT",
    "AUTOSKILLIT_STATE_DIR",
    "AUTOSKILLIT_HEADLESS",
)


def join_hook_env(tmp_path: Path) -> dict[str, str]:
    return {
        "AUTOSKILLIT_AGENT_BACKEND": "claude-code",
        "AUTOSKILLIT_LOG_DIR": str(tmp_path / "logs"),
        "AUTOSKILLIT_SESSION_TYPE": "skill",
    }


def run_join_hook(
    tmp_path: Path,
    hook: Path,
    payload: dict[str, object] | str,
    *,
    cwd: Path,
    env_overrides: dict[str, str] | None = None,
) -> HookEmission:
    """Run a real hook program with the shared protocol-test environment."""
    script = hook.relative_to(_HOOKS_SOURCE) if hook.is_relative_to(_HOOKS_SOURCE) else hook
    return run_hook(
        script,
        payload,
        env={**join_hook_env(tmp_path), **(env_overrides or {})},
        unset=JOIN_HOOK_UNSET,
        cwd=cwd,
    )


def skill_load_payload(
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


def load_join_bearing_skill(
    tmp_path: Path,
    *,
    session_id: str = "session-1",
    activation_source: str = "PostToolUse",
    cook_launch_id: str = "",
    resumed: bool = False,
) -> Path:
    worktree, _ = load_join_bearing_skill_with_context(
        tmp_path,
        session_id=session_id,
        activation_source=activation_source,
        cook_launch_id=cook_launch_id,
        resumed=resumed,
    )
    return worktree


def load_join_bearing_skill_with_context(
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
        write_cook_registry(
            worktree,
            launch_id=cook_launch_id,
            session_id=session_id if resumed else None,
        )
        if resumed:
            registry_before = (
                worktree / ".autoskillit" / "temp" / "session_registry.json"
            ).read_bytes()

    skill_load_event = skill_load_payload(
        worktree,
        session_id=session_id,
        activation_source=activation_source,
    )
    completed = run_join_hook(
        tmp_path,
        skill_load_hook,
        skill_load_event,
        cwd=worktree,
        env_overrides=({"AUTOSKILLIT_LAUNCH_ID": cook_launch_id} if cook_launch_id else None),
    )
    assert completed.exit_code == 0, completed.stderr
    output = json.loads(completed.stdout)
    hook_output = output.get("hookSpecificOutput")
    context = hook_output.get("additionalContext", "") if isinstance(hook_output, dict) else ""
    assert isinstance(context, str)
    assert context
    if activation_source == "PostToolUse":
        for verdict_fn in (codex_verdict, claude_verdict):
            verdict = verdict_fn(
                "PostToolUse",
                exit_code=completed.exit_code,
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
            assert verdict.status == STATUS_COMPLETED
            assert verdict.contexts
    binding = read_binding(resolve_binding_path(str(worktree), session_id))
    assert binding is not None and binding.join_required
    if registry_before is not None:
        assert (
            worktree / ".autoskillit" / "temp" / "session_registry.json"
        ).read_bytes() == registry_before
    return worktree, context


def declare_one_assignment(worktree: Path, *, session_id: str) -> Path:
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


def write_cook_registry(worktree: Path, *, launch_id: str, session_id: str | None) -> None:
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


def complete_one_assignment_wave(worktree: Path, *, session_id: str) -> None:
    flag_dir = declare_one_assignment(worktree, session_id=session_id)
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
        outcome=OUTCOME_SUCCESS,
    )
