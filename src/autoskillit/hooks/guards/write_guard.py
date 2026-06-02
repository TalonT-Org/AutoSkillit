"""PreToolUse hook: blocks Write/Edit/Bash/apply_patch outside
the allowed prefix in write-scoped sessions."""

from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path

_HOOKS_DIR = str(Path(__file__).resolve().parent.parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from _command_classification import (  # type: ignore[import-not-found]  # noqa: E402
    _FD_REDIRECT_RE,
    _REDIRECT_OP_ONLY_RE,
    _REDIRECT_TOKEN_RE,
    command_verb,
    extract_interpreter_write_paths,
    extract_redirect_targets,
    is_gh_command,
    resolve_write_target,
    tokenize_command_segments,
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

_GIT_FLAG_WITH_VALUE: frozenset[str] = frozenset({"-C", "--git-dir", "--work-tree", "-c"})


def _extract_segment_targets(segment: list[str], cwd: str) -> list[str] | None:
    """Extract write target paths from a single command segment.

    Returns None if segment is not a write command, [] if write detected
    but all targets are pseudo-devices, or [paths] otherwise.
    """
    if is_gh_command(segment):
        return None

    verb = command_verb(segment)
    targets: list[str] = []
    found_write = False

    if verb == "git" and len(segment) >= 2:
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
        if idx < len(segment):
            subcmd = segment[idx]
            if subcmd == "checkout" and "--" in segment[idx + 1 :]:
                found_write = True
                double_dash = segment.index("--", idx + 1)
                for t in segment[double_dash + 1 :]:
                    resolved = resolve_write_target(t, cwd)
                    if resolved is not None and resolved not in _PSEUDO_DEVICE_PATHS:
                        targets.append(resolved)
            elif subcmd == "reset" and "--hard" in segment[idx + 1 :]:
                found_write = True
    elif verb in _WRITE_VERBS:
        found_write = True
        non_flag: list[str] = []
        skip_next = False
        for t in segment[1:]:
            if skip_next:
                skip_next = False
                continue
            if t.startswith("-") or t.startswith("&") or _FD_REDIRECT_RE.match(t):
                continue
            if _REDIRECT_OP_ONLY_RE.match(t):
                skip_next = True
                continue
            if _REDIRECT_TOKEN_RE.match(t):
                continue
            non_flag.append(t)
        if verb == "sed":
            # -i flag must be present; last non-flag arg is the target
            flags = [t for t in segment[1:] if t.startswith("-")]
            has_inplace = any(t.startswith("-i") or t == "--in-place" for t in flags)
            if has_inplace and non_flag:
                path = non_flag[-1]
                resolved = resolve_write_target(path, cwd)
                if resolved is not None and resolved not in _PSEUDO_DEVICE_PATHS:
                    targets.append(resolved)
        elif verb == "tee":
            for t in non_flag:
                resolved = resolve_write_target(t, cwd)
                if resolved is not None and resolved not in _PSEUDO_DEVICE_PATHS:
                    targets.append(resolved)
        elif verb in ("mv", "cp"):
            if len(non_flag) >= 2:
                path = non_flag[-1]
                resolved = resolve_write_target(path, cwd)
                if resolved is not None and resolved not in _PSEUDO_DEVICE_PATHS:
                    targets.append(resolved)
        elif verb == "patch":
            for t in non_flag:
                resolved = resolve_write_target(t, cwd)
                if resolved is not None:
                    if resolved not in _PSEUDO_DEVICE_PATHS:
                        targets.append(resolved)
                    break
        elif verb in ("rm", "unlink"):
            for t in non_flag:
                resolved = resolve_write_target(t, cwd)
                if resolved is not None and resolved not in _PSEUDO_DEVICE_PATHS:
                    targets.append(resolved)

    if found_write:
        return targets
    return None


def _extract_bash_write_targets(command: str) -> list[str] | None:
    """Return absolute target paths from a bash command, or None if no write command found.

    Returns an empty list when a write command is detected but no path can be reliably
    extracted — callers treat this as fail-open (ambiguous = allow).
    """
    segments = tokenize_command_segments(command)
    cwd = os.environ.get("AUTOSKILLIT_CWD", "")
    if cwd and not os.path.isabs(cwd):
        cwd = ""

    all_targets: list[str] = []
    found_any_write = False

    for segment in segments:
        result = _extract_segment_targets(segment, cwd)
        if result is not None:
            found_any_write = True
            all_targets.extend(result)

    try:
        flat_tokens = shlex.split(command)
    except (ValueError, TypeError, AttributeError):
        flat_tokens = []
    redirect_paths = extract_redirect_targets(flat_tokens, cwd)
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


def _extract_paths_from_patch(command: str) -> list[str]:
    """Extract target file paths from a unified diff patch ('+++ b/' lines)."""
    if not command:
        return []
    paths: list[str] = []
    for line in command.split("\n"):
        if line.startswith("+++ b/"):
            paths.append(line[6:])
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


def main() -> None:
    if not os.environ.get("AUTOSKILLIT_HEADLESS"):
        sys.exit(0)

    prefixes_str = os.environ.get("AUTOSKILLIT_ALLOWED_WRITE_PREFIXES", "")
    if prefixes_str:
        raw_prefixes = [p for p in prefixes_str.split(":") if p]
    else:
        singular = os.environ.get("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", "")
        raw_prefixes = [singular] if singular else []

    if not raw_prefixes:
        sys.exit(0)

    norm_prefixes = [os.path.realpath(p).rstrip("/") + "/" for p in raw_prefixes]
    display_prefix = ", ".join(raw_prefixes)

    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, ValueError, OSError):
        _deny(
            f"Write/Edit/apply_patch blocked: {WRITE_GUARD_DENY_TRIGGER} (malformed hook input)."
        )
        return

    tool_name = data.get("tool_name", "")
    if tool_name not in ("Write", "Edit", "Bash", "apply_patch") and "run_cmd" not in tool_name:
        sys.exit(0)

    tool_input = data.get("tool_input", {})

    def _within_any_prefix(path: str) -> bool:
        resolved = os.path.realpath(path)
        return any(resolved.startswith(np) or resolved == np.rstrip("/") for np in norm_prefixes)

    if tool_name == "Bash" or "run_cmd" in tool_name:
        command = tool_input.get("command", "") or tool_input.get("cmd", "")

        interp_paths = extract_interpreter_write_paths(command)
        if interp_paths is not None:
            if not interp_paths:
                _deny(
                    f"Write/Edit/apply_patch blocked: {WRITE_GUARD_DENY_TRIGGER}. "
                    f"Interpreter-mediated file writes are not permitted."
                )
                return

            cwd = os.environ.get("AUTOSKILLIT_CWD", "")
            if cwd and not os.path.isabs(cwd):
                cwd = ""

            resolved: list[str] = []
            for p in interp_paths:
                if os.path.isabs(p):
                    resolved.append(p)
                elif cwd:
                    resolved.append(os.path.join(cwd, p))
                else:
                    _deny(
                        f"Write/Edit/apply_patch blocked: {WRITE_GUARD_DENY_TRIGGER}. "
                        f"Interpreter-mediated file writes are not permitted."
                    )
                    return

            for rp in resolved:
                if not _within_any_prefix(rp):
                    _deny(
                        f"Write/Edit/apply_patch blocked: {WRITE_GUARD_DENY_TRIGGER}. "
                        f"Only writes to {display_prefix} are permitted."
                    )
                    return

        targets = _extract_bash_write_targets(command)
        if targets is None:
            sys.exit(0)
        if not targets:
            sys.exit(0)
        for target in targets:
            if not _within_any_prefix(target):
                _deny(
                    f"Write/Edit/apply_patch blocked: {WRITE_GUARD_DENY_TRIGGER}. "
                    f"Only writes to {display_prefix} are permitted."
                )
                return
        sys.exit(0)

    if tool_name == "apply_patch":
        command = tool_input.get("command", "")
        paths = _extract_paths_from_patch(command)
        if not paths:
            _deny(
                f"Write/Edit/apply_patch blocked: {WRITE_GUARD_DENY_TRIGGER} "
                f"(no target paths found in patch)."
            )
            return
        for p in paths:
            if not _within_any_prefix(p):
                _deny(
                    f"Write/Edit/apply_patch blocked: {WRITE_GUARD_DENY_TRIGGER}. "
                    f"Only writes to {display_prefix} are permitted."
                )
                return
        sys.exit(0)

    # Write or Edit
    file_path = tool_input.get("file_path", "")
    if not file_path:
        _deny(f"Write/Edit/apply_patch blocked: {WRITE_GUARD_DENY_TRIGGER} (no file_path).")
        return

    if _within_any_prefix(file_path):
        sys.exit(0)

    _deny(
        f"Write/Edit/apply_patch blocked: {WRITE_GUARD_DENY_TRIGGER}. "
        f"Only writes to {display_prefix} are permitted."
    )


if __name__ == "__main__":
    main()
