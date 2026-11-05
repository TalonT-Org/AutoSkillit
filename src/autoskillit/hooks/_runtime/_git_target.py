"""Which repository a Git command acts on, and what it does there.

Members: GitTargetKind, GitTarget, ChannelFlags, GitSegmentFacts,
command_channel_flags, analyze_git_segment, resolve_git_target_for_path,
expand_git_alias, _GIT_BUILTIN_SUBCOMMANDS, _GIT_MUTATION_SUBCOMMANDS.

``analyze_git_segment`` is the single analysis entry for a walked shell
segment. The target is RESOLVED only from a proven working directory; an
unknown cwd, a dynamic ``-C``, a repository-layout override or an unparseable
global option is UNRESOLVED, never a guessed repository. A path that is not
inside any repository is NOT_A_REPOSITORY. Callers decide what each kind
means for the command at hand.

Every Git query goes through ``_git_result`` (locale-stable), once per
canonical path per ``cache``.
"""

from __future__ import annotations

import os
import re
from enum import StrEnum
from typing import Any, Literal, NamedTuple

from _command_classification import (
    GitInvocation,
    _git_subcommand_index,
    extract_git_subcommand_and_flags,
    extract_interpreter_command_payloads,
    live_command_text,
    tokenize_command_segments,
)
from _git_command_classification import _git_result
from _git_outcome import GitHeadEffect, GitRefOutcome, git_ref_outcome, risky_git_operation
from _github_mutation_analysis import _DYNAMIC_SHELL_TOKEN_RE
from _path_identity import PathIdentityError, canonical_existing_path
from _shell_segment_walk import WalkedSegment, walk_evaluated_segments

# Git subcommands that can mutate checked-out refs. Read-only commands (log,
# status, diff, show, ...) must not be denied just because a preceding cd failed.
_GIT_MUTATION_SUBCOMMANDS: frozenset[str] = frozenset(
    {"update-ref", "branch", "checkout", "switch", "reset", "fetch", "push", "symbolic-ref"}
)

# ``git --list-cmds=builtins`` on the reference Git. Aliases cannot shadow these.
_GIT_BUILTIN_SUBCOMMANDS: frozenset[str] = frozenset(
    (
        "add am annotate apply archive bisect blame branch bugreport bundle cat-file "
        "check-attr check-ignore check-mailmap check-ref-format checkout checkout--worker "
        "checkout-index cherry cherry-pick clean clone column commit commit-graph commit-tree "
        "config count-objects credential credential-cache credential-cache--daemon "
        "credential-store describe diagnose diff diff-files diff-index diff-tree difftool "
        "fast-export fast-import fetch fetch-pack fmt-merge-msg for-each-ref for-each-repo "
        "format-patch fsck fsck-objects fsmonitor--daemon gc get-tar-commit-id grep "
        "hash-object help hook index-pack init init-db interpret-trailers log ls-files "
        "ls-remote ls-tree mailinfo mailsplit maintenance merge merge-base merge-file "
        "merge-index merge-ours merge-recursive merge-recursive-ours merge-recursive-theirs "
        "merge-subtree merge-tree mktag mktree multi-pack-index mv name-rev notes "
        "pack-objects pack-redundant pack-refs patch-id pickaxe prune prune-packed pull push "
        "range-diff read-tree rebase receive-pack reflog remote remote-ext remote-fd repack "
        "replace rerere reset restore rev-list rev-parse revert rm send-pack shortlog show "
        "show-branch show-index show-ref sparse-checkout stage stash status stripspace "
        "submodule--helper switch symbolic-ref tag unpack-file unpack-objects update-index "
        "update-ref update-server-info upload-archive upload-archive--writer upload-pack var "
        "verify-commit verify-pack verify-tag version whatchanged worktree write-tree"
    ).split()
)

