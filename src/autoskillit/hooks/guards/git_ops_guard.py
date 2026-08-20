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
import shlex
import subprocess
import sys
from pathlib import Path

_HOOKS_DIR = str(Path(__file__).resolve().parent.parent)
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)

from _command_classification import (  # type: ignore[import-not-found]  # noqa: E402
    _GIT_GLOBAL_FLAG_SPEC,
    _SHELL_OPS,
    _consume_str_flag,
    _FlagArity,
    command_verb_and_args,
    extract_git_subcommand_and_flags,
    extract_interpreter_command_payloads,
    extract_interpreter_write_paths,
    extract_redirect_targets,
    has_interpreter_wrapped_command,
    has_nested_shell,
    tokenize_command_segments,
    tokenize_shell_payload_segments,
)
from _github_mutation_analysis import (  # type: ignore[import-not-found]  # noqa: E402
    _DYNAMIC_SHELL_TOKEN_RE,
)
from _hook_payload import (  # type: ignore[import-not-found]  # noqa: E402
    parse_hook_command,
    resolve_state_root,
)
from _hook_settings import read_merged_hook_config  # type: ignore[import-not-found]  # noqa: E402

GIT_OPS_DENY_TRIGGER: str = "Destructive git operation blocked in headless session"
CHECKED_OUT_REF_DENY_PREFIX: str = "Checked-out ref mutation blocked: "

_DENY_REASON_TEMPLATE = (
    "Destructive git operation '{op}' is blocked in headless skill sessions. "
    "Create a new commit instead of amending, and avoid force-push, reset --hard, "
    "clean -f, or checkout . in automated workflows."
)

# Must stay in sync with RISKY_GIT_OPERATIONS in hook_registry.py —
# stdlib-only boundary prevents a shared import. test_risky_git_ops_coverage.py
# enforces that this set covers every tuple in RISKY_GIT_OPERATIONS.
_BLOCKED_GIT_OPS: frozenset[tuple[str, ...]] = frozenset(
    {
        ("commit", "--amend"),
        ("push", "--force"),
        ("push", "-f"),
        ("push", "--force-with-lease"),
        ("reset", "--hard"),
        ("clean", "-f"),
        ("clean", "-fd"),
        ("checkout", "."),
        ("checkout", "--", "."),
    }
)

# No skill legitimately needs destructive git ops in a headless session.
_EXEMPT_SKILLS: frozenset[str] = frozenset()

# Must stay in sync with exempt_session_types on the git_ops_guard HookDef
# in hook_registry.py — stdlib-only boundary prevents a shared import.
_EXEMPT_SESSION_TYPES: frozenset[str] = frozenset({"orchestrator"})

_RAW_WRITE_VERBS = frozenset(
    {"cp", "mv", "install", "tee", "truncate", "rm", "unlink", "sed", "dd"}
)


