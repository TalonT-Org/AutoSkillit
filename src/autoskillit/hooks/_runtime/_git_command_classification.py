"""Classification primitives for git operations used by git_ops_guard.

The orchestrator (`git_ops_guard.py`) imports a controlled subset of the public
surface (`_classify_git_invocation`,
`_contains_blocked_git_op`, `_git_result`, `_git_text`, `_parse_worktree_owners`,
`_resolve_attempted_sha`, `_resolve_git_common_dir`) and dispatches into the inner
classifiers transitively through `_classify_git_invocation`.

Ref-rewriting subcommands are classified by outcome: branch, checkout, switch,
symbolic-ref, update-ref, reset, push and fetch read their argv through the
option tables in `_git_option_tables` and the parsers in `_git_outcome`, never by
matching option tokens.

This module is stdlib-only at the package level: it imports the
tokenization/classification primitives from `_command_classification`, the
outcome parsers from `_git_outcome` and one symbol from
`_github_mutation_analysis` (`_DYNAMIC_SHELL_TOKEN_RE`). All are bare-name
flat-mode siblings resolved via `git_ops_guard.py`'s `sys.path` bootstrap (or
the test-side bootstrap centralized in `tests/conftest.py`). It does NOT
re-export any `_command_classification` symbol.

`_contains_blocked_git_op` reads the command exclusively through
`all_evaluated_segments`/`live_command_text` (rectify #4941 Part A) rather than
a raw `shlex.split`/text-bag scan, so a `git` invocation delivered via
`bash -c`, a heredoc, a herestring, or a pipe is seen the same way a direct
invocation is.

Public API surface:
    Classifiers: _classify_git_invocation,
        _classify_fetch, _classify_push, _classify_update_ref, _classify_reset,
        _classify_outcome, _contains_blocked_git_op
    Helpers (consumed transitively by the classifiers above):
        _git_result, _git_text, _parse_worktree_owners,
        _resolve_git_common_dir, _normal_branch_ref, _symbolic_head,
        _resolve_attempted_sha, _refspec_targets, _same_repository,
        _resolve_remote_url, _local_path_from_remote_url
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from _command_classification import (
    GitInvocation,
    _command_position_candidate_spans,
    all_evaluated_segments,
    command_verb_and_args,
    extract_git_subcommand_and_flags,
    extract_interpreter_command_payloads,
    live_command_text,
)
from _git_outcome import (
    git_ref_outcome,
    parse_fetch,
    parse_push,
    parse_reset,
    parse_update_ref,
)
from _github_mutation_analysis import (
    _DYNAMIC_SHELL_TOKEN_RE,
)

_DELIMITERS_RE = re.compile(r"[\s,\[\]'\"()]+")


def _tokenize_text(text: str) -> frozenset[str]:
    """Split text on common delimiters for set-membership matching.

    Prevents '.' from matching periods inside filenames like 'foo.py'.
    """
    return frozenset(t for t in _DELIMITERS_RE.split(text) if t)


def _blocked_op_in_evaluated_segment(
    segment: list[str], blocked_ops: frozenset[tuple[str, ...]]
) -> tuple[str, ...] | None:
    """Return the first blocked git operation in an evaluated segment."""
    for start, end in _command_position_candidate_spans(segment):
        verb, args = command_verb_and_args(segment[start:end])
        if verb != "git" and not verb.endswith("/git"):
            continue
        result = extract_git_subcommand_and_flags([verb, *args])
        if result is None:
            continue
        subcommand = result.subcommand
        remaining = result.flags
        if subcommand == "<unresolved>":
            return (subcommand,)
        for op_tuple in blocked_ops:
            if subcommand == op_tuple[0] and all(flag in remaining for flag in op_tuple[1:]):
                return op_tuple
    return None


def _contains_blocked_git_op(
    cmd: str, blocked_ops: frozenset[tuple[str, ...]]
) -> tuple[str, ...] | None:
    """Return the matching blocked git op tuple, or None if no match.

    Reads *cmd* exclusively through `all_evaluated_segments`, the single
    authority for "what will actually execute, and as what argv" (rectify
    #4941 Part A): a direct invocation, one delivered via `bash -c`/`eval`,
    one fed through a heredoc/herestring/pipe to a shell, and a literal-argv
    `subprocess.run(["git", ...])` are all seen the same way, at every
    command-position candidate span (top level, and inside an inline
    function/group body). A 'git' token (or /path/to/git, or a wrapper- or
    env-assignment-prefixed invocation) is a command start via
    `command_verb_and_args`. Returns `None` when `all_evaluated_segments`
    cannot tokenize *cmd* (fail-open).

    `blocked_ops` is passed by the caller (the orchestrator's `main()`
    passes `_BLOCKED_GIT_OPS` to keep the constant co-located with itself
    per the sync-invariant test in `test_risky_git_ops_coverage.py`).
    """
    segments = all_evaluated_segments(cmd)
    if segments is None:
        return None

    for segment in segments:
        blocked_op = _blocked_op_in_evaluated_segment(segment, blocked_ops)
        if blocked_op is not None:
            return blocked_op

    # A Python payload whose subprocess/os call could not be resolved to a
    # literal argv or string (dynamic argument, unrecognized call shape)
    # means a "git" mention anywhere in what will actually run cannot be
    # ruled out -- deny unconditionally rather than silently falling
    # through to "not blocked".
    _payloads, has_unresolved = extract_interpreter_command_payloads(cmd)
    if has_unresolved and "git" in _tokenize_text(live_command_text(cmd)):
        return ("<unresolved>",)

    return None


def _git_result(cwd: str, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", cwd, *args],
        capture_output=True,
        check=False,
        text=False,
        timeout=10,
        env={**os.environ, "LC_ALL": "C", "LANGUAGE": ""},
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
    if value in ("", "<delete>", "<rename>", "<unresolved>"):
        return ""
    return _git_text(cwd, "rev-parse", "--verify", f"{value}^{{commit}}")


def _classify_update_ref(args: list[str], cwd: str) -> tuple[str, str, bool] | None:
    parsed = parse_update_ref(args)
    if parsed.unresolved is not None or parsed.stdin:
        return ("", "<unresolved>", True)
    if not parsed.operands:
        return None
    target = parsed.operands[0]
    if parsed.delete:
        attempted = "<delete>"
    elif len(parsed.operands) > 1:
        attempted = parsed.operands[1]
    else:
        return None
    if _DYNAMIC_SHELL_TOKEN_RE.search(target) or _DYNAMIC_SHELL_TOKEN_RE.search(attempted):
        return ("", "<unresolved>", True)
    if target == "HEAD" and parsed.no_deref:
        return ("HEAD", attempted, False)
    if target == "HEAD":
        target = _symbolic_head(cwd)
        if not target:
            return ("HEAD", attempted, False)
    return (_normal_branch_ref(target), attempted, False)


def _classify_outcome(subcommand: str, args: list[str], cwd: str) -> list[tuple[str, str, bool]]:
    """Ref rewrites of a branch/checkout/switch/symbolic-ref argv.

    A HEAD effect with no ref rewrite (``checkout other``, ``checkout -b x``,
    ``switch --detach``) yields nothing: Git itself refuses to check out a
    branch another worktree has checked out, and a primary-HEAD move is the
    cook rule's concern, not the shared-ref preflight's.
    """
    outcome = git_ref_outcome(subcommand, args)
    if outcome is None:
        return []
    if outcome.ref_ambiguous:
        return [("", "<unresolved>", True)]
    if outcome.rewrites_current_branch:
        return [(_symbolic_head(cwd) or "HEAD", "<rename>", False)]
    if outcome.rewritten_ref is not None:
        return [(_normal_branch_ref(outcome.rewritten_ref), outcome.start_point, False)]
    return []


def _classify_reset(args: list[str], cwd: str) -> tuple[str, str, bool] | None:
    parsed = parse_reset(args)
    if parsed.unresolved is not None:
        return ("", "<unresolved>", True)
    if parsed.path_mode:
        return None
    attempted = parsed.commit
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


def _fetch_mappings(
    refmaps: list[str], command_refspecs: list[str], cwd: str, remote: str
) -> list[str] | None:
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
            return None
    return mappings


def _classify_fetch(
    args: list[str], cwd: str, owned_refs: list[str]
) -> list[tuple[str, str, bool]]:
    fetch = parse_fetch(args)
    if fetch.unresolved is not None:
        return [("", "<unresolved>", True)]
    if fetch.remote is None:
        return []
    refmaps = [option.value or "" for option in fetch.parsed.options if option.name == "refmap"]
    mappings = _fetch_mappings(refmaps, list(fetch.refspecs), cwd, fetch.remote)
    if mappings is None:
        return [("", "<unresolved>", True)]
    result: list[tuple[str, str, bool]] = []
    for mapping in mappings:
        source = mapping.removeprefix("+").split(":", 1)[0]
        for target in _refspec_targets(mapping, owned_refs):
            result.append((target, source or "<delete>", False))
    return result


def _resolve_remote_url(candidate: str, execution_cwd: str) -> str | None:
    """Return the configured URL for candidate if it names a git remote, else None.

    The returned URL has already passed through git's `insteadOf`/
    `pushInsteadOf` config rewriting (git resolves this before printing), so
    callers compare the rewritten value, not the raw configured one. A remote
    configured with an empty URL (`url = `) also collapses to `None` here,
    indistinguishable from an unconfigured remote name -- both are
    pre-existing git-level ambiguities this guard does not disambiguate
    further.
    """
    url = _git_text(execution_cwd, "remote", "get-url", candidate)
    return url or None


def _local_path_from_remote_url(url: str) -> str | None:
    """Return the filesystem path url denotes if it is a local path/file:// remote.

    Returns None for a network remote -- a scheme URL (https://, ssh://,
    git://, ...) or git's SCP-like `user@host:path` syntax -- which cannot be
    a checked-out worktree of this machine's filesystem.
    """
    if url.startswith("file://"):
        return url[len("file://") :]
    if "://" in url:
        return None
    colon_index = url.find(":")
    slash_index = url.find("/")
    if colon_index != -1 and (slash_index == -1 or colon_index < slash_index):
        # SCP-like syntax (e.g. git@host:owner/repo.git) is recognized only
        # when no slash precedes the first colon -- mirrors git's own rule
        # for disambiguating scp-like remotes from local paths containing a
        # colon (see `git help clone`, "GIT URLS"). A Windows DOS-drive path
        # (e.g. `C:\repo`) matches this same shape and is classified as
        # network here, diverging from git's own DOS-drive special case --
        # benign for this guard, since a network classification only ever
        # widens the allow path (`_same_repository` returns `False`), never
        # the deny path.
        return None
    return url


def _same_repository(candidate: str, context: dict[str, object]) -> bool | None:
    """Return True if candidate resolves to the same repo, False if not, None if uncertain."""
    execution_cwd = str(context["execution_cwd"])
    remote_url = _resolve_remote_url(candidate, execution_cwd)
    if remote_url is not None:
        local_path = _local_path_from_remote_url(remote_url)
        if local_path is None:
            # candidate is a configured remote resolving to a network URL --
            # definitively not this worktree's own checkout.
            return False
        candidate_path = local_path
    else:
        candidate_path = candidate
    if not os.path.isabs(candidate_path):
        candidate_path = str(Path(execution_cwd) / candidate_path)
    common_path = _resolve_git_common_dir(candidate_path)
    if common_path is None:
        # rev-parse failed transiently (unreachable git, ref removed, fs error);
        # caller must treat as ambiguous rather than silently allowing push through.
        return None
    return str(common_path) == str(context["common_git_dir"])


def _classify_push(
    args: list[str], context: dict[str, object], owned_refs: list[str]
) -> list[tuple[str, str, bool]]:
    push = parse_push(args)
    if push.unresolved is not None:
        return [("", "<unresolved>", True)]
    if push.remote is None:
        return []
    same_repo = _same_repository(push.remote, context)
    if same_repo is False:
        return []
    if same_repo is None:
        return [("", "<unresolved>", True)]
    # Same-repository push with no explicit refspec (e.g. `git push .` or
    # `git push origin`) uses remote.pushDefault / branch.<name>.merge to
    # resolve the target — we cannot enumerate without reading config, so
    # treat as ambiguous: route through _all_threatened rather than fail-open.
    if not push.refspecs:
        return [("", "<unresolved>", True)]
    result: list[tuple[str, str, bool]] = []
    for refspec in push.refspecs:
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


def _classify_git_invocation(
    invocation: GitInvocation, context: dict[str, object]
) -> list[tuple[str, str, bool]]:
    subcommand = invocation.subcommand
    args = invocation.flags
    cwd = str(context["execution_cwd"])
    owners = context["owners"]
    if not isinstance(owners, dict):
        raise TypeError("context['owners'] must be a dict")
    owned_refs = sorted(owners)
    one: tuple[str, str, bool] | None = None
    if subcommand == "update-ref":
        one = _classify_update_ref(args, cwd)
    elif subcommand in ("branch", "checkout", "switch", "symbolic-ref"):
        return _classify_outcome(subcommand, args, cwd)
    elif subcommand == "reset":
        one = _classify_reset(args, cwd)
    elif subcommand == "fetch":
        return _classify_fetch(args, cwd, owned_refs)
    elif subcommand == "push":
        return _classify_push(args, context, owned_refs)
    return [one] if one is not None else []
