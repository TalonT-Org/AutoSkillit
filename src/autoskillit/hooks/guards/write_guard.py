"""PreToolUse write boundary for headless launcher and interactive skill sessions.

Headless sessions use launcher supplied prefixes. Interactive Claude sessions use
the intersection of loaded skills' projected write_paths. Codex relies on its
workspace sandbox for this boundary; installation writes have a separate guard.
"""

from __future__ import annotations

import json
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
    _GIT_GLOBAL_FLAG_SPEC,
    WRITE_VERBS,
    _FlagArity,
    all_evaluated_segments,
    command_verb,
    extract_interpreter_write_paths,
    extract_patch_paths,
    extract_redirect_targets,
    extract_write_verb_targets,
    is_gh_command,
    resolve_write_target,
    updated_execution_cwd,
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
    is_headless_session,
    read_session_binding,
)
from _session_binding import (  # noqa: E402
    SessionBindingError,
    read_manifest,
    resolve_projection_manifest_path,
)

WRITE_GUARD_DENY_TRIGGER = "read-only skill session"

_PSEUDO_DEVICE_PATHS: frozenset[str] = frozenset(
    {
        "/dev/null",
        "/dev/zero",
        "/dev/stdout",
        "/dev/stderr",
        "/dev/stdin",
    }
)

# Every value-taking git global flag, derived at import time from
# _GIT_GLOBAL_FLAG_SPEC in _command_classification.py. Hook scripts already
# import from that module via sys.path (same as git_ops_guard.py in this
# same package) so there is no separate "manual mirror" -- the spec table
# is the single source of truth and any future addition automatically
# extends this frozenset. A flag missing from this set is misread by the
# loop below as a 1-token boolean skip, so its value gets mistaken for
# the git subcommand -- e.g. `git --namespace refs/foo checkout -- file`
# previously stopped the loop at `refs/foo`, never reaching `checkout`.
_GIT_FLAG_WITH_VALUE: frozenset[str] = frozenset(
    flag for flag, arity in _GIT_GLOBAL_FLAG_SPEC.items() if arity == _FlagArity.VALUE
)


def _resolve_real_targets(operands: list[str], cwd: str) -> list[str]:
    targets: list[str] = []
    for operand in operands:
        resolved = resolve_write_target(operand, cwd)
        if resolved is not None and resolved not in _PSEUDO_DEVICE_PATHS:
            targets.append(resolved)
    return targets


def _git_subcommand_index(segment: list[str]) -> int:
    idx = 1
    while idx < len(segment):
        tok = segment[idx]
        if tok in _GIT_FLAG_WITH_VALUE:
            idx += 2
            if idx >= len(segment):
                break
        elif tok.startswith("-") and "=" not in tok and tok not in ("--", "--hard"):
            idx += 1
        else:
            break
    return idx


def _extract_git_write_targets(segment: list[str], cwd: str) -> list[str] | None:
    idx = _git_subcommand_index(segment)
    if idx >= len(segment):
        return None
    subcmd = segment[idx]
    if subcmd == "checkout" and "--" in segment[idx + 1 :]:
        double_dash = segment.index("--", idx + 1)
        return _resolve_real_targets(segment[double_dash + 1 :], cwd)
    if subcmd == "reset" and "--hard" in segment[idx + 1 :]:
        return []
    return None


_UNRESOLVED_TARGET: list[str] = []
"""Sentinel returned by ``_extract_segment_targets`` when a write verb's
target contains an unresolvable operand (e.g. shell variable). Distinct
from a None return (non-write) and a real ``[]`` (write with no path)."""


def _extract_segment_targets(segment: list[str], cwd: str) -> list[str] | None:
    """Return None for non-writes, [] for writes without real paths, or target paths."""
    if is_gh_command(segment):
        return None
    verb = command_verb(segment)
    if verb == "git" and len(segment) >= 2:
        return _extract_git_write_targets(segment, cwd)
    if verb in WRITE_VERBS:
        targets, unresolved_target = extract_write_verb_targets(verb, segment, cwd)
        if unresolved_target:
            return _UNRESOLVED_TARGET
        return [target for target in targets if target not in _PSEUDO_DEVICE_PATHS]
    return None


def _effective_execution_cwd(execution_cwd: str) -> str:
    if execution_cwd:
        return execution_cwd
    cwd = os.environ.get("AUTOSKILLIT_CWD", "")
    if cwd and not os.path.isabs(cwd):
        return ""
    return cwd


