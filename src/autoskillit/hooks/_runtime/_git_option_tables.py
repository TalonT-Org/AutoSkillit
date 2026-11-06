"""Option tables for the commands whose argv ``git_ops_guard`` classifies.

Covers the Git subcommands in ``_GIT_SUBCOMMAND_TABLES`` and ``sed`` (``_SED_FLAG_SPEC``),
whose in-place edit makes its last operand a raw write target in the guard's raw-write scan.
Both are read by the ``_option_grammar`` engine.

Members: _GIT_CHECKOUT_FLAG_SPEC, _GIT_SWITCH_FLAG_SPEC, _GIT_BRANCH_FLAG_SPEC,
_GIT_SYMBOLIC_REF_FLAG_SPEC, _GIT_UPDATE_REF_FLAG_SPEC, _GIT_RESET_FLAG_SPEC,
_GIT_PUSH_FLAG_SPEC, _GIT_FETCH_FLAG_SPEC, _GIT_COMMIT_FLAG_SPEC,
_GIT_CLEAN_FLAG_SPEC, _SED_FLAG_SPEC, _GIT_SUBCOMMAND_TABLES,
_GIT_TABLES_REFERENCE_VERSION.

Data only. Rows come from ``git <sub> -h`` and ``git <sub>
--git-completion-helper-all`` on ``_GIT_TABLES_REFERENCE_VERSION``, including
hidden and short-only rows. An arity must agree with the live completion
helper: a trailing ``=`` there means REQUIRED, and a ``--no-<x>`` after its
bare ``--`` means the row is negatable.

branch's ``merged``, ``no-merged``, ``contains`` and ``no-contains`` are
OPTIONAL although Git declares them ``PARSE_OPT_LASTARG_DEFAULT`` (the next
token is their value unless they are last, so ``branch --contains --list``
exits 129). The engine has no fourth arity for that, and the divergence never
changes a verdict because every list-mode form classifies as no ref outcome.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autoskillit.hooks._classification._option_grammar import OptionArity, OptionDef
else:
    if __package__:
        from .._classification import _option_grammar
    else:
        from _classification import _option_grammar

    OptionArity = _option_grammar.OptionArity
    OptionDef = _option_grammar.OptionDef

_GIT_TABLES_REFERENCE_VERSION: tuple[int, int] = (2, 43)

_NONE = OptionArity.NONE
_REQUIRED = OptionArity.REQUIRED
_OPTIONAL = OptionArity.OPTIONAL


def _opt(
    name: str,
    arity: OptionArity = _NONE,
    *,
    short: str | None = None,
    negatable: bool = True,
) -> OptionDef:
    return OptionDef(name, short, arity, negatable, True)


def _short(letter: str, arity: OptionArity = _NONE) -> OptionDef:
    return OptionDef(letter, letter, arity, False, False)


_GIT_CHECKOUT_FLAG_SPEC = {
    row.name: row
    for row in (
        _short("b", _REQUIRED),
        _short("B", _REQUIRED),
        _short("l"),
        _opt("guess"),
        _opt("overlay"),
        _opt("quiet", short="q"),
        _opt("recurse-submodules", _OPTIONAL),
        _opt("progress"),
        _opt("merge", short="m"),
        _opt("conflict", _REQUIRED),
        _opt("detach", short="d"),
        _opt("track", _OPTIONAL, short="t"),
        _opt("force", short="f"),
        _opt("orphan", _REQUIRED),
        _opt("overwrite-ignore"),
        _opt("ignore-other-worktrees"),
        _opt("ours", short="2", negatable=False),
        _opt("theirs", short="3", negatable=False),
        _opt("patch", short="p"),
        _opt("ignore-skip-worktree-bits"),
        _opt("pathspec-from-file", _REQUIRED),
        _opt("pathspec-file-nul"),
    )
}

_GIT_SWITCH_FLAG_SPEC = {
    row.name: row
    for row in (
        _opt("create", _REQUIRED, short="c"),
        _opt("force-create", _REQUIRED, short="C"),
        _opt("guess"),
        _opt("discard-changes"),
        _opt("quiet", short="q"),
        _opt("recurse-submodules", _OPTIONAL),
        _opt("progress"),
        _opt("merge", short="m"),
        _opt("conflict", _REQUIRED),
        _opt("detach", short="d"),
        _opt("track", _OPTIONAL, short="t"),
        _opt("force", short="f"),
        _opt("orphan", _REQUIRED),
        _opt("overwrite-ignore"),
        _opt("ignore-other-worktrees"),
    )
}

_GIT_BRANCH_FLAG_SPEC = {
    row.name: row
    for row in (
        _opt("verbose", short="v"),
        _opt("quiet", short="q"),
        _opt("track", _OPTIONAL, short="t"),
        _opt("set-upstream"),
        _opt("set-upstream-to", _REQUIRED, short="u"),
        _opt("unset-upstream"),
        _opt("color", _OPTIONAL),
        _opt("remotes", short="r", negatable=False),
        _opt("contains", _OPTIONAL, negatable=False),
        _opt("no-contains", _OPTIONAL, negatable=False),
        _opt("with", _OPTIONAL, negatable=False),
        _opt("without", _OPTIONAL, negatable=False),
        _opt("abbrev", _OPTIONAL),
        _opt("all", short="a", negatable=False),
        _opt("delete", short="d"),
        _short("D"),
        _opt("move", short="m"),
        _short("M"),
        _opt("omit-empty"),
        _opt("copy", short="c"),
        _short("C"),
        _opt("list", short="l"),
        _opt("show-current"),
        _opt("create-reflog"),
        _opt("edit-description"),
        _opt("force", short="f"),
        _opt("merged", _OPTIONAL, negatable=False),
        _opt("no-merged", _OPTIONAL, negatable=False),
        _opt("column", _OPTIONAL),
        _opt("sort", _REQUIRED),
        _opt("points-at", _REQUIRED),
        _opt("ignore-case", short="i"),
        _opt("recurse-submodules"),
        _opt("format", _REQUIRED),
    )
}

_GIT_SYMBOLIC_REF_FLAG_SPEC = {
    row.name: row
    for row in (
        _opt("quiet", short="q"),
        _opt("delete", short="d"),
        _opt("short"),
        _opt("recurse"),
        _short("m", _REQUIRED),
    )
}

_GIT_UPDATE_REF_FLAG_SPEC = {
    row.name: row
    for row in (
        _short("m", _REQUIRED),
        _short("d"),
        _short("z"),
        _opt("no-deref", negatable=False),
        _opt("deref", negatable=False),
        _opt("stdin"),
        _opt("create-reflog"),
    )
}

_GIT_RESET_FLAG_SPEC = {
    row.name: row
    for row in (
        _opt("quiet", short="q"),
        _opt("no-refresh", negatable=False),
        _opt("refresh", negatable=False),
        _opt("mixed", negatable=False),
        _opt("soft", negatable=False),
        _opt("hard", negatable=False),
        _opt("merge", negatable=False),
        _opt("keep", negatable=False),
        _opt("recurse-submodules", _OPTIONAL),
        _opt("patch", short="p"),
        _opt("intent-to-add", short="N"),
        _opt("pathspec-from-file", _REQUIRED),
        _opt("pathspec-file-nul"),
    )
}

_GIT_PUSH_FLAG_SPEC = {
    row.name: row
    for row in (
        _opt("verbose", short="v"),
        _opt("quiet", short="q"),
        _opt("repo", _REQUIRED),
        _opt("all"),
        _opt("branches"),
        _opt("mirror"),
        _opt("delete", short="d"),
        _opt("tags"),
        _opt("dry-run", short="n"),
        _opt("porcelain"),
        _opt("force", short="f"),
        _opt("force-with-lease", _OPTIONAL),
        _opt("force-if-includes"),
        _opt("recurse-submodules", _REQUIRED),
        _opt("thin"),
        _opt("receive-pack", _REQUIRED),
        _opt("exec", _REQUIRED),
        _opt("set-upstream", short="u"),
        _opt("progress"),
        _opt("prune"),
        _opt("no-verify", negatable=False),
        _opt("verify", negatable=False),
        _opt("follow-tags"),
        _opt("signed", _OPTIONAL),
        _opt("atomic"),
        _opt("push-option", _REQUIRED, short="o"),
        _opt("ipv4", short="4", negatable=False),
        _opt("ipv6", short="6", negatable=False),
    )
}

_GIT_FETCH_FLAG_SPEC = {
    row.name: row
    for row in (
        _opt("verbose", short="v"),
        _opt("quiet", short="q"),
        _opt("all"),
        _opt("set-upstream"),
        _opt("append", short="a"),
        _opt("atomic"),
        _opt("upload-pack", _REQUIRED),
        _opt("force", short="f"),
        _opt("multiple", short="m"),
        _opt("tags", short="t"),
        _short("n"),
        _opt("jobs", _REQUIRED, short="j"),
        _opt("prefetch"),
        _opt("prune", short="p"),
        _opt("prune-tags", short="P"),
        _opt("recurse-submodules", _OPTIONAL),
        _opt("dry-run"),
        _opt("porcelain"),
        _opt("write-fetch-head"),
        _opt("keep", short="k"),
        _opt("update-head-ok", short="u"),
        _opt("progress"),
        _opt("depth", _REQUIRED),
        _opt("shallow-since", _REQUIRED),
        _opt("shallow-exclude", _REQUIRED),
        _opt("deepen", _REQUIRED),
        _opt("unshallow", negatable=False),
        _opt("refetch", negatable=False),
        _opt("submodule-prefix", _REQUIRED),
        _opt("recurse-submodules-default", _REQUIRED),
        _opt("update-shallow"),
        _opt("refmap", _REQUIRED, negatable=False),
        _opt("server-option", _REQUIRED, short="o"),
        _opt("ipv4", short="4", negatable=False),
        _opt("ipv6", short="6", negatable=False),
        _opt("negotiation-tip", _REQUIRED),
        _opt("negotiate-only"),
        _opt("filter", _REQUIRED),
        _opt("auto-maintenance"),
        _opt("auto-gc"),
        _opt("show-forced-updates"),
        _opt("write-commit-graph"),
        _opt("stdin"),
    )
}

_GIT_COMMIT_FLAG_SPEC = {
    row.name: row
    for row in (
        _opt("quiet", short="q"),
        _opt("verbose", short="v"),
        _opt("file", _REQUIRED, short="F"),
        _opt("author", _REQUIRED),
        _opt("date", _REQUIRED),
        _opt("message", _REQUIRED, short="m"),
        _opt("reedit-message", _REQUIRED, short="c"),
        _opt("reuse-message", _REQUIRED, short="C"),
        _opt("fixup", _REQUIRED),
        _opt("squash", _REQUIRED),
        _opt("reset-author"),
        _opt("trailer", _REQUIRED, negatable=False),
        _opt("signoff", short="s"),
        _opt("template", _REQUIRED, short="t"),
        _opt("edit", short="e"),
        _opt("cleanup", _REQUIRED),
        _opt("status"),
        _opt("gpg-sign", _OPTIONAL, short="S"),
        _opt("all", short="a"),
        _opt("include", short="i"),
        _opt("interactive"),
        _opt("patch", short="p"),
        _opt("only", short="o"),
        _opt("no-verify", short="n", negatable=False),
        _opt("verify", negatable=False),
        _opt("dry-run"),
        _opt("short"),
        _opt("branch"),
        _opt("ahead-behind"),
        _opt("porcelain"),
        _opt("long"),
        _opt("null", short="z"),
        _opt("amend"),
        _opt("no-post-rewrite", negatable=False),
        _opt("post-rewrite", negatable=False),
        _opt("untracked-files", _OPTIONAL, short="u"),
        _opt("pathspec-from-file", _REQUIRED),
        _opt("pathspec-file-nul"),
        _opt("allow-empty"),
        _opt("allow-empty-message"),
    )
}

_GIT_CLEAN_FLAG_SPEC = {
    row.name: row
    for row in (
        _opt("quiet", short="q"),
        _opt("dry-run", short="n"),
        _opt("force", short="f"),
        _opt("interactive", short="i"),
        _opt("exclude", _REQUIRED, short="e", negatable=False),
        _short("d"),
        _short("x"),
        _short("X"),
    )
}

_SED_FLAG_SPEC = {
    row.name: row
    for row in (
        _opt("quiet", short="n", negatable=False),
        _opt("silent", negatable=False),
        _short("E"),
        _opt("regexp-extended", short="r", negatable=False),
        _opt("separate", short="s", negatable=False),
        _opt("unbuffered", short="u", negatable=False),
        _opt("null-data", short="z", negatable=False),
        _opt("binary", short="b", negatable=False),
        _opt("posix", negatable=False),
        _opt("debug", negatable=False),
        _opt("sandbox", negatable=False),
        _opt("follow-symlinks", negatable=False),
        _opt("help", negatable=False),
        _opt("version", negatable=False),
        _opt("expression", _REQUIRED, short="e", negatable=False),
        _opt("file", _REQUIRED, short="f", negatable=False),
        _opt("line-length", _REQUIRED, short="l", negatable=False),
        _opt("in-place", _OPTIONAL, short="i", negatable=False),
    )
}

_GIT_SUBCOMMAND_TABLES: Mapping[str, Mapping[str, OptionDef]] = {
    "checkout": _GIT_CHECKOUT_FLAG_SPEC,
    "switch": _GIT_SWITCH_FLAG_SPEC,
    "branch": _GIT_BRANCH_FLAG_SPEC,
    "symbolic-ref": _GIT_SYMBOLIC_REF_FLAG_SPEC,
    "update-ref": _GIT_UPDATE_REF_FLAG_SPEC,
    "reset": _GIT_RESET_FLAG_SPEC,
    "push": _GIT_PUSH_FLAG_SPEC,
    "fetch": _GIT_FETCH_FLAG_SPEC,
    "commit": _GIT_COMMIT_FLAG_SPEC,
    "clean": _GIT_CLEAN_FLAG_SPEC,
}
