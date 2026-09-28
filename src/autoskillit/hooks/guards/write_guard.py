"""PreToolUse write boundary for headless launcher and interactive skill sessions.

Headless sessions use launcher supplied prefixes. Interactive Claude sessions use
the union of the loaded manifest skills' projected write scopes: a BOUNDED skill
contributes its temp directories, an UNRESTRICTED skill lifts the boundary for the
session (allowed with reason ``unrestricted_skill``), and an INHERIT or foreign
skill abstains. An invalid or foreign-session binding, an unreadable manifest or
manifest entry, and a declared directory that resolves outside the session temp
root fail closed with the precise cause. Codex relies on its workspace sandbox for
this boundary; installation writes have a separate guard.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Literal, NamedTuple, assert_never

_HOOKS_DIR = str(Path(__file__).resolve().parent.parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)
_RUNTIME_DIR = str(Path(_HOOKS_DIR) / "_runtime")
if _RUNTIME_DIR not in sys.path:
    sys.path.insert(0, _RUNTIME_DIR)


from _command_classification import (  # noqa: E402
    UNRESOLVED_WRITE_TARGET_REMEDIATION,
    WriteTargetScan,
    extract_interpreter_write_paths,
    extract_patch_paths,
    scan_write_targets,
)
from _guard_decision_diagnostics import (  # noqa: E402
    record_guard_decision,
)
from _hook_payload import (  # noqa: E402
    extract_apply_patch_text,
    parse_hook_command,
)
from _hook_settings import (  # noqa: E402
    enforce_session_scope,
    hook_session_shape,
)
from _session_binding import (  # noqa: E402
    BindingReadOutcome,
    LoadedSkillEntry,
    LoadedSkillOrigin,
    SessionBindingError,
    manifest_skill_write_scope,
    read_binding_outcome,
    read_manifest,
    resolve_projection_manifest_path,
)
from _write_scope import (  # noqa: E402
    SessionScopeState,
    SessionWriteBoundary,
    WriteScope,
    fold_session_write_scopes,
    temp_root_escape,
)

WRITE_GUARD_DENY_TRIGGER = "read-only skill session"

# Per-process set of prefix values that already produced a realpath-failure warning;
# pytest-xdist workers get isolated copies via the import boundary.
_WARNED_PREFIXES: set[str] = set()
_LOGGER = logging.getLogger(__name__)  # noqa: TID251 - standalone stdlib guard
# Empty-boundary denial hint, keyed by activation. Kept terse to bound JSON payload size.
_EMPTY_BOUNDARY_HINT_BY_ACTIVATION: dict[str, str] = {
    "headless": (
        "every prefix in AUTOSKILLIT_ALLOWED_WRITE_PREFIX "
        "or AUTOSKILLIT_ALLOWED_WRITE_PREFIXES failed realpath normalization"
    ),
    "skill_binding": ("every write_paths entry in session_binding failed realpath normalization"),
}

_PolicyState = Literal["none", "unrestricted", "active", "empty", "unresolved"]


class _WritePolicy(NamedTuple):
    """The resolved write boundary: normalized prefixes when active, else a cause."""

    state: _PolicyState
    prefixes: tuple[str, ...]
    display: str
    cause: str


def _resolution(state: _PolicyState, cause: str = "") -> _WritePolicy:
    return _WritePolicy(state, (), "", cause)


class _InteractiveBinding(NamedTuple):
    payload_cwd: str
    loaded_skills: tuple[LoadedSkillEntry, ...]


def _effective_execution_cwd(execution_cwd: str) -> str:
    if execution_cwd:
        return execution_cwd
    cwd = os.environ.get("AUTOSKILLIT_CWD", "")
    if cwd and not os.path.isabs(cwd):
        return ""
    return cwd


def _extract_bash_write_targets(command: str, execution_cwd: str = "") -> WriteTargetScan:
    """Scan Bash writes using the tool's execution cwd when available."""
    return scan_write_targets(command, _effective_execution_cwd(execution_cwd))


def _bash_validation_error(
    command: str, execution_cwd: str, norm_prefixes: list[str], display_prefix: str
) -> str | None:
    scan = _extract_bash_write_targets(command, execution_cwd)
    if not scan.parseable:
        return (
            f"Write/Edit/apply_patch blocked: {WRITE_GUARD_DENY_TRIGGER} "
            "(unparseable Bash command). Correct the shell syntax and retry."
        )
    if scan.unresolved:
        return (
            f"Write/Edit/apply_patch blocked: {WRITE_GUARD_DENY_TRIGGER} "
            "(unresolved write target). " + UNRESOLVED_WRITE_TARGET_REMEDIATION
        )
    if not scan.targets:
        return None
    return _paths_validation_error(list(scan.targets), norm_prefixes, display_prefix)


