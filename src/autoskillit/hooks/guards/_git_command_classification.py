"""Classification primitives for git operations — extracted from git_ops_guard.

Step 9 (#4733) of the decompose-hooks-files plan (#4665). All `_classify_*`
primitives and the helpers used exclusively by them moved from
`git_ops_guard.py` (1,002 lines) into this sibling module. The orchestrator
(`git_ops_guard.py`) imports a controlled subset of the public surface
(`_classify_git_segment`, `_contains_blocked_git_op`, `_git_result`,
`_git_text`, `_parse_worktree_owners`, `_resolve_attempted_sha`,
`_resolve_git_common_dir`) and dispatches into the inner classifiers
transitively through `_classify_git_segment`.

This module is stdlib-only at the package level: it imports six symbols from
`_command_classification` (the tokenization primitives `_consume_str_flag`,
`_SHELL_OPS`, `extract_git_subcommand_and_flags`,
`has_interpreter_wrapped_command`, `has_nested_shell`, and the structural
type `_FlagArity`) and one symbol from `_github_mutation_analysis`
(`_DYNAMIC_SHELL_TOKEN_RE`). Both are bare-name flat-mode siblings resolved
via `git_ops_guard.py`'s `sys.path` bootstrap (or the test-side bootstrap in
`tests/infra/test_git_ops_guard.py`). It does NOT re-export any
`_command_classification` symbol — the public-API surface is the seven
classification primitives and their helpers above.

`extract_git_subcommand_and_flags` (from `_command_classification`) is the
actual function used by `_classify_git_segment` — the parent plan's
reference to a hypothetical `_extract_git_subcommand_and_remaining` was a
documentation error that this module surfaces without acting on.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from pathlib import Path

from _command_classification import (  # type: ignore[import-not-found]  # noqa: E402
    _SHELL_OPS,
    _consume_str_flag,
    _FlagArity,
    extract_git_subcommand_and_flags,
    has_interpreter_wrapped_command,
    has_nested_shell,
)
from _github_mutation_analysis import (  # type: ignore[import-not-found]  # noqa: E402
    _DYNAMIC_SHELL_TOKEN_RE,
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


def _contains_blocked_git_op(
    cmd: str, blocked_ops: frozenset[tuple[str, ...]]
) -> tuple[str, ...] | None:
    """Return the matching blocked git op tuple, or None if no match.

    Tokenises with shlex. A 'git' token (or /path/to/git) is considered a
    command start when it is at position 0 or immediately follows a shell
    separator token. env-prefixed invocations (VAR=1 git ...) are skipped
    (fail-open), matching artifact_download_guard behavior.

    `blocked_ops` is passed by the caller (the orchestrator's `main()`
    passes `_BLOCKED_GIT_OPS` to keep the constant co-located with itself
    per the sync-invariant test in `test_risky_git_ops_coverage.py`).
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
            # than matching against blocked_ops's literal tuples,
            # which "<unresolved>" can never equal (an unhandled case
            # would silently fall through to "not blocked" here).
            return (subcommand,)
        for op_tuple in blocked_ops:
            if subcommand != op_tuple[0]:
                continue
            flags = op_tuple[1:]
            if all(f in remaining for f in flags):
                return op_tuple

    # Check for interpreter-wrapped invocations (python3 -c "subprocess.run(['git', ...])")
    if has_interpreter_wrapped_command(cmd, target_commands=["git"]):
        text_tokens = _tokenize_text(cmd.lower())
        for op_tuple in blocked_ops:
            if op_tuple[0] in text_tokens and all(f in text_tokens for f in op_tuple[1:]):
                return op_tuple

    # Check for nested shell invocations (bash -c "git commit --amend")
    if has_nested_shell(cmd):
        text_tokens = _tokenize_text(cmd.lower())
        for op_tuple in blocked_ops:
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