# git fetch's own flag spec (distinct from _GIT_GLOBAL_FLAG_SPEC, git's
# top-level global flags), verified against a live `git fetch -h` read.
# Covers every fetch flag, including its `--no-X` negation form for each
# boolean flag git itself documents as `--[no-]X`. Used by _classify_fetch
# to correctly skip past an unrecognized fetch flag's value instead of
# misreading it as the remote/refspec positional.
_GIT_FETCH_BOOLEAN_FLAGS: frozenset[str] = frozenset(
    {
        "-v",
        "--verbose",
        "-q",
        "--quiet",
        "--all",
        "--set-upstream",
        "-a",
        "--append",
        "--atomic",
        "-f",
        "--force",
        "-m",
        "--multiple",
        "-t",
        "--tags",
        "-n",
        "--prefetch",
        "-p",
        "--prune",
        "-P",
        "--prune-tags",
        "--dry-run",
        "--porcelain",
        "--write-fetch-head",
        "-k",
        "--keep",
        "-u",
        "--update-head-ok",
        "--progress",
        "--unshallow",
        "--refetch",
        "--update-shallow",
        "-4",
        "--ipv4",
        "-6",
        "--ipv6",
        # --recurse-submodules[=<on-demand>] is optional-value; see
        # _GIT_GLOBAL_FLAG_SPEC's --exec-path precedent for why BOOLEAN is
        # the correct default (its `=`-form safely fails closed instead).
        "--recurse-submodules",
        "--negotiate-only",
        "--auto-maintenance",
        "--auto-gc",
        "--show-forced-updates",
        "--write-commit-graph",
        "--stdin",
    }
)
_GIT_FETCH_VALUE_FLAGS: frozenset[str] = frozenset(
    {
        "-j",
        "--jobs",
        "--depth",
        "--shallow-since",
        "--shallow-exclude",
        "--deepen",
        "--refmap",
        "-o",
        "--server-option",
        "--negotiation-tip",
        "--filter",
        "--upload-pack",
    }
)
_GIT_FETCH_FLAG_SPEC: dict[str, _FlagArity] = {
    **{flag: _FlagArity.BOOLEAN for flag in _GIT_FETCH_BOOLEAN_FLAGS},
    **{
        f"--no-{flag.removeprefix('--')}": _FlagArity.BOOLEAN
        for flag in _GIT_FETCH_BOOLEAN_FLAGS
        if flag.startswith("--")
    },
    **{flag: _FlagArity.VALUE for flag in _GIT_FETCH_VALUE_FLAGS},
    **{
        f"--no-{flag.removeprefix('--')}": _FlagArity.BOOLEAN
        for flag in _GIT_FETCH_VALUE_FLAGS
        if flag.startswith("--")
    },
}


_DELIMITERS_RE = re.compile(r"[\s,\[\]'\"()]+")


def _tokenize_text(text: str) -> frozenset[str]:
    """Split text on common delimiters for set-membership matching.

    Prevents '.' from matching periods inside filenames like 'foo.py'.
    """
    return frozenset(t for t in _DELIMITERS_RE.split(text) if t)


def _contains_blocked_git_op(cmd: str) -> tuple[str, ...] | None:
    """Return the matching blocked git op tuple, or None if no match.

    Tokenises with shlex. A 'git' token (or /path/to/git) is considered a
    command start when it is at position 0 or immediately follows a shell
    separator token. env-prefixed invocations (VAR=1 git ...) are skipped
    (fail-open), matching artifact_download_guard behavior.
    """
    try:
        tokens = shlex.split(cmd)
    except ValueError:
        return None

    for i, token in enumerate(tokens):
        if token != "git" and not token.endswith("/git"):
            continue
        # Only treat as a command start at position 0 or after a shell operator.
        if i != 0 and tokens[i - 1] not in _SHELL_OPS:
            continue
        result = extract_git_subcommand_and_flags(tokens[i:])
        if result is None:
            continue
        subcommand, remaining = result
        if subcommand == "<unresolved>":
            # An unrecognized global git flag means the real subcommand
            # could not be found at all -- deny unconditionally rather
            # than matching against _BLOCKED_GIT_OPS's literal tuples,
            # which "<unresolved>" can never equal (an unhandled case
            # would silently fall through to "not blocked" here).
            return (subcommand,)
        for op_tuple in _BLOCKED_GIT_OPS:
            if subcommand != op_tuple[0]:
                continue
            flags = op_tuple[1:]
            if all(f in remaining for f in flags):
                return op_tuple

    # Check for interpreter-wrapped invocations (python3 -c "subprocess.run(['git', ...])")
    if has_interpreter_wrapped_command(cmd, target_commands=["git"]):
        text_tokens = _tokenize_text(cmd.lower())
        for op_tuple in _BLOCKED_GIT_OPS:
            if op_tuple[0] in text_tokens and all(f in text_tokens for f in op_tuple[1:]):
                return op_tuple

    # Check for nested shell invocations (bash -c "git commit --amend")
    if has_nested_shell(cmd):
        text_tokens = _tokenize_text(cmd.lower())
        for op_tuple in _BLOCKED_GIT_OPS:
            if (
                "git" in text_tokens
                and op_tuple[0] in text_tokens
                and all(f in text_tokens for f in op_tuple[1:])
            ):
                return op_tuple

    return None


def _git_result(cwd: str, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", cwd, *args],
        capture_output=True,
        check=False,
        text=False,
        timeout=10,
    )


