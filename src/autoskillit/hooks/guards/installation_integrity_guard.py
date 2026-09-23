#!/usr/bin/env python3
"""Deny writes that target a protected installation tree."""

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
    WRITE_VERBS,
    all_evaluated_segments,
    command_verb,
    extract_interpreter_write_paths,
    extract_patch_paths,
    extract_redirect_targets_with_status,
    extract_write_verb_targets,
    resolve_write_target,
    updated_execution_cwd,
)
from _hook_payload import (  # type: ignore[import-not-found]  # noqa: E402
    extract_apply_patch_text,
    parse_hook_command,
)
from _policy_event import (  # type: ignore[import-not-found]  # noqa: E402
    PolicyEvent,
    render_provenance_prefix,
)

INSTALLATION_INTEGRITY_DENY_TRIGGER = "protected installation"


def _deny(reason_code: str, detail: str) -> None:
    prefix = render_provenance_prefix(
        PolicyEvent(
            hook_id="installation-integrity-guard",
            hook_version=1,
            event="PreToolUse",
            decision="deny",
            reason_code=reason_code,
        )
    )
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"{prefix} {detail}",
            }
        },
        sys.stdout,
    )
    sys.exit(0)


def _path_is_within(path: str, root: Path) -> bool:
    try:
        return os.path.commonpath(
            (os.path.realpath(path), os.path.realpath(root))
        ) == os.path.realpath(root)
    except (OSError, ValueError):
        return False


def _known_roots() -> tuple[Path, ...]:
    home = Path.home()
    return (
        Path(__file__).resolve().parents[2],
        home / ".autoskillit" / "plugin-generations",
        home / ".local" / "share" / "uv" / "tools" / "autoskillit",
        home / ".local" / "bin" / "autoskillit",
    )


def _has_install_layout(path: str) -> bool:
    parts = Path(os.path.normpath(path)).parts
    return any(
        parts[index : index + 2]
        in (("site-packages", "autoskillit"), ("dist-packages", "autoskillit"))
        for index in range(len(parts) - 1)
    ) or (any(part.startswith("archive-v") for part in parts) and "autoskillit" in parts)


def _nearest_existing_ancestor(path: str) -> str:
    candidate = Path(path)
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    try:
        return os.path.realpath(candidate)
    except OSError:
        return str(candidate)


def _matches_protected_inode(path: str) -> bool:
    try:
        target = os.stat(path)
    except OSError:
        return False
    if target.st_nlink <= 1:
        return False
    for root in _known_roots():
        if not root.exists():
            continue
        entries = _tree_entries(root)
        for entry in entries:
            try:
                protected = entry.stat()
            except OSError:
                continue
            if (protected.st_dev, protected.st_ino) == (target.st_dev, target.st_ino):
                return True
    return False


def _tree_entries(root: Path):
    pending = [root]
    seen_dirs: set[Path] = set()
    while pending:
        candidate = pending.pop()
        yield candidate
        try:
            # ``Path.is_dir()`` follows symlinks, so a self-referential
            # symlink would cause unbounded recursion. Skip symlinked
            # directories and dedupe by resolved path.
            if candidate.is_symlink():
                continue
            if candidate.is_dir() and candidate not in seen_dirs:
                seen_dirs.add(candidate)
                pending.extend(candidate.iterdir())
        except OSError:
            continue


def _is_protected_target(path: str) -> bool:
    try:
        normalized = os.path.realpath(path)
    except (OSError, ValueError):
        return True
    ancestor = _nearest_existing_ancestor(path)
    if _has_install_layout(normalized) or _has_install_layout(ancestor):
        return True
    if any(
        _path_is_within(normalized, root) or _path_is_within(ancestor, root)
        for root in _known_roots()
    ):
        return True
    return _matches_protected_inode(normalized)


def _bash_targets(command: str, cwd: str) -> tuple[list[str], bool]:
    segments = all_evaluated_segments(command)
    if segments is None:
        # Fail-closed: an unparseable shell command is an unresolved write target,
        # matching the docstring's contract that shell-local indirection into an
        # installation tree must not bypass the protection floor.
        return [], True
    targets: list[str] = []
    unresolved_target = False
    for segment in segments:
        verb = command_verb(segment)
        if verb == "cd":
            cwd = updated_execution_cwd(segment, cwd)
            continue
        if verb in WRITE_VERBS:
            found, unresolved = extract_write_verb_targets(verb, segment, cwd)
            targets.extend(found)
            unresolved_target = unresolved_target or unresolved
        redirects, unresolved = extract_redirect_targets_with_status(segment, cwd)
        targets.extend(redirects)
        unresolved_target = unresolved_target or unresolved
    interpreter_paths = extract_interpreter_write_paths(command)
    # ``extract_interpreter_write_paths``: None=non-write, []=interpreter write
    # detected but not all targets are static literals (fail-closed below),
    # [...]=literal targets only.
    if interpreter_paths is not None and not interpreter_paths:
        unresolved_target = True
    elif interpreter_paths:
        for path in interpreter_paths:
            resolved = resolve_write_target(path, cwd)
            if resolved is None:
                unresolved_target = True
            else:
                targets.append(resolved)
    return targets, unresolved_target


def _resolve_targets(paths: list[str], cwd: str) -> tuple[list[str], bool]:
    targets: list[str] = []
    unresolved_target = False
    for path in paths:
        resolved = resolve_write_target(path, cwd)
        if resolved is None:
            unresolved_target = True
        else:
            targets.append(resolved)
    return targets, unresolved_target


def _collect_write_targets(data: dict[str, object]) -> tuple[list[str], bool]:
    tool_name = data.get("tool_name")
    if not isinstance(tool_name, str):
        return [], False
    parsed = parse_hook_command(data)
    if tool_name in {"Write", "Edit"}:
        tool_input = data.get("tool_input")
        path = tool_input.get("file_path", "") if isinstance(tool_input, dict) else ""
        if not isinstance(path, str) or not path:
            return [], False
        return _resolve_targets([path], parsed.payload_cwd)
    if tool_name == "apply_patch":
        command = extract_apply_patch_text(data) or ""
        return _resolve_targets(extract_patch_paths(command), parsed.payload_cwd)
    if parsed.tool_kind in {"bash", "run_cmd"}:
        return _bash_targets(parsed.command or "", parsed.execution_cwd)
    return [], False


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, OSError, ValueError):
        sys.exit(0)
    if not isinstance(data, dict):
        sys.exit(0)

    targets, unresolved_target = _collect_write_targets(data)

    if unresolved_target:
        _deny("unresolved-write-target", "Write target could not be resolved safely.")
    if any(_is_protected_target(path) for path in targets):
        _deny(
            "protected-installation-target",
            f"Writing a {INSTALLATION_INTEGRITY_DENY_TRIGGER} is prohibited.",
        )


if __name__ == "__main__":
    main()
