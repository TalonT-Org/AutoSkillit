#!/usr/bin/env python3
"""PreToolUse hook: protect checked-out refs and block destructive headless Git.

Blocks commit --amend, push --force, reset --hard, clean -f, checkout .
and related operations that rewrite history or destroy uncommitted changes.

stdlib-only; no autoskillit imports.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

_HOOKS_DIR = str(Path(__file__).resolve().parent.parent)  # hooks/
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

_GUARDS_DIR = str(Path(__file__).resolve().parent)  # hooks/guards/
if _GUARDS_DIR not in sys.path:
    sys.path.insert(0, _GUARDS_DIR)

from _command_classification import (  # type: ignore[import-not-found]  # noqa: E402
    _GIT_GLOBAL_FLAG_SPEC,
    _consume_str_flag,
    command_verb_and_args,
    extract_interpreter_command_payloads,
    extract_interpreter_write_paths,
    extract_redirect_targets,
    tokenize_command_segments,
    tokenize_shell_payload_segments,
)
from _git_command_classification import (  # type: ignore[import-not-found]  # noqa: E402
    _classify_git_segment,
    _contains_blocked_git_op,
    _git_result,
    _git_text,
    _parse_worktree_owners,
    _resolve_attempted_sha,
    _resolve_git_common_dir,
)
from _github_mutation_analysis import (  # type: ignore[import-not-found]  # noqa: E402
    _DYNAMIC_SHELL_TOKEN_RE,
)
from _hook_constants import (  # type: ignore[import-not-found]  # noqa: E402
    DENY_REASON_BY_GUARD,
    DENY_TRIGGER_BY_GUARD,
    EXEMPT_SKILLS_BY_GUARD,
)
from _hook_constants import (  # type: ignore[import-not-found]  # noqa: E402
    RISKY_GIT_OPERATIONS as _BLOCKED_GIT_OPS,
)
from _hook_payload import (  # type: ignore[import-not-found]  # noqa: E402
    parse_hook_command,
    resolve_state_root,
)
from _hook_settings import read_merged_hook_config  # type: ignore[import-not-found]  # noqa: E402

GIT_OPS_DENY_TRIGGER: str = DENY_TRIGGER_BY_GUARD["git_ops_guard"]
CHECKED_OUT_REF_DENY_PREFIX: str = "Checked-out ref mutation blocked: "

_DENY_REASON_TEMPLATE = DENY_REASON_BY_GUARD["git_ops_guard"]

_EXEMPT_SKILLS: frozenset[str] = EXEMPT_SKILLS_BY_GUARD["git_ops_guard"]
# Script-local orchestrator bypass; HookDef.exempt_session_types stays empty.
_EXEMPT_SESSION_TYPES: frozenset[str] = frozenset({"orchestrator"})

_RAW_WRITE_VERBS = frozenset(
    {"cp", "mv", "install", "tee", "truncate", "rm", "unlink", "sed", "dd"}
)


def _repository_context(execution_cwd: str) -> dict[str, object] | None:
    git_dir = _git_text(execution_cwd, "rev-parse", "--absolute-git-dir")
    common_path = _resolve_git_common_dir(execution_cwd)
    requesting = _git_text(execution_cwd, "rev-parse", "--show-toplevel")
    if not git_dir or common_path is None or not requesting:
        return None
    common_git_dir = str(common_path)
    worktrees = _git_result(execution_cwd, "worktree", "list", "--porcelain", "-z")
    if worktrees.returncode != 0:
        return None
    try:
        owners = _parse_worktree_owners(worktrees.stdout)
    except UnicodeDecodeError:
        return None
    return {
        "common_git_dir": common_git_dir,
        "execution_cwd": execution_cwd,
        "owners": owners,
        "requesting_worktree_path": requesting,
        "worktree_git_dir": git_dir,
    }


def _all_threatened(context: dict[str, object]) -> list[dict[str, object]]:
    owners = context["owners"]
    if not isinstance(owners, dict):
        raise TypeError("context['owners'] must be a dict")
    rows: list[dict[str, object]] = []
    for target_ref in sorted(owners):
        owner_paths = owners[target_ref]
        if not isinstance(owner_paths, list):
            raise TypeError("owner_paths must be a list")
        rows.append(
            {
                "old_sha": _git_text(str(context["execution_cwd"]), "rev-parse", target_ref),
                "owner_paths": owner_paths,
                "target_ref": target_ref,
            }
        )
    return rows


def _threatened_for_target(context: dict[str, object], target_ref: str) -> list[dict[str, object]]:
    if target_ref == "HEAD":
        return [
            {
                "old_sha": _git_text(str(context["execution_cwd"]), "rev-parse", "HEAD"),
                "owner_paths": [str(context["requesting_worktree_path"])],
                "target_ref": "HEAD",
            }
        ]
    owners = context["owners"]
    if not isinstance(owners, dict):
        raise TypeError("context['owners'] must be a dict")
    owner_paths = owners.get(target_ref)
    if not isinstance(owner_paths, list):
        return []
    return [
        {
            "old_sha": _git_text(str(context["execution_cwd"]), "rev-parse", target_ref),
            "owner_paths": owner_paths,
            "target_ref": target_ref,
        }
    ]


def _ctx_str(context: dict[str, object] | None, key: str) -> str:
    """Return str(context[key]) when context has the key, else ""."""
    if context is None:
        return ""
    return str(context.get(key, ""))


def _deny_checked_out_ref(
    *,
    data: dict[str, object],
    context: dict[str, object] | None,
    attempted_value: str,
    threatened_refs: list[dict[str, object]],
) -> None:
    details = {
        "attempted_value": attempted_value,
        "common_git_dir": _ctx_str(context, "common_git_dir"),
        "execution_cwd": _ctx_str(context, "execution_cwd"),
        "requesting_worktree_path": _ctx_str(context, "requesting_worktree_path"),
        "resolved_attempted_new_sha": (
            _resolve_attempted_sha(_ctx_str(context, "execution_cwd"), attempted_value)
            if context
            else ""
        ),
        "session_id": str(data.get("session_id", "")),
        "threatened_refs": threatened_refs,
        "worktree_git_dir": _ctx_str(context, "worktree_git_dir"),
    }
    reason = CHECKED_OUT_REF_DENY_PREFIX + json.dumps(
        details, sort_keys=True, separators=(",", ":")
    )
    sys.stdout.write(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        )
        + "\n"
    )
    raise SystemExit(0)


def _raw_write_targets(command: str, segments: list[list[str]]) -> tuple[list[str], bool]:
    targets: list[str] = []
    ambiguous = False
    for segment in segments:
        targets.extend(extract_redirect_targets(segment))
        verb, args = command_verb_and_args(segment)
        verb = os.path.basename(verb)
        if verb not in _RAW_WRITE_VERBS:
            continue
        if verb == "dd":
            candidates = [token.split("=", 1)[1] for token in args if token.startswith("of=")]
        elif verb == "sed":
            candidates = args[-1:] if any(token.startswith("-i") for token in args) else []
        else:
            candidates = args[-1:]
        for candidate in candidates:
            if _DYNAMIC_SHELL_TOKEN_RE.search(candidate):
                ambiguous = True
            elif candidate:
                targets.append(candidate)
    interpreter_paths = extract_interpreter_write_paths(command)
    if interpreter_paths == [] and "open(" in command:
        ambiguous = True
    elif interpreter_paths:
        targets.extend(interpreter_paths)
    return (targets, ambiguous)


def _raw_target_mutations(
    command: str, segments: list[list[str]], context: dict[str, object]
) -> list[tuple[str, str, bool]]:
    targets, ambiguous = _raw_write_targets(command, segments)
    if ambiguous:
        return [("", "<unresolved>", True)]
    common = Path(str(context["common_git_dir"])).resolve()
    worktree_git = Path(str(context["worktree_git_dir"])).resolve()
    worktrees_dir = common / "worktrees"
    result: list[tuple[str, str, bool]] = []
    for raw_target in targets:
        target = Path(raw_target)
        if not target.is_absolute():
            target = Path(str(context["execution_cwd"])) / target
        target = target.resolve()
        if target == worktree_git / "HEAD":
            result.append(("HEAD", "<unresolved>", False))
        elif target == common / "HEAD":
            result.append(("HEAD", "<unresolved>", False))
        elif target == common / "packed-refs":
            result.append(("", "<unresolved>", True))
        else:
            try:
                relative = target.relative_to(worktrees_dir)
            except ValueError:
                pass
            else:
                # <common>/worktrees/<name>/HEAD writes the HEAD of another
                # worktree (the per-worktree HEAD symref). Fail closed by
                # routing it through _all_threatened against every owned ref
                # — we can't cheaply resolve which branch that worktree
                # checked out, so we deny any owner to be safe.
                if relative.parts and len(relative.parts) == 2 and relative.parts[1] == "HEAD":
                    result.append(("", "<unresolved>", True))
                    continue
                # <common>/worktrees/<name>/refs/... is a per-worktree
                # ref store; any write to it must deny against every
                # owned ref (ambiguous which branch it lands in).
                if len(relative.parts) >= 2 and relative.parts[1] == "refs":
                    result.append(("", "<unresolved>", True))
                    continue
            try:
                relative = target.relative_to(common / "refs" / "heads")
            except ValueError:
                continue
            result.append((f"refs/heads/{relative.as_posix()}", "<unresolved>", False))
    return result


def _git_segment_cwd(segment: list[str], cwd: str) -> str:
    verb, args = command_verb_and_args(segment)
    if verb != "git" and not verb.endswith("/git"):
        return cwd
    current = Path(cwd)
    index = 0
    while index < len(args):
        token = args[index]
        if token == "-C" and index + 1 < len(args):
            candidate = Path(args[index + 1])
            current = candidate if candidate.is_absolute() else current / candidate
            index += 2
            continue
        if not token.startswith("-"):
            break
        # -C's own value is captured above (needed to build `current`); every
        # other global flag is just skipped past, same as before -- an
        # unrecognized flag advances by one token rather than stopping, this
        # is cwd-tracking, not the security-critical subcommand parse.
        _, next_index, recognized = _consume_str_flag(args, index, _GIT_GLOBAL_FLAG_SPEC)
        index = next_index if recognized else index + 1
    return str(current.resolve())


def _preflight_checked_out_ref_mutation(
    data: dict[str, object], command: str, execution_cwd: str
) -> None:
    outer_segments = tokenize_command_segments(command)
    nested_segments = tokenize_shell_payload_segments(command)
    interpreter_payloads, interpreter_unresolved = extract_interpreter_command_payloads(command)
    structural_mutation = bool(
        re.search(
            r"\bgit\b[^\n;&|]*(?:update-ref|branch\s+(?:-f|--force)|checkout\s+-B|switch\s+-C|"
            r"reset\b|fetch\b|push\b|symbolic-ref\s+HEAD)\b",
            command,
        )
        or any(
            os.path.basename(command_verb_and_args(segment)[0]) in _RAW_WRITE_VERBS
            for segment in outer_segments
        )
    )
    if not execution_cwd:
        if structural_mutation:
            _deny_checked_out_ref(
                data=data,
                context=None,
                attempted_value="<unresolved>",
                threatened_refs=[],
            )
        return
    current_cwd = execution_cwd
    for segment in outer_segments:
        verb, args = command_verb_and_args(segment)
        if verb == "cd":
            # Accept `cd <dir>`, `cd -P <dir>` (-P resolves physical path),
            # and `cd -- <dir>` (-- ends option parsing). Reject dynamic
            # dirs (`cd $foo`, `cd $(cmd)`) and `cd -` (which swaps to
            # OLDPWD — unresolvable without $OLDPWD state) by bailing to
            # an empty cwd so subsequent git segments are skipped rather
            # than classified against a stale or invalid cwd.
            if any(arg == "-" for arg in args):
                current_cwd = ""
                continue
            target_args = [arg for arg in args if arg not in ("-P", "--")]
            if len(target_args) == 1 and not _DYNAMIC_SHELL_TOKEN_RE.search(target_args[0]):
                candidate = Path(target_args[0])
                current_cwd = str(
                    (
                        candidate if candidate.is_absolute() else Path(current_cwd) / candidate
                    ).resolve()
                )
                continue
            if len(args) != 1:
                # Anything else (cd with no args, nested-expansion)
                # cannot be resolved safely; bail out rather than letting
                # later git segments classify against a stale cwd.
                current_cwd = ""
                continue
        elif verb == "pushd" and len(args) == 1 and not _DYNAMIC_SHELL_TOKEN_RE.search(args[0]):
            # pushd swaps cwd into the new dir; track it like cd.
            candidate = Path(args[0])
            current_cwd = str(
                (candidate if candidate.is_absolute() else Path(current_cwd) / candidate).resolve()
            )
            continue
        if not current_cwd:
            # cwd resolution failed earlier in this command — refuse to
            # classify subsequent git segments rather than silently routing
            # them against the wrong repo.
            continue
        segment_context = _repository_context(_git_segment_cwd(segment, current_cwd))
        if segment_context is None:
            continue
        for target_ref, attempted_value, ambiguous in _classify_git_segment(
            segment, segment_context
        ):
            threatened = (
                _all_threatened(segment_context)
                if ambiguous
                else _threatened_for_target(segment_context, target_ref)
            )
            if threatened:
                _deny_checked_out_ref(
                    data=data,
                    context=segment_context,
                    attempted_value=attempted_value,
                    threatened_refs=threatened,
                )

    context = _repository_context(current_cwd)
    if context is None:
        return
    additional_segments: list[list[str]] = []
    if nested_segments:
        additional_segments.extend(nested_segments)
    for payload in interpreter_payloads:
        if isinstance(payload, list):
            additional_segments.append(payload)
        else:
            additional_segments.extend(tokenize_command_segments(payload))
    mutations: list[tuple[str, str, bool]] = []
    for segment in additional_segments:
        mutations.extend(_classify_git_segment(segment, context))
    mutations.extend(_raw_target_mutations(command, outer_segments, context))
    if interpreter_unresolved and structural_mutation:
        mutations.append(("", "<unresolved>", True))

    for target_ref, attempted_value, ambiguous in mutations:
        threatened = (
            _all_threatened(context) if ambiguous else _threatened_for_target(context, target_ref)
        )
        if threatened:
            _deny_checked_out_ref(
                data=data,
                context=context,
                attempted_value=attempted_value,
                threatened_refs=threatened,
            )


def main() -> None:
    try:
        data = json.loads(sys.stdin.read())
        if not isinstance(data, dict):
            sys.exit(0)
        parsed = parse_hook_command(data)
    except (json.JSONDecodeError, AttributeError, OSError, TypeError, ValueError):
        sys.exit(0)

    cmd = parsed.command or ""

    if not cmd:
        sys.exit(0)

    project_root = resolve_state_root(parsed.payload_cwd)
    try:
        cfg_path = project_root / ".autoskillit" / "temp" / ".hook_config.json"
        kitchen_open = cfg_path.exists()
    except OSError:
        sys.exit(0)

    if kitchen_open:
        try:
            _preflight_checked_out_ref_mutation(data, cmd, parsed.execution_cwd)
        except (
            OSError,
            subprocess.SubprocessError,
            TypeError,
            UnicodeDecodeError,
            ValueError,
        ) as exc:
            # Fail-closed on preflight exception. SystemExit raised by
            # _deny_checked_out_ref is BaseException, not Exception, so it
            # bypasses this handler and serves as the explicit deny signal.
            sys.stderr.write(f"git_ops_guard: preflight failed: {exc}\n")
            sys.exit(2)

    if os.environ.get("AUTOSKILLIT_HEADLESS") != "1":
        sys.exit(0)

    blocked = _contains_blocked_git_op(cmd, _BLOCKED_GIT_OPS)
    if blocked is None:
        sys.exit(0)

    skill_name = os.environ.get("AUTOSKILLIT_SKILL_NAME", "")
    if skill_name in _EXEMPT_SKILLS:
        sys.exit(0)

    session_type = os.environ.get("AUTOSKILLIT_SESSION_TYPE", "")
    if session_type in _EXEMPT_SESSION_TYPES:
        sys.exit(0)

    # Hook config file is written by open_kitchen and removed by close_kitchen.
    # Its presence reliably signals an open kitchen without needing session ID.
    if not kitchen_open:
        sys.exit(0)

    # Recipe-level authorization: check git_ops_policy for per-subcommand allow.
    try:
        hook_data = read_merged_hook_config(root=project_root)
        git_ops_policy = hook_data.get("git_ops_policy", {})
        if git_ops_policy.get(f"allow_{blocked[0]}"):
            sys.exit(0)
    except (OSError, json.JSONDecodeError, AttributeError, TypeError) as exc:
        sys.stderr.write(f"git_ops_guard: config read error: {exc}\n")

    op_str = " ".join(("git",) + blocked)
    deny_reason = _DENY_REASON_TEMPLATE.format(op=op_str)
    payload = json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": deny_reason,
            }
        }
    )
    sys.stdout.write(payload + "\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
