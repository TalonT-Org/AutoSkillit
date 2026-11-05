"""Git command outcomes: what an argv does, not which tokens it contains.

Members: GitHeadEffect, GitRefOutcome, git_ref_outcome, UpdateRefArgs,
parse_update_ref, ResetArgs, parse_reset, PushArgs, parse_push, FetchArgs,
parse_fetch, is_ref_mutation.

Every classification reads options through the ``_option_grammar`` engine over
the tables in ``_git_option_tables``, so a stuck, bundled, abbreviated or
reordered spelling means what it means to Git. ``git_ref_outcome`` maps
branch, checkout, switch and symbolic-ref argv to the branch ref a command
rewrites and the effect on ``HEAD``; the other parsers expose the operands the
classifiers in ``_git_command_classification`` need.

Expansion word-splitting into options (``git checkout $FLAGS``) is outside this
bounded classification; a dynamic deciding operand is reported ambiguous.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum
from typing import TYPE_CHECKING, NamedTuple

from _git_option_tables import (
    _GIT_BRANCH_FLAG_SPEC,
    _GIT_CHECKOUT_FLAG_SPEC,
    _GIT_FETCH_FLAG_SPEC,
    _GIT_PUSH_FLAG_SPEC,
    _GIT_RESET_FLAG_SPEC,
    _GIT_SUBCOMMAND_TABLES,
    _GIT_SWITCH_FLAG_SPEC,
    _GIT_SYMBOLIC_REF_FLAG_SPEC,
    _GIT_UPDATE_REF_FLAG_SPEC,
)
from _github_mutation_analysis import _DYNAMIC_SHELL_TOKEN_RE

if TYPE_CHECKING:
    from autoskillit.hooks._classification._option_grammar import (
        ParsedArgv,
        ParsedOption,
        last_option,
        parse_options,
    )
else:
    if __package__:
        from .._classification import _option_grammar
    else:
        from _classification import _option_grammar

    ParsedArgv = _option_grammar.ParsedArgv
    ParsedOption = _option_grammar.ParsedOption
    last_option = _option_grammar.last_option
    parse_options = _option_grammar.parse_options


class GitHeadEffect(StrEnum):
    NONE = "none"
    MOVES = "moves"
    PATH_RESTORE = "path_restore"
    AMBIGUOUS = "ambiguous"


class GitRefOutcome(NamedTuple):
    rewritten_ref: str | None
    rewrites_current_branch: bool
    start_point: str
    ref_ambiguous: bool
    head_effect: GitHeadEffect


def _dynamic(token: str) -> bool:
    return bool(_DYNAMIC_SHELL_TOKEN_RE.search(token))


def _active(parsed: ParsedArgv, name: str) -> ParsedOption | None:
    """The last occurrence of *name*, unless a later ``--no-`` negates it."""
    option = last_option(parsed, name)
    return option if option is not None and not option.negated else None


def _head(effect: GitHeadEffect) -> GitRefOutcome:
    return GitRefOutcome(None, False, "HEAD", False, effect)


def _rewrite(ref: str, start_point: str, effect: GitHeadEffect) -> GitRefOutcome:
    ambiguous = _dynamic(ref) or _dynamic(start_point)
    return GitRefOutcome(ref, False, start_point, ambiguous, effect)


def _ambiguous_outcome() -> GitRefOutcome:
    return GitRefOutcome(None, False, "HEAD", True, GitHeadEffect.AMBIGUOUS)


def _single_operand_effect(operand: str) -> GitHeadEffect:
    """Effect of ``git checkout <operand>`` with no ``--``.

    A branch, a remote-tracking guess, a commit and a path need repository
    queries to tell apart, so only the unambiguous spellings are decided.
    """
    if _dynamic(operand):
        return GitHeadEffect.AMBIGUOUS
    if operand == "-" or "..." in operand or (operand.startswith("@{-") and operand.endswith("}")):
        return GitHeadEffect.MOVES
    # ``:/`` is the top-of-tree pathspec; ``:/.`` and other ``:/<text>`` are
    # revisions naming a commit by message, so they fall through.
    if operand in (".", ":/") or operand.startswith(("./", "/")) or "/./" in operand:
        return GitHeadEffect.PATH_RESTORE
    return GitHeadEffect.AMBIGUOUS


def _dashdash_effect(before: int, after: int) -> GitHeadEffect:
    """Effect of ``git checkout <before operands> -- <after operands>``."""
    if before > 1:
        return GitHeadEffect.AMBIGUOUS
    if after:
        return GitHeadEffect.PATH_RESTORE
    return GitHeadEffect.NONE if before == 0 else GitHeadEffect.MOVES


def _checkout_operand_effect(parsed: ParsedArgv) -> GitHeadEffect:
    operands = parsed.operands
    before = parsed.operands_before_dashdash
    if before is not None:
        return _dashdash_effect(before, len(operands) - before)
    if not operands:
        return GitHeadEffect.NONE
    if len(operands) >= 2:
        return GitHeadEffect.PATH_RESTORE
    return _single_operand_effect(operands[0])


def _checkout_outcome(args: Sequence[str]) -> GitRefOutcome:
    parsed = parse_options(args, _GIT_CHECKOUT_FLAG_SPEC)
    if parsed.unresolved:
        return _ambiguous_outcome()
    operands = parsed.operands
    forced = _active(parsed, "B")
    if forced is not None:
        return _rewrite(
            forced.value or "", operands[0] if operands else "HEAD", GitHeadEffect.MOVES
        )
    if any(_active(parsed, name) for name in ("b", "orphan", "detach", "track")):
        return _head(GitHeadEffect.MOVES)
    if _active(parsed, "pathspec-from-file"):
        return _head(GitHeadEffect.AMBIGUOUS)
    if any(_active(parsed, name) for name in ("patch", "ours", "theirs")):
        return _head(GitHeadEffect.PATH_RESTORE)
    return _head(_checkout_operand_effect(parsed))


def _switch_outcome(args: Sequence[str]) -> GitRefOutcome:
    parsed = parse_options(args, _GIT_SWITCH_FLAG_SPEC)
    if parsed.unresolved:
        return _ambiguous_outcome()
    operands = parsed.operands
    forced = _active(parsed, "force-create")
    if forced is not None:
        return _rewrite(
            forced.value or "", operands[0] if operands else "HEAD", GitHeadEffect.MOVES
        )
    if any(_active(parsed, name) for name in ("create", "orphan", "detach", "track")):
        return _head(GitHeadEffect.MOVES)
    if not operands:
        return _head(GitHeadEffect.NONE)
    return _head(GitHeadEffect.MOVES if len(operands) == 1 else GitHeadEffect.AMBIGUOUS)


_BRANCH_NON_REWRITING_MODES = (
    "delete",
    "D",
    "list",
    "all",
    "remotes",
    "show-current",
    "set-upstream-to",
    "set-upstream",
    "unset-upstream",
    "edit-description",
    "contains",
    "no-contains",
    "with",
    "without",
    "merged",
    "no-merged",
)


def _branch_outcome(args: Sequence[str]) -> GitRefOutcome | None:
    parsed = parse_options(args, _GIT_BRANCH_FLAG_SPEC)
    if parsed.unresolved:
        return _ambiguous_outcome()
    operands = parsed.operands
    if _active(parsed, "move") or _active(parsed, "M"):
        if len(operands) == 1:
            return GitRefOutcome(None, True, "HEAD", False, GitHeadEffect.MOVES)
        if len(operands) == 2:
            return _rewrite(operands[0], "HEAD", GitHeadEffect.NONE)
        return None
    if _active(parsed, "copy") or _active(parsed, "C"):
        if 1 <= len(operands) <= 2:
            return _rewrite(operands[-1], operands[0], GitHeadEffect.NONE)
        return None
    if any(_active(parsed, name) for name in _BRANCH_NON_REWRITING_MODES):
        return None
    if _active(parsed, "force") and operands:
        return _rewrite(
            operands[0], operands[1] if len(operands) > 1 else "HEAD", GitHeadEffect.NONE
        )
    return None


def _symbolic_ref_outcome(args: Sequence[str]) -> GitRefOutcome | None:
    parsed = parse_options(args, _GIT_SYMBOLIC_REF_FLAG_SPEC)
    if parsed.unresolved:
        return _ambiguous_outcome()
    operands = parsed.operands
    if _active(parsed, "delete") and operands:
        return _rewrite(operands[0], "HEAD", GitHeadEffect.NONE)
    if len(operands) < 2:
        return None
    if _dynamic(operands[0]):
        return _ambiguous_outcome()
    if operands[0] == "HEAD":
        return _rewrite(operands[1], operands[1], GitHeadEffect.MOVES)
    return _rewrite(operands[0], operands[1], GitHeadEffect.NONE)


def git_ref_outcome(subcommand: str, args: Sequence[str]) -> GitRefOutcome | None:
    """The ref a branch/checkout/switch/symbolic-ref argv rewrites and its HEAD effect.

    ``None`` means the argv rewrites no branch ref and has no HEAD effect to
    report (list, delete and similar modes).
    """
    if subcommand == "checkout":
        return _checkout_outcome(args)
    if subcommand == "switch":
        return _switch_outcome(args)
    if subcommand == "branch":
        return _branch_outcome(args)
    if subcommand == "symbolic-ref":
        return _symbolic_ref_outcome(args)
    return None


class UpdateRefArgs(NamedTuple):
    stdin: bool
    delete: bool
    no_deref: bool
    operands: tuple[str, ...]
    unresolved: str | None


def parse_update_ref(args: Sequence[str]) -> UpdateRefArgs:
    parsed = parse_options(args, _GIT_UPDATE_REF_FLAG_SPEC)
    deref = next(
        (option for option in reversed(parsed.options) if option.name in ("deref", "no-deref")),
        None,
    )
    return UpdateRefArgs(
        stdin=_active(parsed, "stdin") is not None,
        delete=_active(parsed, "d") is not None,
        no_deref=deref is not None and deref.name == "no-deref",
        operands=parsed.operands,
        unresolved=parsed.unresolved,
    )


class ResetArgs(NamedTuple):
    mode: str | None
    path_mode: bool
    commit: str
    unresolved: str | None


def _last_reset_mode(parsed: ParsedArgv) -> str | None:
    modes = ("soft", "mixed", "hard", "merge", "keep")
    return next((o.name for o in reversed(parsed.options) if o.name in modes), None)


def parse_reset(args: Sequence[str]) -> ResetArgs:
    parsed = parse_options(args, _GIT_RESET_FLAG_SPEC)
    unresolved = parsed.unresolved
    if unresolved is None and _active(parsed, "pathspec-from-file"):
        unresolved = "pathspec_from_file"
    operands = parsed.operands
    before = parsed.operands_before_dashdash
    mode = _last_reset_mode(parsed)
    path_mode = (
        _active(parsed, "patch") is not None
        or (before is not None and len(operands) > before)
        or len(operands) >= 2
    )
    has_commit = bool(operands) and before != 0
    return ResetArgs(mode, path_mode, operands[0] if has_commit else "HEAD", unresolved)


def _remote_and_refspecs(
    parsed: ParsedArgv, repo: ParsedOption | None
) -> tuple[str | None, tuple[str, ...]]:
    operands = parsed.operands
    # A positional repository overrides --repo (builtin/push.c).
    if operands:
        return operands[0], operands[1:]
    return (repo.value, ()) if repo is not None else (None, ())


class PushArgs(NamedTuple):
    parsed: ParsedArgv
    remote: str | None
    refspecs: tuple[str, ...]
    unresolved: str | None


def parse_push(args: Sequence[str]) -> PushArgs:
    parsed = parse_options(args, _GIT_PUSH_FLAG_SPEC)
    remote, refspecs = _remote_and_refspecs(parsed, _active(parsed, "repo"))
    return PushArgs(parsed, remote, refspecs, parsed.unresolved)


class FetchArgs(NamedTuple):
    parsed: ParsedArgv
    remote: str | None
    refspecs: tuple[str, ...]
    unresolved: str | None


def parse_fetch(args: Sequence[str]) -> FetchArgs:
    parsed = parse_options(args, _GIT_FETCH_FLAG_SPEC)
    unresolved = parsed.unresolved
    if unresolved is None and _active(parsed, "stdin"):
        unresolved = "stdin"
    remote, refspecs = _remote_and_refspecs(parsed, None)
    return FetchArgs(parsed, remote, refspecs, unresolved)


def _path_like_remote(remote: str) -> bool:
    return remote == "." or remote.startswith(("/", "./", "../", "file://")) or _dynamic(remote)


def is_ref_mutation(subcommand: str, args: Sequence[str]) -> bool:
    """Whether the command can rewrite a local branch ref, for an unknown cwd.

    Used when the repository the command acts on cannot be resolved. A push or
    fetch to a *named* remote does not count: it cannot write local refs.
    """
    if subcommand == "<unresolved>" or _dynamic(subcommand):
        return True
    if subcommand == "update-ref":
        return True
    if subcommand == "reset":
        reset = parse_reset(args)
        return reset.unresolved is not None or not reset.path_mode
    if subcommand in ("branch", "checkout", "switch", "symbolic-ref"):
        outcome = git_ref_outcome(subcommand, args)
        return outcome is not None and (
            outcome.rewritten_ref is not None
            or outcome.rewrites_current_branch
            or outcome.ref_ambiguous
        )
    if subcommand == "push":
        push = parse_push(args)
        return push.unresolved is not None or (
            push.remote is not None and _path_like_remote(push.remote)
        )
    if subcommand == "fetch":
        fetch = parse_fetch(args)
        return (
            fetch.unresolved is not None
            or _active(fetch.parsed, "update-head-ok") is not None
            or any(":" in refspec for refspec in fetch.refspecs)
        )
    return False


# ``:/`` is top-of-tree magic only before nothing or a wildcard: ``:/.`` and other
# ``:/<text>`` name a commit by message, a revision rather than a pathspec.
_PATHSPEC_MAGIC_RE = re.compile(r"^:(?:/(?=\*|$)|\([a-z,]*\))?")


def _is_whole_tree_pathspec(token: str) -> bool:
    """Whether a pathspec selects the whole tree: ``.``, ``./``, ``d/..``, ``:/``, ``:(top)``,
    ``*`` and their equivalents once pathspec magic and redundant segments are removed."""
    normalized = os.path.normpath(_PATHSPEC_MAGIC_RE.sub("", token, count=1))
    return normalized == "." or set(normalized) == {"*"}


def _risky_amend(parsed: ParsedArgv) -> bool:
    return _active(parsed, "amend") is not None


def _risky_force_push(parsed: ParsedArgv) -> bool:
    forced_refspec = any(refspec.startswith("+") for refspec in parsed.operands[1:])
    overwrites_remote = _active(parsed, "force") or _active(parsed, "mirror")
    return overwrites_remote is not None or forced_refspec


def _risky_force_with_lease(parsed: ParsedArgv) -> bool:
    return _active(parsed, "force-with-lease") is not None


def _risky_hard_reset(parsed: ParsedArgv) -> bool:
    return _last_reset_mode(parsed) == "hard"


def _risky_clean(parsed: ParsedArgv) -> bool:
    """Any clean that is not a dry run: Git refuses an unforced clean unless config
    says otherwise, so this covers every config channel without querying config."""
    return not any(_active(parsed, name) for name in ("dry-run", "interactive"))


def _risky_clean_directories(parsed: ParsedArgv) -> bool:
    return _risky_clean(parsed) and _active(parsed, "d") is not None


def _risky_checkout_whole_tree(parsed: ParsedArgv) -> bool:
    operands = parsed.operands
    if not operands:
        return _active(parsed, "force") is not None
    if parsed.operands_before_dashdash is not None:
        return False
    return any(_is_whole_tree_pathspec(operand) for operand in operands)


def _risky_checkout_dashdash_whole_tree(parsed: ParsedArgv) -> bool:
    before = parsed.operands_before_dashdash
    return before is not None and any(
        _is_whole_tree_pathspec(operand) for operand in parsed.operands[before:]
    )


# Keyed exactly by RISKY_GIT_OPERATIONS (tests/hooks/test_git_outcome.py pins this).
_RISKY_PREDICATES: Mapping[tuple[str, ...], Callable[[ParsedArgv], bool]] = {
    ("commit", "--amend"): _risky_amend,
    ("push", "--force"): _risky_force_push,
    ("push", "-f"): _risky_force_push,
    ("push", "--force-with-lease"): _risky_force_with_lease,
    ("reset", "--hard"): _risky_hard_reset,
    ("clean", "-f"): _risky_clean,
    ("clean", "-fd"): _risky_clean_directories,
    ("checkout", "."): _risky_checkout_whole_tree,
    ("checkout", "--", "."): _risky_checkout_dashdash_whole_tree,
}


def risky_git_operation(
    subcommand: str, args: Sequence[str], blocked_ops: frozenset[tuple[str, ...]]
) -> tuple[str, ...] | None:
    """The blocked operation an invocation performs, by outcome rather than by spelling.

    An unresolved subcommand or option parse is returned as ``("<unresolved>",)``.
    Among several matching operations the longest wins, then sorted order, so the
    choice is deterministic. Option values are never read as flags, so
    ``commit -m '--amend'`` is not an amend.
    """
    if subcommand == "<unresolved>":
        return ("<unresolved>",)
    candidates = [op for op in blocked_ops if op[0] == subcommand]
    if not candidates:
        return None
    parsed = parse_options(args, _GIT_SUBCOMMAND_TABLES[subcommand])
    if parsed.unresolved:
        return ("<unresolved>",)
    matching = sorted(
        (op for op in candidates if _RISKY_PREDICATES[op](parsed)), key=lambda op: (-len(op), op)
    )
    return matching[0] if matching else None