def _git_text(cwd: str, *args: str) -> str:
    result = _git_result(cwd, *args)
    if result.returncode != 0:
        return ""
    return result.stdout.decode("utf-8", errors="strict").strip()


def _parse_worktree_owners(raw: bytes) -> dict[str, list[str]]:
    owners: dict[str, list[str]] = {}
    for raw_record in raw.split(b"\0\0"):
        worktree_path = ""
        branch_ref = ""
        detached = False
        for raw_field in raw_record.split(b"\0"):
            if not raw_field:
                continue
            field = raw_field.decode("utf-8", errors="strict")
            key, _, value = field.partition(" ")
            if key == "worktree":
                worktree_path = value
            elif key == "branch":
                branch_ref = value
            elif key == "detached":
                detached = True
        if worktree_path and branch_ref.startswith("refs/heads/") and not detached:
            owners.setdefault(branch_ref, []).append(worktree_path)
    for paths in owners.values():
        paths.sort()
    return owners


def _resolve_git_common_dir(cwd: str) -> Path | None:
    """Return the resolved common git dir for cwd, or None if rev-parse fails."""
    common_raw = _git_text(cwd, "rev-parse", "--git-common-dir")
    if not common_raw:
        return None
    common_path = Path(common_raw)
    if not common_path.is_absolute():
        common_path = Path(cwd) / common_path
    return common_path.resolve()


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


def _normal_branch_ref(value: str) -> str:
    if value.startswith("refs/heads/"):
        return value
    if value == "HEAD" or value.startswith("refs/"):
        return value
    return f"refs/heads/{value}"


def _symbolic_head(cwd: str) -> str:
    return _git_text(cwd, "symbolic-ref", "-q", "HEAD")


def _resolve_attempted_sha(cwd: str, value: str) -> str:
    if value in ("", "<delete>", "<unresolved>"):
        return ""
    return _git_text(cwd, "rev-parse", "--verify", f"{value}^{{commit}}")


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


def _consume_option_value(args: list[str], index: int, option: str) -> int:
    return index + 2 if args[index] == option and index + 1 < len(args) else index + 1


def _classify_update_ref(args: list[str], cwd: str) -> tuple[str, str, bool] | None:
    no_deref = False
    delete = False
    positional: list[str] = []
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--stdin":
            return ("", "<unresolved>", True)
        if token == "--no-deref":
            no_deref = True
        elif token in ("-d", "--delete"):
            delete = True
        elif token == "-m":
            index = _consume_option_value(args, index, token)
            continue
        elif token == "--create-reflog":
            # Boolean flag — do NOT consume the next token as its value.
            index += 1
            continue
        elif token.startswith("-"):
            pass
        else:
            positional.append(token)
        index += 1
    if not positional:
        return None
    target = positional[0]
    attempted = "<delete>" if delete else (positional[1] if len(positional) > 1 else "")
    if not attempted:
        return None
    if _DYNAMIC_SHELL_TOKEN_RE.search(target) or _DYNAMIC_SHELL_TOKEN_RE.search(attempted):
        return ("", "<unresolved>", True)
    if target == "HEAD" and no_deref:
        return ("HEAD", attempted, False)
    if target == "HEAD":
        target = _symbolic_head(cwd)
        if not target:
            return ("HEAD", attempted, False)
    return (_normal_branch_ref(target), attempted, False)


def _classify_branch_position(subcommand: str, args: list[str]) -> tuple[str, str, bool] | None:
    if subcommand == "branch":
        if not any(token in ("-f", "--force") for token in args):
            return None
        # `git branch -f <name> [<start-point>]` — `-f` is a flag that
        # may appear anywhere in the arg list, so gather positionals
        # across the whole list. Target is the first positional, start
        # point (if any) is the second.
        positional = [token for token in args if not token.startswith("-")]
        if not positional:
            return None
        target = positional[0]
        attempted = positional[1] if len(positional) > 1 else "HEAD"
    else:
        marker = "-B" if subcommand == "checkout" else "-C"
        try:
            marker_index = args.index(marker)
        except ValueError:
            return None
        # `git checkout -B <branch> [<start-point>]` and `git switch -C
        # <branch> [<start-point>]` — the marker MUST be followed by the
        # new branch name (next positional token), but the start-point
        # may appear anywhere else (before or after the marker). Do NOT
        # conflate "any positional after marker" with "target", since
        # start-points placed before the marker would silently bypass
        # detection (`git checkout <sha> -B main` must still deny against
        # `refs/heads/main`).
        if marker_index + 1 >= len(args):
            return None
        target = args[marker_index + 1]
        if target.startswith("-"):
            return None
        other_positionals = [
            token
            for index, token in enumerate(args)
            if index != marker_index + 1 and not token.startswith("-")
        ]
        attempted = other_positionals[0] if other_positionals else "HEAD"
    if _DYNAMIC_SHELL_TOKEN_RE.search(target) or _DYNAMIC_SHELL_TOKEN_RE.search(attempted):
        return ("", "<unresolved>", True)
    return (_normal_branch_ref(target), attempted, False)