def _extract_paths_from_patch(command: str) -> list[str]:
    """Extract target file paths from a patch.

    Supports unified diff ('+++ b/') and Codex apply_patch ('*** Update/Add/Delete File:').
    """
    return extract_patch_paths(command)


def _record(data: object, *, activation: str, scope: str, decision: str, reason: str) -> None:
    record_guard_decision(
        data,
        guard="write_guard",
        activation_source=activation,
        scope=scope,
        decision=decision,
        reason=reason,
    )


def _deny(data: object, reason: str, *, reason_code: str, activation: str) -> None:
    _record(
        data,
        activation=activation,
        scope="write_prefix",
        decision="deny",
        reason=reason_code,
    )
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
    )
    sys.exit(0)


def _normalize_prefixes(raw_prefixes: list[str], *, source_label: str) -> list[str]:
    normalized: list[str] = []
    for prefix in raw_prefixes:
        try:
            real = os.path.realpath(prefix)
        except (OSError, ValueError) as exc:
            if prefix not in _WARNED_PREFIXES:
                _LOGGER.warning(
                    "write_guard: dropping prefix %r (realpath failed: %s: %s; source=%s)",
                    prefix,
                    type(exc).__name__,
                    exc,
                    source_label,
                )
                _WARNED_PREFIXES.add(prefix)
            continue
        normalized.append(real.rstrip("/") + "/")
    return normalized


def _load_interactive_binding(data: dict[str, object]) -> _InteractiveBinding | _WritePolicy:
    payload_cwd = data.get("cwd")
    session_id = data.get("session_id")
    if not isinstance(payload_cwd, str) or not os.path.isabs(payload_cwd):
        return _resolution("none", "payload cwd is missing or relative")
    if not isinstance(session_id, str) or not session_id:
        return _resolution("none", "payload session_id is missing")
    read = read_binding_outcome(payload_cwd, session_id)
    match read.outcome:
        case BindingReadOutcome.NO_BINDING:
            return _resolution("none")
        case BindingReadOutcome.WRONG_SESSION:
            return _resolution(
                "unresolved",
                f"the session binding flag for {session_id!r} belongs to another session",
            )
        case BindingReadOutcome.INVALID:
            return _resolution(
                "unresolved",
                f"session binding is invalid ({read.error}); start a new session after "
                "repairing the installation (autoskillit doctor)",
            )
        case BindingReadOutcome.VALID:
            binding = read.binding
            assert binding is not None
            return _InteractiveBinding(payload_cwd, binding.loaded_skills)
        case _ as unreachable:
            assert_never(unreachable)


def _loaded_write_scopes(
    skill_names: list[str],
) -> list[tuple[str, WriteScope]] | _WritePolicy:
    manifest_path = resolve_projection_manifest_path(Path(__file__))
    if manifest_path is None:
        return _resolution(
            "unresolved", "projection manifest not found beside the installed hooks"
        )
    try:
        manifest = read_manifest(manifest_path)
    except SessionBindingError as exc:
        return _resolution("unresolved", f"projection manifest unreadable: {exc}")
    try:
        return [(name, manifest_skill_write_scope(manifest, name)) for name in skill_names]
    except SessionBindingError as exc:
        return _resolution("unresolved", str(exc))


def _temp_root_escape_cause(boundary: SessionWriteBoundary, payload_cwd: str) -> str | None:
    for name, prefixes in boundary.contributors:
        for prefix in prefixes:
            try:
                escape = temp_root_escape(prefix, payload_cwd)
            except (OSError, ValueError):
                # _normalize_prefixes drops (and warns about) a prefix whose realpath fails.
                continue
            if escape is not None:
                return f"declared write scope for skill {name} {escape}"
    return None