_LAYOUT_ENV_NAMES: tuple[str, ...] = ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR")
_LAYOUT_CHANNEL_RE = re.compile(
    r"\b(?:GIT_DIR|GIT_WORK_TREE|GIT_COMMON_DIR)=|--git-dir\b|--work-tree\b|--bare\b"
    r"|core\.(?:worktree|bare)|include(?:If)?\."
)
_ALIAS_CHANNEL_RE = re.compile(r"alias\.|GIT_CONFIG|\bHOME=|XDG_CONFIG_HOME=")
_ALIAS_KEY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]*")
_DELIMITERS_RE = re.compile(r"[\s,\[\]'\"()]+")


class GitTargetKind(StrEnum):
    RESOLVED = "resolved"
    NOT_A_REPOSITORY = "not_a_repository"
    UNRESOLVED = "unresolved"


class GitTarget(NamedTuple):
    kind: GitTargetKind
    # "" unless UNRESOLVED: cwd_unknown | dynamic_cwd | layout_override |
    # unparsed_global_options | git_query_failed
    reason: str
    cwd: str
    worktree_git_dir: str
    common_git_dir: str
    is_main_worktree: bool
    toplevel: str | None


class ChannelFlags(NamedTuple):
    layout_override: bool
    alias_channel: bool


class GitSegmentFacts(NamedTuple):
    invocation: GitInvocation
    target: GitTarget
    outcome: GitRefOutcome | None
    identity_unknown: bool


def _unresolved(reason: str, cwd: str = "") -> GitTarget:
    return GitTarget(GitTargetKind.UNRESOLVED, reason, cwd, "", "", False, None)


def command_channel_flags(live_text: str) -> ChannelFlags:
    """Command-scoped channels that can redirect a Git command's repository or identity.

    The layout channel also trips on a layout variable in the hook's own
    environment, since that redirects the guard's own Git queries. The alias
    channel is read from the command text only, never from an ambient ``HOME``.
    """
    layout = _LAYOUT_CHANNEL_RE.search(live_text) is not None
    if not layout:
        for name, value in os.environ.items():
            if name in _LAYOUT_ENV_NAMES and value:
                layout = True
                break
    return ChannelFlags(layout, _ALIAS_CHANNEL_RE.search(live_text) is not None)


def _nearest_existing_directory(path: str) -> str:
    candidate = path
    while candidate and not os.path.isdir(candidate):
        parent = os.path.dirname(candidate)
        if parent == candidate:
            return ""
        candidate = parent
    return candidate


def _probe_repository(path: str) -> GitTarget:
    probe = _git_result(path, "rev-parse", "--absolute-git-dir", "--git-common-dir")
    if probe.returncode != 0:
        if b"not a git repository" in probe.stderr:
            return GitTarget(GitTargetKind.NOT_A_REPOSITORY, "", path, "", "", False, None)
        return _unresolved("git_query_failed", path)
    git_dir_raw, common_raw = probe.stdout.decode("utf-8", errors="strict").splitlines()[:2]
    worktree_git_dir = canonical_existing_path(git_dir_raw)
    # ``--git-common-dir`` is relative to the queried path (``.`` inside ``.git``).
    common_git_dir = canonical_existing_path(os.path.join(path, common_raw))
    # ``--show-toplevel`` fails inside ``.git`` and in a bare repository, so it
    # is a separate query.
    top = _git_result(path, "rev-parse", "--show-toplevel")
    toplevel = top.stdout.decode("utf-8", errors="strict").strip() if top.returncode == 0 else None
    return GitTarget(
        GitTargetKind.RESOLVED,
        "",
        path,
        str(worktree_git_dir),
        str(common_git_dir),
        worktree_git_dir == common_git_dir,
        toplevel,
    )


def _query_repository(path: str, cache: dict[Any, Any]) -> GitTarget:
    key = ("repository", path)
    if key not in cache:
        cache[key] = _probe_repository(path)
    target: GitTarget = cache[key]
    return target


def resolve_git_target_for_path(path: str, cache: dict[Any, Any]) -> GitTarget:
    """The repository containing *path*: its nearest existing ancestor directory."""
    directory = _nearest_existing_directory(path)
    if not directory:
        return _unresolved("cwd_unknown")
    try:
        canonical = str(canonical_existing_path(directory))
    except PathIdentityError:
        return _unresolved("cwd_unknown")
    return _query_repository(canonical, cache)