def _classify_reset(args: list[str], cwd: str) -> tuple[str, str, bool] | None:
    if "--" in args or any(token.startswith("--pathspec-from-file") for token in args):
        return None
    attempted = next((token for token in args if not token.startswith("-")), "HEAD")
    target = _symbolic_head(cwd)
    if not target:
        # Fail-closed: ambiguous on rev-parse failure — route to _all_threatened.
        return ("", "<unresolved>", True)
    if _DYNAMIC_SHELL_TOKEN_RE.search(attempted):
        return ("", "<unresolved>", True)
    old_sha = _git_text(cwd, "rev-parse", target)
    attempted_sha = _resolve_attempted_sha(cwd, attempted)
    # Fail-closed: empty == empty would silently allow; treat as ambiguous.
    if not old_sha or not attempted_sha:
        return ("", "<unresolved>", True)
    if attempted_sha == old_sha:
        return None
    return (target, attempted, False)


def _refspec_targets(refspec: str, owned_refs: list[str]) -> list[str]:
    refspec = refspec.removeprefix("+")
    if refspec.startswith("^") or ":" not in refspec:
        return []
    destination = refspec.split(":", 1)[1]
    if not destination:
        return []
    if _DYNAMIC_SHELL_TOKEN_RE.search(destination.replace("*", "")):
        return owned_refs
    destination = _normal_branch_ref(destination)
    if "*" not in destination:
        return [destination]
    prefix, suffix = destination.split("*", 1)
    return [ref for ref in owned_refs if ref.startswith(prefix) and ref.endswith(suffix)]


def _classify_fetch(
    args: list[str], cwd: str, owned_refs: list[str]
) -> list[tuple[str, str, bool]]:
    if "--stdin" in args:
        return [("", "<unresolved>", True)]
    refmaps: list[str] = []
    positional: list[str] = []
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--refmap" and index + 1 < len(args):
            refmaps.append(args[index + 1])
            index += 2
            continue
        if token.startswith("--refmap="):
            refmaps.append(token.split("=", 1)[1])
            index += 1
            continue
        if token.startswith("-"):
            _, next_index, recognized = _consume_str_flag(args, index, _GIT_FETCH_FLAG_SPEC)
            if not recognized:
                # An unrecognized fetch flag's value would otherwise be
                # misread as the remote/refspec positional -- fail closed
                # into the same ambiguous-deny idiom this function already
                # uses for --stdin and an unreadable refmap config, rather
                # than silently misclassifying the fetch destination.
                return [("", "<unresolved>", True)]
            index = next_index
            continue
        positional.append(token)
        index += 1
    if not positional:
        return []
    remote = positional[0]
    command_refspecs = positional[1:]
    mappings: list[str] = []
    if refmaps:
        mappings.extend(mapping for mapping in refmaps if mapping)
    elif command_refspecs:
        mappings.extend(spec for spec in command_refspecs if ":" in spec)
    else:
        configured = _git_result(cwd, "config", "--get-all", f"remote.{remote}.fetch")
        if configured.returncode in (0, 1):
            mappings.extend(
                line for line in configured.stdout.decode("utf-8", errors="strict").splitlines()
            )
        elif configured.returncode >= 2:
            # Fail-closed: refmap unreadable (filesystem/permission) — return
            # ambiguous so the caller routes through _all_threatened against
            # every owned ref instead of silently allowing the fetch through.
            return [("", "<unresolved>", True)]
    result: list[tuple[str, str, bool]] = []
    for mapping in mappings:
        source = mapping.removeprefix("+").split(":", 1)[0]
        for target in _refspec_targets(mapping, owned_refs):
            result.append((target, source or "<delete>", False))
    return result