def _extract_bash_write_targets(command: str, execution_cwd: str = "") -> list[str] | None:
    """Return absolute target paths from a bash command, None if no write, or
    ``_UNRESOLVED_TARGET`` if a write verb's target could not be statically
    resolved (fail-closed: callers must deny).

    ``execution_cwd`` (the run_cmd tool's own cwd argument, or Bash's session
    cwd) is preferred for resolving relative targets when non-empty; falls
    back to the ``AUTOSKILLIT_CWD`` env var otherwise.

    Reads *command* through `all_evaluated_segments` (rectify #4941 Part B):
    outer segments, every recursively tokenized SHELL payload (a heredoc,
    herestring, pipe, or `bash -c`/`eval` body), and every literal-argv or
    executing-string Python subprocess spec are all classified and scanned
    for redirects independently, per evaluated segment, rather than
    flattening the whole command into one private token stream -- so
    `bash <<'EOF'\nrm -rf src/\nEOF` and `bash -c 'echo x > /outside/f'` are
    seen the same way a direct invocation is. `None` (unparseable) returns
    `None`, the guard's documented authority-failure result -- no private
    raw-command fallback.

    Returns an empty list when a write command is detected but no path can be reliably
    extracted — callers treat this as fail-open (ambiguous = allow).
    """
    segments = all_evaluated_segments(command)
    if segments is None:
        return None

    cwd = _effective_execution_cwd(execution_cwd)

    all_targets: list[str] = []
    found_any_write = False

    for segment in segments:
        if command_verb(segment) == "cd":
            cwd = updated_execution_cwd(segment, cwd)
            continue
        result = _extract_segment_targets(segment, cwd)
        if result is _UNRESOLVED_TARGET:
            return _UNRESOLVED_TARGET
        if result is not None:
            found_any_write = True
            all_targets.extend(result)

        redirect_paths = extract_redirect_targets(segment, cwd)
        for path in redirect_paths:
            found_any_write = True
            if path not in _PSEUDO_DEVICE_PATHS:
                all_targets.append(path)

    if not found_any_write:
        return None

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for t in all_targets:
        if t not in seen:
            seen.add(t)
            unique.append(t)
    return unique


def _bash_validation_error(
    command: str, execution_cwd: str, norm_prefixes: list[str], display_prefix: str
) -> str | None:
    targets = _extract_bash_write_targets(command, execution_cwd)
    if targets is _UNRESOLVED_TARGET:
        # Fail-closed: a write verb whose target contains an unresolvable
        # operand could be a shell-local indirection into a protected path.
        return (
            f"Write/Edit/apply_patch blocked: {WRITE_GUARD_DENY_TRIGGER} "
            "(unresolved write target)."
        )
    if not targets:
        return None
    return _paths_validation_error(targets, norm_prefixes, display_prefix)


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