def _boundary_policy(boundary: SessionWriteBoundary, payload_cwd: str) -> _WritePolicy:
    match boundary.state:
        case SessionScopeState.NONE:
            return _resolution("none")
        case SessionScopeState.UNRESTRICTED:
            return _WritePolicy("unrestricted", (), ", ".join(boundary.unrestricted_by), "")
        case SessionScopeState.BOUNDED:
            pass
        case _ as unreachable:
            assert_never(unreachable)
    escape = _temp_root_escape_cause(boundary, payload_cwd)
    if escape is not None:
        return _resolution("unresolved", escape)
    normalized = _normalize_prefixes(list(boundary.prefixes), source_label="session_binding")
    if not normalized:
        return _resolution("empty")
    display = (
        "the union of loaded skills' write scopes ("
        + "; ".join(
            f"{name} → {', '.join(prefix.rstrip('/') for prefix in prefixes)}"
            for name, prefixes in boundary.contributors
        )
        + ")"
    )
    return _WritePolicy("active", tuple(normalized), display, "")


def _interactive_prefix_policy(data: dict[str, object]) -> _WritePolicy:
    context = _load_interactive_binding(data)
    if isinstance(context, _WritePolicy):
        return context
    skill_names = list(
        dict.fromkeys(
            entry.skill_name
            for entry in context.loaded_skills
            if entry.origin is LoadedSkillOrigin.AUTOSKILLIT
        )
    )
    if not skill_names:
        return _resolution("none")
    scopes = _loaded_write_scopes(skill_names)
    if isinstance(scopes, _WritePolicy):
        return scopes
    return _boundary_policy(
        fold_session_write_scopes(scopes, context.payload_cwd), context.payload_cwd
    )


def _write_prefix_policy(data: dict[str, object], headless: bool) -> _WritePolicy:
    if not headless:
        return _interactive_prefix_policy(data)
    prefixes_str = os.environ.get("AUTOSKILLIT_ALLOWED_WRITE_PREFIXES", "")
    if prefixes_str:
        raw_prefixes = [p for p in prefixes_str.split(":") if p]
    else:
        singular = os.environ.get("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", "")
        raw_prefixes = [singular] if singular else []

    norm_prefixes = _normalize_prefixes(
        raw_prefixes,
        source_label=(
            "AUTOSKILLIT_ALLOWED_WRITE_PREFIXES"
            if prefixes_str
            else "AUTOSKILLIT_ALLOWED_WRITE_PREFIX"
        ),
    )
    if not norm_prefixes:
        return _resolution("none")
    return _WritePolicy("active", tuple(norm_prefixes), ", ".join(raw_prefixes), "")


def _paths_validation_error(
    paths: list[str], norm_prefixes: list[str], display_prefix: str
) -> str | None:
    for path in paths:
        resolved = os.path.realpath(path)
        if not any(resolved.startswith(np) or resolved == np.rstrip("/") for np in norm_prefixes):
            return (
                f"Write/Edit/apply_patch blocked: {WRITE_GUARD_DENY_TRIGGER}. "
                f"Only writes to {display_prefix} are permitted."
            )
    return None


def _direct_path_validation_error(
    file_path: str, norm_prefixes: list[str], display_prefix: str
) -> str | None:
    if not file_path:
        return f"Write/Edit/apply_patch blocked: {WRITE_GUARD_DENY_TRIGGER} (no file_path)."
    return _paths_validation_error([file_path], norm_prefixes, display_prefix)


def _patch_validation_error(
    command: str, norm_prefixes: list[str], display_prefix: str
) -> str | None:
    paths = _extract_paths_from_patch(command)
    if not paths:
        return (
            f"Write/Edit/apply_patch blocked: {WRITE_GUARD_DENY_TRIGGER} "
            f"(no target paths found in patch)."
        )
    return _paths_validation_error(paths, norm_prefixes, display_prefix)


def _interpreter_validation_error(
    command: str, execution_cwd: str, norm_prefixes: list[str], display_prefix: str
) -> str | None:
    interp_paths = extract_interpreter_write_paths(command)
    if interp_paths is None:
        return None
    unresolved_reason = (
        f"Write/Edit/apply_patch blocked: {WRITE_GUARD_DENY_TRIGGER}. "
        f"Interpreter-mediated file writes are not permitted."
    )
    if not interp_paths:
        return unresolved_reason
    cwd = _effective_execution_cwd(execution_cwd)
    resolved: list[str] = []
    for path in interp_paths:
        if os.path.isabs(path):
            resolved.append(path)
        elif cwd:
            resolved.append(os.path.join(cwd, path))
        else:
            return unresolved_reason
    return _paths_validation_error(resolved, norm_prefixes, display_prefix)


