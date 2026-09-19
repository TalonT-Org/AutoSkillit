"""PreToolUse hook: blocks tool calls outside the allowed prefix
in write-scoped sessions.

Two gate mechanisms operate together:
1. Named-tool gate: controlled by AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES (default:
   Write, Edit, Bash, apply_patch). Tool calls not in this set pass through
   immediately with no prefix check.
2. run_cmd bypass: any tool whose name contains the substring "run_cmd" is
   unconditionally routed into the Bash command analysis path regardless of
   AUTOSKILLIT_WRITE_GUARD_TOOL_NAMES. This ensures Codex's run_cmd tool is
   always subject to command-level write checks. The env var cannot suppress
   or extend this bypass.

Bypass conditions:
- AUTOSKILLIT_HEADLESS not set: non-headless session, exit 0.
- AUTOSKILLIT_AGENT_BACKEND == 'codex': codex enforces writes via
  workspace-write sandbox + post-hoc file_changes detection (hard
  enforcement), making PreToolUse deny (soft) redundant. Exit 0.
- No allowed-write prefixes configured: exit 0.

Enforcement strength by backend:
  claude_code  — PreToolUse deny (soft, best-effort)
  codex        — workspace-write sandbox + post-hoc file_changes (hard)
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
    _FD_REDIRECT_RE,
    _GIT_GLOBAL_FLAG_SPEC,
    _REDIRECT_OP_ONLY_RE,
    _REDIRECT_TOKEN_RE,
    _FlagArity,
    all_evaluated_segments,
    command_verb,
    extract_interpreter_write_paths,
    extract_redirect_targets,
    is_gh_command,
    resolve_write_target,
)
from _hook_payload import (  # type: ignore[import-not-found]  # noqa: E402
    extract_apply_patch_text,
    parse_hook_command,
)
from _hook_settings import enforce_session_scope  # noqa: E402

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

_WRITE_VERBS: frozenset[str] = frozenset(
    {
        "sed",
        "tee",
        "mv",
        "cp",
        "patch",
        "rm",
        "unlink",
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


def _non_flag_operands(args: list[str]) -> list[str]:
    operands: list[str] = []
    skip_next = False
    for token in args:
        if skip_next:
            skip_next = False
            continue
        if token.startswith("-") or token.startswith("&") or _FD_REDIRECT_RE.match(token):
            continue
        if _REDIRECT_OP_ONLY_RE.match(token):
            skip_next = True
            continue
        if _REDIRECT_TOKEN_RE.match(token):
            continue
        operands.append(token)
    return operands


def _extract_write_verb_targets(verb: str, segment: list[str], cwd: str) -> list[str]:
    operands = _non_flag_operands(segment[1:])
    if verb == "sed":
        has_inplace = any(t.startswith("-i") or t == "--in-place" for t in segment[1:])
        if not has_inplace:
            return []
        operands = operands[-1:]
    elif verb in ("mv", "cp"):
        if len(operands) < 2:
            return []
        operands = operands[-1:]
    elif verb == "patch":
        for operand in operands:
            resolved = resolve_write_target(operand, cwd)
            if resolved is not None:
                # A pseudo-device still consumes patch's first resolvable operand.
                if resolved in _PSEUDO_DEVICE_PATHS:
                    return []
                return [resolved]
        return []
    return _resolve_real_targets(operands, cwd)


def _extract_segment_targets(segment: list[str], cwd: str) -> list[str] | None:
    """Return None for non-writes, [] for writes without real paths, or target paths."""
    if is_gh_command(segment):
        return None
    verb = command_verb(segment)
    if verb == "git" and len(segment) >= 2:
        return _extract_git_write_targets(segment, cwd)
    if verb in _WRITE_VERBS:
        return _extract_write_verb_targets(verb, segment, cwd)
    return None


def _effective_execution_cwd(execution_cwd: str) -> str:
    if execution_cwd:
        return execution_cwd
    cwd = os.environ.get("AUTOSKILLIT_CWD", "")
    if cwd and not os.path.isabs(cwd):
        return ""
    return cwd


def _extract_bash_write_targets(command: str, execution_cwd: str = "") -> list[str] | None:
    """Return absolute target paths from a bash command, or None if no write command found.

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
        result = _extract_segment_targets(segment, cwd)
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


_CODEX_FILE_MARKERS = ("*** Update File: ", "*** Add File: ", "*** Delete File: ")


def _extract_paths_from_patch(command: str) -> list[str]:
    """Extract target file paths from a patch.

    Supports unified diff ('+++ b/') and Codex apply_patch ('*** Update/Add/Delete File:').
    """
    if not command:
        return []
    paths: list[str] = []
    for line in command.split("\n"):
        if line.startswith("+++ b/"):
            paths.append(line[6:])
        else:
            for marker in _CODEX_FILE_MARKERS:
                if line.startswith(marker):
                    paths.append(line[len(marker) :].strip())
                    break
    return paths


def _deny(reason: str) -> None:
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


def _write_prefix_policy() -> tuple[list[str], str]:
    prefixes_str = os.environ.get("AUTOSKILLIT_ALLOWED_WRITE_PREFIXES", "")
    if prefixes_str:
        raw_prefixes = [p for p in prefixes_str.split(":") if p]
    else:
        singular = os.environ.get("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", "")
        raw_prefixes = [singular] if singular else []

    norm_prefixes = [os.path.realpath(p).rstrip("/") + "/" for p in raw_prefixes]
    return norm_prefixes, ", ".join(raw_prefixes)


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


def _bash_validation_error(
    command: str, execution_cwd: str, norm_prefixes: list[str], display_prefix: str
) -> str | None:
    targets = _extract_bash_write_targets(command, execution_cwd)
    if not targets:
        return None
    return _paths_validation_error(targets, norm_prefixes, display_prefix)


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
    enforce_session_scope("headless_only")

    if os.environ.get("AUTOSKILLIT_AGENT_BACKEND") == "codex":
        sys.exit(0)

    norm_prefixes, display_prefix = _write_prefix_policy()
    if not norm_prefixes:
        sys.exit(0)

    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        _deny(
            f"Write/Edit/apply_patch blocked: {WRITE_GUARD_DENY_TRIGGER} (malformed hook input)."
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
        sys.exit(0)

    tool_input = data.get("tool_input", {})

    if tool_name == "Bash" or "run_cmd" in tool_name:
        parsed = parse_hook_command(data)
        command = parsed.command or ""
        reason = _interpreter_validation_error(
            command, parsed.execution_cwd, norm_prefixes, display_prefix
        )
        if reason is not None:
            _deny(reason)
            return
        reason = _bash_validation_error(
            command, parsed.execution_cwd, norm_prefixes, display_prefix
        )
        if reason is not None:
            _deny(reason)
            return
        sys.exit(0)

    if tool_name == "apply_patch":
        command = extract_apply_patch_text(data) or ""
        reason = _patch_validation_error(command, norm_prefixes, display_prefix)
        if reason is not None:
            _deny(reason)
            return
        sys.exit(0)

    # Write or Edit
    file_path = tool_input.get("file_path", "")
    reason = _direct_path_validation_error(file_path, norm_prefixes, display_prefix)
    if reason is not None:
        _deny(reason)
        return
    sys.exit(0)


if __name__ == "__main__":
    main()