def _git_invocation(seg: WalkedSegment) -> tuple[GitInvocation | None, bool]:
    """The Git invocation a segment runs, and whether the command word is dynamic."""
    argv = seg.executable[seg.start :] if seg.start is not None else []
    if not argv:
        return None, False
    verb = argv[0]
    if verb == "git" or verb.endswith("/git"):
        return extract_git_subcommand_and_flags(argv), False
    name = os.path.basename(verb)
    if name.startswith("git-") and name[4:] in _GIT_BUILTIN_SUBCOMMANDS:
        return GitInvocation(name[4:], argv[1:], [], seg.executable[: seg.start]), False
    if (
        _DYNAMIC_SHELL_TOKEN_RE.search(verb)
        and len(argv) > 1
        and argv[1] in _GIT_MUTATION_SUBCOMMANDS
    ):
        return GitInvocation(argv[1], argv[2:], [], seg.executable[: seg.start]), True
    return None, False


def _segment_target(
    seg: WalkedSegment, invocation: GitInvocation, flags: ChannelFlags, cache: dict[Any, Any]
) -> GitTarget:
    if invocation.subcommand == "<unresolved>":
        return _unresolved("unparsed_global_options")
    if flags.layout_override:
        return _unresolved("layout_override")
    if not seg.cwd_certain or not seg.verb_cwd:
        return _unresolved("cwd_unknown")
    start = seg.start or 0
    base = os.path.normpath(seg.verb_cwd)
    tokens = None if seg.executable_tokens is None else seg.executable_tokens[start:]
    # ``-C`` is applied without normalization: chdir resolves ``..`` physically.
    _, cwd, _ = _git_subcommand_index(seg.executable[start:], base, tokens)
    if not cwd:
        return _unresolved("dynamic_cwd")
    return resolve_git_target_for_path(cwd, cache)


def expand_git_alias(
    target: GitTarget, invocation: GitInvocation
) -> GitInvocation | Literal["unknown"] | None:
    """Expand ``alias.<name>``; None for an external command, "unknown" when opaque."""
    result = _git_result(target.cwd, "config", "--get", f"alias.{invocation.subcommand}")
    if result.returncode == 1:
        return None
    if result.returncode != 0:
        return "unknown"
    value = result.stdout.decode("utf-8", errors="strict").strip()
    if value.startswith("!"):
        return "unknown"
    segments = tokenize_command_segments(value)
    if len(segments) != 1:
        return "unknown"
    expanded = extract_git_subcommand_and_flags(["git", *segments[0], *invocation.flags])
    if expanded is None or expanded.subcommand not in _GIT_BUILTIN_SUBCOMMANDS:
        return "unknown"
    return expanded


def _identity_unknown(invocation: GitInvocation, flags: ChannelFlags, dynamic_verb: bool) -> bool:
    subcommand = invocation.subcommand
    if dynamic_verb:
        return True
    if subcommand == "<unresolved>":
        return False
    return (
        _DYNAMIC_SHELL_TOKEN_RE.search(subcommand) is not None
        or _ALIAS_KEY_RE.fullmatch(subcommand) is None
        or (flags.alias_channel and subcommand not in _GIT_BUILTIN_SUBCOMMANDS)
    )


def analyze_git_segment(
    seg: WalkedSegment, flags: ChannelFlags, cache: dict[Any, Any]
) -> GitSegmentFacts | None:
    """Invocation, target and outcome of a walked segment; None for a non-Git command."""
    invocation, dynamic_verb = _git_invocation(seg)
    if invocation is None:
        return None
    identity_unknown = _identity_unknown(invocation, flags, dynamic_verb)
    target = _segment_target(seg, invocation, flags, cache)
    if (
        target.kind == GitTargetKind.RESOLVED
        and not identity_unknown
        and invocation.subcommand not in _GIT_BUILTIN_SUBCOMMANDS
    ):
        expanded = expand_git_alias(target, invocation)
        if expanded is None:
            return None
        if expanded == "unknown":
            identity_unknown = True
        else:
            invocation = expanded
    outcome = (
        GitRefOutcome(None, False, "HEAD", True, GitHeadEffect.AMBIGUOUS)
        if identity_unknown
        else git_ref_outcome(invocation.subcommand, invocation.flags)
    )
    return GitSegmentFacts(invocation, target, outcome, identity_unknown)