def _same_repository(candidate: str, context: dict[str, object]) -> bool | None:
    """Return True if candidate resolves to the same repo, False if not, None if uncertain."""
    candidate_path = candidate
    if not os.path.isabs(candidate_path):
        candidate_path = str(Path(str(context["execution_cwd"])) / candidate_path)
    common_path = _resolve_git_common_dir(candidate_path)
    if common_path is None:
        # rev-parse failed transiently (unreachable git, ref removed, fs error);
        # caller must treat as ambiguous rather than silently allowing push through.
        return None
    return str(common_path) == str(context["common_git_dir"])


def _classify_push(
    args: list[str], context: dict[str, object], owned_refs: list[str]
) -> list[tuple[str, str, bool]]:
    positional = [token for token in args if not token.startswith("-")]
    if not positional:
        return []
    same_repo = _same_repository(positional[0], context)
    if same_repo is False:
        return []
    if same_repo is None:
        return [("", "<unresolved>", True)]
    # Same-repository push with no explicit refspec (e.g. `git push .` or
    # `git push origin`) uses remote.pushDefault / branch.<name>.merge to
    # resolve the target — we cannot enumerate without reading config, so
    # treat as ambiguous: route through _all_threatened rather than fail-open.
    if len(positional) < 2:
        return [("", "<unresolved>", True)]
    result: list[tuple[str, str, bool]] = []
    for refspec in positional[1:]:
        # Colonless refspec (e.g. `git push . feature-branch`) defaults to
        # `feature-branch:feature-branch` and still updates a checked-out
        # ref if it lands in owned_refs — normalize before classifying.
        normalized = refspec if ":" in refspec else f"{refspec}:{refspec}"
        source, destination = normalized.removeprefix("+").split(":", 1)
        if _DYNAMIC_SHELL_TOKEN_RE.search(destination):
            return [("", "<unresolved>", True)]
        target = _normal_branch_ref(destination)
        if target in owned_refs:
            result.append((target, source or "<delete>", False))
    return result


def _classify_git_segment(
    segment: list[str], context: dict[str, object]
) -> list[tuple[str, str, bool]]:
    parsed = extract_git_subcommand_and_flags(segment)
    if parsed is None:
        return []
    subcommand, args = parsed
    if subcommand == "<unresolved>":
        # An unrecognized global git flag means the real subcommand could
        # not be found -- ambiguous-deny (route through _all_threatened
        # against every owned ref), not "nothing to check here".
        return [("", "<unresolved>", True)]
    cwd = str(context["execution_cwd"])
    owners = context["owners"]
    if not isinstance(owners, dict):
        raise TypeError("context['owners'] must be a dict")
    owned_refs = sorted(owners)
    one: tuple[str, str, bool] | None = None
    if subcommand == "update-ref":
        one = _classify_update_ref(args, cwd)
    elif subcommand in ("branch", "checkout", "switch"):
        one = _classify_branch_position(subcommand, args)
    elif subcommand == "reset":
        one = _classify_reset(args, cwd)
    elif subcommand == "fetch":
        return _classify_fetch(args, cwd, owned_refs)
    elif subcommand == "push":
        return _classify_push(args, context, owned_refs)
    elif subcommand == "symbolic-ref":
        # -m <reason> can appear before OR after the positionals — its value
        # looks positional, so we skip past it instead of naively filtering.
        # `len >= 2` guards against IndexError on read forms like `symbolic-ref HEAD`.
        positional: list[str] = []
        index = 0
        while index < len(args):
            token = args[index]
            if token == "-m":
                index += 2
                continue
            if token.startswith("-"):
                index += 1
                continue
            positional.append(token)
            index += 1
        if len(positional) >= 2 and positional[0] == "HEAD":
            one = (_normal_branch_ref(positional[1]), positional[1], False)
    return [one] if one is not None else []


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

    blocked = _contains_blocked_git_op(cmd)
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
