"""PreToolUse write boundary for headless launcher and interactive skill sessions.

Headless sessions use launcher supplied prefixes. Interactive Claude sessions use
the intersection of loaded skills' projected write_paths. Codex relies on its
workspace sandbox for this boundary; installation writes have a separate guard.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

_HOOKS_DIR = str(Path(__file__).resolve().parent.parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)
_RUNTIME_DIR = str(Path(_HOOKS_DIR) / "_runtime")
if _RUNTIME_DIR not in sys.path:
    sys.path.insert(0, _RUNTIME_DIR)


from _command_classification import (  # type: ignore[import-not-found]  # noqa: E402
    UNRESOLVED_WRITE_TARGET_REMEDIATION,
    WriteTargetScan,
    extract_interpreter_write_paths,
    extract_patch_paths,
    scan_write_targets,
)
from _guard_decision_diagnostics import (  # noqa: E402
    record_guard_decision,
)
from _hook_payload import (  # type: ignore[import-not-found]  # noqa: E402
    TEMP_RELATIVE_DIR,
    extract_apply_patch_text,
    parse_hook_command,
)
from _hook_settings import (  # noqa: E402
    enforce_session_scope,
    hook_session_shape,
)
from _session_binding import (  # type: ignore[import-not-found]  # noqa: E402
    SessionBindingError,
    read_manifest,
    read_session_binding,
    resolve_projection_manifest_path,
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
    if scan.unresolved:
        return (
            f"Write/Edit/apply_patch blocked: {WRITE_GUARD_DENY_TRIGGER} "
            "(unresolved write target). " + UNRESOLVED_WRITE_TARGET_REMEDIATION
        )
    if not scan.parseable or not scan.targets:
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


def _narrow_compatible_prefixes(left: list[str], right: list[str]) -> list[str]:
    result: list[str] = []
    for left_prefix in left:
        for right_prefix in right:
            if left_prefix.startswith(right_prefix):
                result.append(left_prefix)
            elif right_prefix.startswith(left_prefix):
                result.append(right_prefix)
    return list(dict.fromkeys(result))


def _load_interactive_policy_context(
    data: dict[str, object],
) -> tuple[tuple[str, list[object], dict[str, object]] | None, str]:
    payload_cwd = data.get("cwd")
    session_id = data.get("session_id")
    if not isinstance(payload_cwd, str) or not os.path.isabs(payload_cwd):
        return None, "none"
    if not isinstance(session_id, str) or not session_id:
        return None, "none"
    binding = read_session_binding(payload_cwd, session_id)
    if binding is None:
        return None, "none"
    loaded_skills = binding.get("loaded_skills")
    if not isinstance(loaded_skills, list):
        return None, "unresolved"
    manifest_path = resolve_projection_manifest_path(Path(__file__))
    if manifest_path is None:
        return None, "unresolved"
    try:
        manifest = read_manifest(manifest_path)
    except SessionBindingError:
        return None, "unresolved"
    skills = manifest.get("skills")
    if not isinstance(skills, dict):
        return None, "unresolved"
    return (payload_cwd, loaded_skills, skills), ""


def _loaded_skill_prefixes(
    loaded: object, skills: dict[str, object], payload_cwd: str
) -> tuple[list[str] | None, bool]:
    if not isinstance(loaded, dict) or not isinstance(loaded.get("skill_name"), str):
        return None, True
    if loaded.get("binding_valid") is not True:
        return None, True
    entry = skills.get(loaded["skill_name"])
    if not isinstance(entry, dict) or "write_paths" not in entry:
        return None, True
    raw_paths = entry["write_paths"]
    if raw_paths is None:
        return None, False
    if not isinstance(raw_paths, list) or any(not isinstance(path, str) for path in raw_paths):
        return None, True
    paths = [
        (
            os.path.join(payload_cwd, path)
            if path.startswith(f"{TEMP_RELATIVE_DIR}/")
            else path.replace("{{AUTOSKILLIT_TEMP}}", f"{payload_cwd}/{TEMP_RELATIVE_DIR}")
        )
        for path in raw_paths
    ]
    return _normalize_prefixes(paths, source_label="session_binding"), False


def _interactive_prefix_policy(data: dict[str, object]) -> tuple[list[str], str, str]:
    context, state = _load_interactive_policy_context(data)
    if context is None:
        return [], "", state
    payload_cwd, loaded_skills, skills = context

    effective: list[str] | None = None
    for loaded in loaded_skills:
        prefixes, unresolved = _loaded_skill_prefixes(loaded, skills, payload_cwd)
        if unresolved:
            return [], "", "unresolved"
        if prefixes is None:
            continue
        effective = (
            prefixes if effective is None else _narrow_compatible_prefixes(effective, prefixes)
        )

    if effective is None:
        return [], "", "none"
    return (
        effective,
        ", ".join(prefix.rstrip("/") for prefix in effective),
        ("active" if effective else "empty"),
    )


def _write_prefix_policy(data: dict[str, object], headless: bool) -> tuple[list[str], str, str]:
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
    return norm_prefixes, ", ".join(raw_prefixes), ("active" if norm_prefixes else "none")


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

    norm_prefixes, display_prefix, policy_state = _write_prefix_policy(data, headless)
    activation = "headless" if headless else "skill_binding"
    if policy_state == "none":
        _record(data, activation=activation, scope="none", decision="allow", reason="no_scope")
        sys.exit(0)
    if policy_state in {"empty", "unresolved"}:
        hint = _EMPTY_BOUNDARY_HINT_BY_ACTIVATION.get(activation)
        suffix = (
            f"empty boundary: {hint}"
            if hint is not None
            else f"{policy_state} boundary (activation={activation})"
        )
        _deny(
            data,
            (f"Write/Edit/apply_patch blocked: {WRITE_GUARD_DENY_TRIGGER} ({suffix})."),
            reason_code=policy_state,
            activation=activation,
        )
        return

    tool_name = data.get("tool_name", "")

    raw_tool_names = os.environ.get("AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES", "")
    parsed_names = [t.strip() for t in raw_tool_names.split(",") if t.strip()]
    # Default must match CLAUDE_CODE_CAPABILITIES.write_guard_tool_names
    # in core/types/_type_backend.py
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