def _mutation_names(subcommands: frozenset[str]) -> str:
    return "|".join(sorted(re.escape(name) for name in subcommands))


def _mentions_git_mutation(
    text: str, subcommands: frozenset[str] = _GIT_MUTATION_SUBCOMMANDS
) -> bool:
    """Whether *text* names a Git command word followed by one of *subcommands*.

    Deliberately matches inside arguments too: an opaque launcher's payload
    (``xargs sh -c '...git push...'``) cannot be told apart from quoted data
    without per-launcher grammar.
    """
    names = _mutation_names(subcommands)
    pattern = rf"(?<![\w.-])git(?:-[a-z-]+)?(?![\w.])[^\n;&|]*?\b(?:{names})\b"
    return re.search(pattern, text) is not None


def _tokenize_text(text: str) -> frozenset[str]:
    """Split text on common delimiters for set-membership matching.

    Prevents '.' from matching periods inside filenames like 'foo.py'.
    """
    return frozenset(token for token in _DELIMITERS_RE.split(text) if token)


def _blocked_in_segment(
    seg: WalkedSegment,
    flags: ChannelFlags,
    cache: dict[Any, Any],
    blocked_ops: frozenset[tuple[str, ...]],
) -> tuple[str, ...] | None:
    names = frozenset(op[0] for op in blocked_ops)
    if seg.position_unresolved and _mentions_git_mutation(" ".join(seg.executable), names):
        return ("<unresolved>",)
    facts = analyze_git_segment(seg, flags, cache)
    if facts is None:
        return None
    if facts.identity_unknown:
        return ("<unresolved>",)
    return risky_git_operation(facts.invocation.subcommand, facts.invocation.flags, blocked_ops)


def contains_blocked_git_op(
    cmd: str,
    blocked_ops: frozenset[tuple[str, ...]],
    cwd: str = "",
    cache: dict[Any, Any] | None = None,
) -> tuple[str, ...] | None:
    """Return the blocked Git operation *cmd* performs, or None.

    Reads *cmd* exclusively through the evaluated-segment walk, the single
    authority for what will actually execute and as what argv (rectify #4941
    Part A): a direct invocation, one delivered via ``bash -c``/``eval``, a
    heredoc/herestring/pipe-fed shell, and a literal-argv
    ``subprocess.run(["git", ...])`` are all seen the same way. Operations are
    matched by outcome (``risky_git_operation``), not by option spelling.
    Returns None when *cmd* cannot be tokenized (fail-open).

    ``blocked_ops`` is passed by the caller (the orchestrator's ``main()`` passes
    ``_BLOCKED_GIT_OPS`` to keep the constant co-located with itself per the
    sync-invariant test in ``test_risky_git_ops_coverage.py``).
    """
    walk = walk_evaluated_segments(cmd, cwd)
    if walk is None:
        return None
    live_text = live_command_text(cmd)
    flags = command_channel_flags(live_text)
    shared_cache: dict[Any, Any] = {} if cache is None else cache
    for seg in walk:
        blocked = _blocked_in_segment(seg, flags, shared_cache, blocked_ops)
        if blocked is not None:
            return blocked

    # A Python payload whose subprocess/os call could not be resolved to a
    # literal argv or string means a "git" mention anywhere in what will
    # actually run cannot be ruled out -- deny rather than silently falling
    # through to "not blocked".
    _payloads, has_unresolved = extract_interpreter_command_payloads(cmd)
    if has_unresolved and "git" in _tokenize_text(live_text):
        return ("<unresolved>",)
    return None