def _tool_validation_error(
    tool_name: str,
    data: dict[str, object],
    norm_prefixes: list[str],
    display_prefix: str,
) -> str | None:
    raw_tool_input = data.get("tool_input")
    tool_input: dict[str, object] = raw_tool_input if isinstance(raw_tool_input, dict) else {}

    if tool_name == "Bash" or "run_cmd" in tool_name:
        parsed = parse_hook_command(data)
        command = parsed.command or ""
        reason = _interpreter_validation_error(
            command, parsed.execution_cwd, norm_prefixes, display_prefix
        )
        if reason is not None:
            return reason
        return _bash_validation_error(command, parsed.execution_cwd, norm_prefixes, display_prefix)

    if tool_name == "apply_patch":
        command = extract_apply_patch_text(data) or ""
        return _patch_validation_error(command, norm_prefixes, display_prefix)

    raw_file_path = tool_input.get("file_path", "")
    file_path = raw_file_path if isinstance(raw_file_path, str) else ""
    return _direct_path_validation_error(file_path, norm_prefixes, display_prefix)


def _settle_inactive_policy(
    data: dict[str, object], policy: _WritePolicy, activation: str
) -> bool:
    """Record and settle every policy state except ``active``; return whether it settled."""
    match policy.state:
        case "none":
            _record(data, activation=activation, scope="none", decision="allow", reason="no_scope")
            sys.exit(0)
        case "unrestricted":
            _record(
                data,
                activation=activation,
                scope="none",
                decision="allow",
                reason="unrestricted_skill",
            )
            sys.exit(0)
        case "empty":
            _deny(
                data,
                f"Write/Edit/apply_patch blocked: {WRITE_GUARD_DENY_TRIGGER} "
                f"(empty boundary: {_EMPTY_BOUNDARY_HINT_BY_ACTIVATION[activation]}).",
                reason_code="empty",
                activation=activation,
            )
            return True
        case "unresolved":
            _deny(
                data,
                f"Write/Edit/apply_patch blocked: {WRITE_GUARD_DENY_TRIGGER} "
                f"(unresolved boundary: {policy.cause}).",
                reason_code="unresolved",
                activation=activation,
            )
            return True
        case "active":
            return False
        case _ as unreachable:
            assert_never(unreachable)


def main() -> None:
    enforce_session_scope("any")
    try:
        data: object = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        data = None

    if os.environ.get("AUTOSKILLIT_AGENT_BACKEND") == "codex":
        _record(data, activation="backend", scope="workspace", decision="allow", reason="codex")
        sys.exit(0)

    # Resolve the runtime shape once and pass it to helper paths so each guard
    # invocation makes a single hook_session_shape() call instead of three.
    headless, _ = hook_session_shape()

    if not isinstance(data, dict):
        if (
            headless
            and not os.environ.get("AUTOSKILLIT_ALLOWED_WRITE_PREFIXES")
            and not os.environ.get("AUTOSKILLIT_ALLOWED_WRITE_PREFIX")
        ):
            _record(data, activation="headless", scope="none", decision="allow", reason="no_scope")
            sys.exit(0)
        _deny(
            data,
            f"Write/Edit/apply_patch blocked: {WRITE_GUARD_DENY_TRIGGER} (malformed hook input).",
            reason_code="malformed_input",
            activation="headless",
        )
        return

    policy = _write_prefix_policy(data, headless)
    activation = "headless" if headless else "skill_binding"
    if _settle_inactive_policy(data, policy, activation):
        return
    norm_prefixes = list(policy.prefixes)
    display_prefix = policy.display

    tool_name = data.get("tool_name", "")

    raw_tool_names = os.environ.get("AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES", "")
    parsed_names = [t.strip() for t in raw_tool_names.split(",") if t.strip()]
    # Default must match CLAUDE_CODE_CAPABILITIES.write_guard_tool_names
    # in core/types/execution/_type_backend.py
    effective_tool_names: frozenset[str] = (
        frozenset(parsed_names)
        if parsed_names
        else frozenset({"Write", "Edit", "Bash", "apply_patch"})
    )

    if not isinstance(tool_name, str) or (
        tool_name not in effective_tool_names and "run_cmd" not in tool_name
    ):
        _record(
            data,
            activation=activation,
            scope="write_prefix",
            decision="allow",
            reason="tool_exempt",
        )
        sys.exit(0)

    reason = _tool_validation_error(tool_name, data, norm_prefixes, display_prefix)
    if reason is not None:
        _deny(data, reason, reason_code="scope_violation", activation=activation)
        return
    _record(data, activation=activation, scope="write_prefix", decision="allow", reason="in_scope")
    sys.exit(0)


if __name__ == "__main__":
    main()