def _deny(data: object, reason: str, *, reason_code: str) -> None:
    _record(
        data,
        activation="headless",
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


def _normalize_prefixes(raw_prefixes: list[str]) -> list[str]:
    normalized: list[str] = []
    for prefix in raw_prefixes:
        try:
            real = os.path.realpath(prefix)
        except OSError:
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


def _interactive_prefix_policy(data: dict[str, object]) -> tuple[list[str], str, str]:
    payload_cwd = data.get("cwd")
    session_id = data.get("session_id")
    if not isinstance(payload_cwd, str) or not os.path.isabs(payload_cwd):
        return [], "", "none"
    if not isinstance(session_id, str) or not session_id:
        return [], "", "none"
    binding = read_session_binding(payload_cwd, session_id)
    if binding is None:
        return [], "", "none"
    loaded_skills = binding.get("loaded_skills")
    if not isinstance(loaded_skills, list):
        return [], "", "unresolved"
    manifest_path = resolve_projection_manifest_path(Path(__file__))
    if manifest_path is None:
        return [], "", "unresolved"
    try:
        manifest = read_manifest(manifest_path)
    except SessionBindingError:
        return [], "", "unresolved"
    skills = manifest.get("skills")
    if not isinstance(skills, dict):
        return [], "", "unresolved"

    effective: list[str] | None = None
    for loaded in loaded_skills:
        if not isinstance(loaded, dict) or not isinstance(loaded.get("skill_name"), str):
            return [], "", "unresolved"
        if loaded.get("binding_valid") is not True:
            return [], "", "unresolved"
        entry = skills.get(loaded["skill_name"])
        if not isinstance(entry, dict):
            return [], "", "unresolved"
        if "write_paths" not in entry:
            return [], "", "unresolved"
        raw_paths = entry["write_paths"]
        if raw_paths is None:
            continue
        if not isinstance(raw_paths, list) or any(not isinstance(path, str) for path in raw_paths):
            return [], "", "unresolved"
        paths = [
            (
                os.path.join(payload_cwd, path)
                if path.startswith(f"{TEMP_RELATIVE_DIR}/")
                else path.replace("{{AUTOSKILLIT_TEMP}}", f"{payload_cwd}/{TEMP_RELATIVE_DIR}")
            )
            for path in raw_paths
        ]
        prefixes = _normalize_prefixes(paths)
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


def _write_prefix_policy(data: dict[str, object]) -> tuple[list[str], str, str]:
    if not is_headless_session():
        return _interactive_prefix_policy(data)
    prefixes_str = os.environ.get("AUTOSKILLIT_ALLOWED_WRITE_PREFIXES", "")
    if prefixes_str:
        raw_prefixes = [p for p in prefixes_str.split(":") if p]
    else:
        singular = os.environ.get("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", "")
        raw_prefixes = [singular] if singular else []

    norm_prefixes = _normalize_prefixes(raw_prefixes)
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


def main() -> None:
    try:
        data: object = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        data = None

    if not enforce_session_scope("guards/write_guard.py"):
        sys.exit(0)

    if os.environ.get("AUTOSKILLIT_AGENT_BACKEND") == "codex":
        _record(data, activation="backend", scope="workspace", decision="allow", reason="codex")
        sys.exit(0)

    if not isinstance(data, dict):
        if (
            is_headless_session()
            and not os.environ.get("AUTOSKILLIT_ALLOWED_WRITE_PREFIXES")
            and not os.environ.get("AUTOSKILLIT_ALLOWED_WRITE_PREFIX")
        ):
            _record(data, activation="headless", scope="none", decision="allow", reason="no_scope")
            sys.exit(0)
        _deny(
            data,
            f"Write/Edit/apply_patch blocked: {WRITE_GUARD_DENY_TRIGGER} (malformed hook input).",
            reason_code="malformed_input",
        )
        return

    norm_prefixes, display_prefix, policy_state = _write_prefix_policy(data)
    activation = "headless" if is_headless_session() else "skill_binding"
    if policy_state == "none":
        _record(data, activation=activation, scope="none", decision="allow", reason="no_scope")
        sys.exit(0)
    if policy_state in {"empty", "unresolved"}:
        _deny(
            data,
            (
                f"Write/Edit/apply_patch blocked: {WRITE_GUARD_DENY_TRIGGER} "
                f"({policy_state} boundary)."
            ),
            reason_code=policy_state,
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

    if tool_name not in effective_tool_names and "run_cmd" not in tool_name:
        _record(
            data,
            activation=activation,
            scope="write_prefix",
            decision="allow",
            reason="tool_exempt",
        )
        sys.exit(0)

    tool_input = data.get("tool_input", {})

    if tool_name == "Bash" or "run_cmd" in tool_name:
        parsed = parse_hook_command(data)
        command = parsed.command or ""
        reason = _interpreter_validation_error(
            command, parsed.execution_cwd, norm_prefixes, display_prefix
        )
        if reason is not None:
            _deny(data, reason, reason_code="scope_violation")
            return
        reason = _bash_validation_error(
            command, parsed.execution_cwd, norm_prefixes, display_prefix
        )
        if reason is not None:
            _deny(data, reason, reason_code="scope_violation")
            return
        _record(
            data, activation=activation, scope="write_prefix", decision="allow", reason="in_scope"
        )
        sys.exit(0)

    if tool_name == "apply_patch":
        command = extract_apply_patch_text(data) or ""
        reason = _patch_validation_error(command, norm_prefixes, display_prefix)
        if reason is not None:
            _deny(data, reason, reason_code="scope_violation")
            return
        _record(
            data, activation=activation, scope="write_prefix", decision="allow", reason="in_scope"
        )
        sys.exit(0)

    # Write or Edit
    file_path = tool_input.get("file_path", "")
    reason = _direct_path_validation_error(file_path, norm_prefixes, display_prefix)
    if reason is not None:
        _deny(data, reason, reason_code="scope_violation")
        return
    _record(data, activation=activation, scope="write_prefix", decision="allow", reason="in_scope")
    sys.exit(0)


if __name__ == "__main__":
    main()
