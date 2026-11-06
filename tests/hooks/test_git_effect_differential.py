"""Real-Git differential soundness of the guard's ref-effect classification.

A generated Git argv is classified by the guard's own machinery, run for real
in a repository with a primary and a linked worktree, and Git's observable
effect on every worktree is compared with what the classifier predicted.

Members: ``_build_base`` and ``_sandbox`` (fixture repositories),
``_run_differential`` (the shared driver returning a ``_Run``), ``_git_cases``
(argv strategy), the Property A section (shared refs), the Property C section
(HEAD of the primary checkout) and the Property D section (destructive effects
on a dirty repository, with its own fixture, strategy and driver).
"""

from __future__ import annotations

import contextlib
import os
import shlex
import shutil
import subprocess
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, NamedTuple

import pytest

# The sys.path bootstrap for bare-name hook-sibling imports is centralized in
# tests/conftest.py -- it must run before this module's own top-level imports,
# which a fixture cannot do.
from _classification._option_grammar import (  # type: ignore[import-not-found]
    OptionArity,
    OptionDef,
)
from _command_classification import (  # type: ignore[import-not-found]
    extract_git_subcommand_and_flags,
    live_command_text,
)
from _git_command_classification import (  # type: ignore[import-not-found]
    _classify_git_invocation,
)
from _git_option_tables import _GIT_SUBCOMMAND_TABLES  # type: ignore[import-not-found]
from _git_outcome import (  # type: ignore[import-not-found]
    GitHeadEffect,
    GitRefOutcome,
    git_ref_outcome,
    risky_git_operation,
)
from _git_target import (  # type: ignore[import-not-found]
    GitTargetKind,
    analyze_git_segment,
    command_channel_flags,
    resolve_git_target_for_path,
)
from _hook_constants import RISKY_GIT_OPERATIONS  # type: ignore[import-not-found]
from _shell_segment_walk import walk_evaluated_segments  # type: ignore[import-not-found]
from hypothesis import HealthCheck, event, example, given, settings
from hypothesis import strategies as st

from autoskillit.hooks.guards import git_ops_guard
from tests._git_topology import commit_file, init_empty_checkout

pytestmark = [
    pytest.mark.layer("hooks"),
    pytest.mark.medium,
    pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed"),
]

_GIT_TIMEOUT_SECONDS = 30

# ---------------------------------------------------------------------------
# Repository fixtures
# ---------------------------------------------------------------------------

_STRIPPED_VARIABLES = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_TEST_DISALLOW_ABBREVIATED_OPTIONS",
)
_COMMIT_DATE = "2026-01-01T00:00:00+0000"


def _pinned_variables(home: Path) -> dict[str, str]:
    return {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_DATA_HOME": str(home / ".local" / "share"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "GIT_AUTHOR_NAME": "Differential Test",
        "GIT_AUTHOR_EMAIL": "differential@example.invalid",
        "GIT_AUTHOR_DATE": _COMMIT_DATE,
        "GIT_COMMITTER_NAME": "Differential Test",
        "GIT_COMMITTER_EMAIL": "differential@example.invalid",
        "GIT_COMMITTER_DATE": _COMMIT_DATE,
        "GIT_EDITOR": "true",
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
    }


@contextlib.contextmanager
def _pinned_git_environment(home: Path) -> Iterator[None]:
    """Pin the process environment every Git subprocess in this module inherits.

    The classifier's own Git queries and ``tests/_git_topology.py`` helpers read
    ``os.environ``, so the pinned environment must be the process environment,
    not a per-call dict.
    """
    with pytest.MonkeyPatch.context() as patch:
        for name in _STRIPPED_VARIABLES:
            patch.delenv(name, raising=False)
        for name, value in _pinned_variables(home).items():
            patch.setenv(name, value)
        yield


def _git(
    cwd: Path, *args: str, check: bool = True, stdin_text: str = ""
) -> subprocess.CompletedProcess[str]:
    """Run Git under the environment pinned by ``_pinned_git_environment``.

    Git reads ``stdin_text`` on stdin; the default is an immediate end of input.
    """
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
        errors="replace",
        input=stdin_text,
        timeout=_GIT_TIMEOUT_SECONDS,
    )


@dataclass(frozen=True, slots=True)
class _Base:
    tree: Path
    home: Path
    sha_a: str
    sha_b: str
    git_version: str


@dataclass(frozen=True, slots=True)
class _Sandbox:
    primary: Path
    linked: Path
    sha_a: str
    sha_b: str
    git_version: str

    @property
    def worktrees(self) -> dict[str, Path]:
        return {"primary": self.primary, "linked": self.linked}


def _build_base(root: Path) -> _Base:
    """Build the repository copied for every example: no linked worktree yet.

    ``develop`` holds commits A and B. ``feat`` diverges from A, ``other`` is
    ahead of ``develop`` and ``review`` sits at A, free to be checked out by the
    per-example linked worktree. ``y`` exists only on ``origin``, and
    ``origin/develop`` is stale behind the remote's ``develop``, so a fetch has
    something to move. The reflog records ``other`` as the previous branch, so
    ``-`` and ``@{-1}`` resolve.
    """
    home = root / "home"
    home.mkdir(parents=True)
    tree = root / "tree"
    with _pinned_git_environment(home):
        primary = init_empty_checkout(tree / "primary")
        _git(primary, "symbolic-ref", "HEAD", "refs/heads/develop")
        sha_a = commit_file(primary, "file.txt", "a\n", "A")
        sha_b = commit_file(primary, "d/x", "x\n", "B")
        _git(primary, "branch", "review", sha_a)
        _git(primary, "checkout", "-q", "-b", "feat", sha_a)
        commit_file(primary, "file.txt", "feat\n", "feat commit")
        _git(primary, "checkout", "-q", "-b", "other", "develop")
        commit_file(primary, "d/x", "other\n", "other commit")
        _git(primary, "checkout", "-q", "develop")
        (primary / "empty.lst").touch()
        # Git's defaults refuse pushes that update or delete a checked-out branch
        # and ignore push options; relaxing them lets `push .` actually rewrite a
        # checked-out ref, which is what the classifier must not miss.
        origin = tree / "origin.git"
        _git(tree, "clone", "-q", "--bare", str(primary), str(origin))
        for repo in (primary, origin):
            for key, value in (
                ("receive.denyCurrentBranch", "ignore"),
                ("receive.denyDeleteCurrent", "ignore"),
                ("receive.advertisePushOptions", "true"),
            ):
                _git(repo, "config", key, value)
        _git(origin, "branch", "y", sha_a)
        _git(primary, "remote", "add", "origin", str(origin))
        _git(primary, "fetch", "-q", "origin")
        _git(primary, "branch", "-q", "-u", "origin/develop", "develop")
        other_sha = _git(primary, "rev-parse", "other").stdout.strip()
        _git(origin, "update-ref", "refs/heads/develop", other_sha)
        git_version = _git(primary, "--version").stdout.strip()
    return _Base(tree, home, sha_a, sha_b, git_version)


@contextlib.contextmanager
def _sandbox(base: _Base, make_dir: Callable[[str], Path]) -> Iterator[_Sandbox]:
    """A private copy of the base with its own linked worktree on ``review``.

    The copied repository's remote and worktree links are absolute, so the
    remote is repointed and the linked worktree recreated; otherwise a mutation
    would leak into the shared base.
    """
    root = make_dir("differential")
    try:
        with _pinned_git_environment(base.home):
            shutil.copytree(base.tree, root, symlinks=True, dirs_exist_ok=True)
            primary = root / "primary"
            linked = root / "linked"
            _git(primary, "remote", "set-url", "origin", str(root / "origin.git"))
            _git(primary, "worktree", "add", "-q", str(linked), "review")
            yield _Sandbox(primary, linked, base.sha_a, base.sha_b, base.git_version)
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Driver: classify, run, observe
# ---------------------------------------------------------------------------

_SHA_A = "<A>"
_SHA_B = "<B>"

_Case = tuple[str, tuple[str, ...]]
_Tuples = tuple[tuple[str, str, bool], ...]
_RefValue = tuple[str, str]


class _View(NamedTuple):
    head_ref: str | None
    head_sha: str | None
    # None when the worktree is no longer a repository, e.g. after its HEAD is deleted.
    branches: Mapping[str, _RefValue] | None


def _observe_worktree(worktree: Path) -> _View:
    head_ref = _git(worktree, "symbolic-ref", "-q", "HEAD", check=False).stdout.strip()
    head_sha = _git(worktree, "rev-parse", "--verify", "-q", "HEAD", check=False).stdout.strip()
    listing = _git(
        worktree,
        "for-each-ref",
        "--format=%(refname)%09%(objectname)%09%(symref)",
        "refs/heads",
        check=False,
    )
    if listing.returncode:
        return _View(head_ref or None, head_sha or None, None)
    branches: dict[str, _RefValue] = {}
    for line in listing.stdout.splitlines():
        ref, sha, symref = line.split("\t")
        branches[ref] = (sha, symref)
    return _View(head_ref or None, head_sha or None, branches)


def _observe(sandbox: _Sandbox) -> dict[str, _View]:
    return {label: _observe_worktree(path) for label, path in sandbox.worktrees.items()}


@dataclass(frozen=True, slots=True)
class _Run:
    subcommand: str
    argv: tuple[str, ...]
    returncode: int
    stderr: str
    owners: tuple[str, ...]
    tuples: _Tuples
    outcome: GitRefOutcome | None
    before: Mapping[str, _View]
    after: Mapping[str, _View]
    git_version: str

    @property
    def command(self) -> str:
        return shlex.join(["git", self.subcommand, *self.argv])

    @property
    def named_refs(self) -> set[str]:
        return {target for target, _, _ in self.tuples}

    @property
    def ambiguous(self) -> bool:
        return any(flag for _, _, flag in self.tuples)

    @property
    def checked_out_ref_changes(self) -> dict[str, tuple[_RefValue | None, _RefValue | None]]:
        """Branches some worktree had checked out whose value changed or vanished."""
        changes: dict[str, tuple[_RefValue | None, _RefValue | None]] = {}
        for label, before in self.before.items():
            after = self.after[label]
            if before.branches is None or after.branches is None:
                continue
            for ref in self.owners:
                if ref not in changes and before.branches.get(ref) != after.branches.get(ref):
                    changes[ref] = (before.branches.get(ref), after.branches.get(ref))
        return changes

    @property
    def head_changes(self) -> list[tuple[str, _View, _View]]:
        """Worktrees whose HEAD name, attachment or identity changed."""
        return [
            (label, view, self.after[label])
            for label, view in self.before.items()
            if (view.head_ref, view.head_sha)
            != (self.after[label].head_ref, self.after[label].head_sha)
        ]


def _run_differential(sandbox: _Sandbox, subcommand: str, case_argv: tuple[str, ...]) -> _Run:
    """Classify ``git <subcommand> <argv>`` with the guard, then run it for real."""
    argv = tuple(
        token.replace(_SHA_A, sandbox.sha_a).replace(_SHA_B, sandbox.sha_b) for token in case_argv
    )
    cache: dict[Any, Any] = {}
    target = resolve_git_target_for_path(str(sandbox.primary), cache)
    assert target.kind == GitTargetKind.RESOLVED, f"no repository for {sandbox.primary}"
    context = git_ops_guard._context_for(target, cache)
    owners = context["owners"]
    assert isinstance(owners, dict)
    invocation = extract_git_subcommand_and_flags(["git", subcommand, *argv])
    assert invocation is not None, f"no git subcommand in {(subcommand, *argv)}"
    tuples = tuple(_classify_git_invocation(invocation, context))
    before = _observe(sandbox)
    completed = _git(sandbox.primary, subcommand, *argv, check=False)
    after = _observe(sandbox)
    return _Run(
        subcommand=subcommand,
        argv=argv,
        returncode=completed.returncode,
        stderr=completed.stderr,
        owners=tuple(sorted(owners)),
        tuples=tuples,
        outcome=git_ref_outcome(subcommand, argv),
        before=before,
        after=after,
        git_version=sandbox.git_version,
    )


# ---------------------------------------------------------------------------
# Argv strategy
# ---------------------------------------------------------------------------

_SUBCOMMANDS = (
    "checkout",
    "switch",
    "branch",
    "symbolic-ref",
    "update-ref",
    "reset",
    "push",
    "fetch",
)

_BRANCH_OPERANDS = (
    "develop",
    "feat",
    "other",
    "review",
    "y",
    _SHA_A,
    "HEAD~1",
    "file.txt",
    ".",
    "-",
    "@{-1}",
    "new-0",
    "new-1",
    "new-2",
    "refs/heads/feat",
    "HEAD:develop",
    "develop:develop",
)
_FULL_REFS = (
    "HEAD",
    "refs/heads/develop",
    "refs/heads/feat",
    "refs/heads/other",
    "refs/heads/review",
    "refs/heads/new-0",
)
_COMMITS = (_SHA_A, _SHA_B, "develop", "feat", "other", "HEAD", "HEAD~1")
_PATHS = ("file.txt", "d/x", ".")
_REMOTES = (".", "origin")
_REFSPECS = (
    "develop",
    "feat",
    "HEAD:develop",
    "develop:develop",
    "other:develop",
    "feat:review",
    "other:review",
    "+feat:develop",
    "+other:review",
    "+feat:review",
    f"{_SHA_A}:refs/heads/review",
    ":feat",
    "HEAD:refs/heads/new-0",
)
_OPERAND_SLOTS: Mapping[str, tuple[tuple[str, ...], ...]] = {
    "checkout": (_BRANCH_OPERANDS,) * 3,
    "switch": (_BRANCH_OPERANDS,) * 2,
    "branch": (_BRANCH_OPERANDS,) * 3,
    "symbolic-ref": (_FULL_REFS, _FULL_REFS),
    "update-ref": (_FULL_REFS, _COMMITS, _COMMITS),
    "reset": (_COMMITS, _PATHS, _PATHS),
    "push": (_REMOTES, _REFSPECS, _REFSPECS),
    "fetch": (_REMOTES, _REFSPECS, _REFSPECS),
}

_BRANCHES = ("develop", "feat", "other", "review", "y")
_NEW = ("new-0", "new-1", "new-2")
_START = (_SHA_A, _SHA_B, "feat", "other", "HEAD~1", "origin/y")
_TARGETS = (*_BRANCHES, "-", "@{-1}", "develop...other", "refs/heads/feat", _SHA_A, "HEAD~1")
_SYMREFS = ("refs/heads/develop", "refs/heads/feat", "refs/heads/review", "refs/heads/new-0")
_Skeleton = tuple[tuple[str, ...], int, tuple[tuple[str, ...], ...]]


def _sk(rows: str, minimum: int, *pools: tuple[str, ...]) -> _Skeleton:
    """A well-formed invocation shape: option rows to spell, then operand pools.

    ``rows`` lists space-separated table rows, each a ``|``-separated choice;
    between *minimum* and all of the pools supply operands.
    """
    return tuple(rows.split()), minimum, pools


# Most generated argv should be accepted by Git, because a rejected command
# changes nothing and proves nothing.
_SKELETONS: Mapping[str, tuple[_Skeleton, ...]] = {
    "checkout": (
        _sk("", 0, _TARGETS),
        _sk("b", 0, _START),
        _sk("B", 1, _START),
        _sk("detach", 0, _START),
        _sk("orphan", 0),
        _sk("b track", 1, ("origin/y", "origin/develop")),
        _sk("force|ignore-other-worktrees B", 1, _START),
        _sk("pathspec-from-file|force|merge|quiet", 0, _BRANCHES),
        _sk("ours|theirs|patch", 0, _PATHS),
        _sk("", 2, _START, _PATHS),
    ),
    "switch": (
        _sk("", 1, _TARGETS),
        _sk("create", 0, _START),
        _sk("force-create", 1, _START),
        _sk("detach", 0, _START),
        _sk("orphan", 0),
        _sk("track", 1, ("origin/y",)),
        _sk("force|ignore-other-worktrees|merge|guess", 1, _BRANCHES),
        _sk("force-create force", 1, _START),
    ),
    "branch": (
        _sk("move|M|copy|C", 1, _BRANCHES, _NEW),
        _sk("force move|M", 1, _BRANCHES, _NEW),
        _sk("force", 1, (*_BRANCHES, *_NEW), _START),
        _sk("", 1, _NEW, _START),
        _sk("delete|D", 1, _BRANCHES),
        _sk("list|all|remotes|verbose|show-current|merged|contains|quiet", 0, _BRANCHES),
        _sk("set-upstream-to|unset-upstream|edit-description", 0, _BRANCHES),
    ),
    "symbolic-ref": (
        _sk("", 2, ("HEAD",), _SYMREFS),
        _sk("m", 2, ("HEAD",), _SYMREFS),
        _sk("", 2, _SYMREFS, _SYMREFS),
        _sk("delete", 1, ("HEAD", *_SYMREFS)),
        _sk("quiet|short", 1, ("HEAD",)),
    ),
    "update-ref": (
        _sk("", 2, _FULL_REFS, _COMMITS, _COMMITS),
        _sk("m", 2, _FULL_REFS, _COMMITS),
        _sk("d", 1, _FULL_REFS, _COMMITS),
        _sk("no-deref|create-reflog", 2, ("HEAD",), _COMMITS),
    ),
    "reset": (
        _sk("soft|mixed|hard|keep|merge", 0, _COMMITS),
        _sk("", 0, _COMMITS),
        _sk("", 2, _COMMITS, _PATHS),
        _sk("patch|quiet|refresh", 0, _COMMITS),
    ),
    "push": (
        _sk("", 2, _REMOTES, _REFSPECS, _REFSPECS),
        _sk(
            "force|dry-run|set-upstream|atomic|porcelain|force-with-lease|quiet",
            2,
            _REMOTES,
            _REFSPECS,
        ),
        _sk("push-option force|follow-tags", 2, _REMOTES, _REFSPECS),
        _sk("delete", 2, _REMOTES, _BRANCHES),
        _sk("repo", 2, _REMOTES, _REFSPECS),
        _sk("all|tags|mirror|prune", 1, _REMOTES),
    ),
    "fetch": (
        _sk("", 0, _REMOTES, _REFSPECS, _REFSPECS),
        _sk("update-head-ok|force", 2, _REMOTES, _REFSPECS),
        _sk("refmap", 2, _REMOTES, ("develop", "feat", "review")),
        _sk("prune|tags|dry-run|all|quiet|verbose|atomic", 0, _REMOTES),
    ),
}

_NEW_BRANCHES = ("develop", "feat", "other", "review", "new-0", "new-1")
_REWRITTEN_BRANCHES = ("develop", "review", "feat", "other", "new-0")
_OPTION_VALUES: Mapping[str, tuple[str, ...]] = {
    "b": _NEW_BRANCHES,
    "B": _REWRITTEN_BRANCHES,
    "orphan": _NEW_BRANCHES,
    "create": _NEW_BRANCHES,
    "force-create": _REWRITTEN_BRANCHES,
    "conflict": ("merge", "diff3"),
    "track": ("direct", "inherit"),
    "recurse-submodules": ("no",),
    "pathspec-from-file": ("empty.lst",),
    "m": ("why",),
    "set-upstream-to": ("origin/develop",),
    "color": ("never",),
    "abbrev": ("7",),
    "column": ("never",),
    "sort": ("refname",),
    "points-at": ("HEAD",),
    "format": ("%(refname)",),
    "contains": ("HEAD", "feat"),
    "no-contains": ("HEAD", "feat"),
    "with": ("HEAD", "feat"),
    "without": ("HEAD", "feat"),
    "merged": ("HEAD", "feat"),
    "no-merged": ("HEAD", "feat"),
    "repo": _REMOTES,
    "force-with-lease": ("develop",),
    "signed": ("no",),
    "push-option": ("x",),
    "server-option": ("x",),
    "receive-pack": ("git-receive-pack",),
    "exec": ("git-receive-pack",),
    "upload-pack": ("git-upload-pack",),
    "jobs": ("1",),
    "depth": ("1",),
    "deepen": ("1",),
    "shallow-since": ("2000-01-01",),
    "shallow-exclude": ("develop",),
    "recurse-submodules-default": ("no",),
    "refmap": ("refs/heads/*:refs/heads/*", "refs/heads/feat:refs/heads/feat"),
    "negotiation-tip": ("HEAD",),
    "filter": ("blob:none",),
    "message": ("x", "amended"),
    "file": ("d/x",),
    "template": ("d/x",),
    "author": ("Other <other@example.invalid>",),
    "date": ("2000-01-01T00:00:00+0000",),
    "fixup": ("HEAD", "amend:HEAD", "reword:HEAD"),
    "squash": ("HEAD",),
    "reuse-message": ("HEAD",),
    "reedit-message": ("HEAD",),
    "cleanup": ("strip", "verbatim"),
    "trailer": ("Acked-by: x",),
    "untracked-files": ("no", "all"),
    "exclude": ("*.tmp", "untracked_dir"),
}


def _accepted_prefixes(table: Mapping[str, OptionDef], spelling: str) -> list[str]:
    """The abbreviations of *spelling* Git resolves to it: unique, or the exact name."""
    candidates = [row.name for row in table.values() if row.has_long]
    candidates += [f"no-{row.name}" for row in table.values() if row.has_long and row.negatable]
    return [
        spelling[:length]
        for length in range(1, len(spelling) + 1)
        if length == len(spelling)
        or sum(candidate.startswith(spelling[:length]) for candidate in candidates) == 1
    ]


def _short_forms(row: OptionDef, bundle: str, value: str) -> list[list[str]]:
    flag = f"-{bundle}{row.short}"
    if row.arity == OptionArity.NONE:
        return [[flag]]
    if row.arity == OptionArity.REQUIRED:
        return [[flag, value], [f"{flag}{value}"]]
    return [[flag], [f"{flag}{value}"]]


def _long_forms(row: OptionDef, spelling: str, negated: bool, value: str) -> list[list[str]]:
    flag = f"--{spelling}"
    if negated or row.arity == OptionArity.NONE:
        return [[flag]]
    if row.arity == OptionArity.REQUIRED:
        return [[f"{flag}={value}"], [flag, value]]
    return [[flag], [f"{flag}={value}"]]


def _short_categories(
    table: Mapping[str, OptionDef], row: OptionDef, value: str
) -> tuple[list[list[str]], list[list[str]]]:
    """Plain short spellings of *row*, and the same bundled behind boolean shorts."""
    if not row.short:
        return [], []
    bundled: list[list[str]] = []
    for other in table.values():
        if other.short and other.arity == OptionArity.NONE and other.short != row.short:
            bundled.extend(_short_forms(row, other.short, value))
    return _short_forms(row, "", value), bundled


def _long_categories(
    table: Mapping[str, OptionDef], row: OptionDef, value: str
) -> tuple[list[list[str]], list[list[str]], list[list[str]]]:
    """Full, abbreviated and ``--no-`` spellings of *row*'s long option."""
    full: list[list[str]] = []
    abbreviated: list[list[str]] = []
    negated: list[list[str]] = []
    if not row.has_long:
        return full, abbreviated, negated
    for spelling in _accepted_prefixes(table, row.name):
        (full if spelling == row.name else abbreviated).extend(
            _long_forms(row, spelling, False, value)
        )
    if row.negatable:
        for spelling in _accepted_prefixes(table, f"no-{row.name}"):
            negated.extend(_long_forms(row, spelling, True, value))
    return full, abbreviated, negated


def _spelling_categories(
    table: Mapping[str, OptionDef], row: OptionDef, value: str
) -> list[list[list[str]]]:
    """Argv spellings of *row* grouped by kind: short, bundled, long, abbreviated, negated."""
    short, bundled = _short_categories(table, row, value)
    full, abbreviated, negated = _long_categories(table, row, value)
    return [short, bundled, full, abbreviated, negated]


@st.composite
def _spelled_row(draw: st.DrawFn, subcommand: str, name: str) -> list[str]:
    table = _GIT_SUBCOMMAND_TABLES[subcommand]
    row = table[name]
    value = draw(st.sampled_from(_OPTION_VALUES.get(name, ("x",))))
    category = draw(st.sampled_from([c for c in _spelling_categories(table, row, value) if c]))
    return draw(st.sampled_from(category))


@st.composite
def _argv_cases(
    draw: st.DrawFn,
    subcommands: tuple[str, ...],
    slots: Mapping[str, tuple[tuple[str, ...], ...]],
    skeletons: Mapping[str, tuple[_Skeleton, ...]],
    strays: Mapping[str, tuple[str, ...]] | None = None,
) -> _Case:
    """A subcommand with options and operands in any order and an optional ``--``.

    Three in four draws start from a well-formed skeleton and add at most one
    stray option; the rest are free-form combinations of table rows and operands.
    Stray options come from the whole option table unless *strays* narrows them.
    """
    subcommand = draw(st.sampled_from(subcommands))
    table = _GIT_SUBCOMMAND_TABLES[subcommand]
    structured = draw(st.sampled_from((True, True, True, False)))
    if structured:
        rows, minimum, pools = draw(st.sampled_from(skeletons[subcommand]))
        stray = 1
    else:
        rows, minimum, pools, stray = (), 0, slots[subcommand], 3
    operands = pools[: draw(st.integers(minimum, len(pools)))]
    items = [(False, [draw(st.sampled_from(pool))]) for pool in operands]
    groups = [
        draw(_spelled_row(subcommand, draw(st.sampled_from(alternatives.split("|")))))
        for alternatives in rows
    ]
    stray_names = sorted(table) if strays is None else strays[subcommand]
    stray_option = st.sampled_from(stray_names).flatmap(
        lambda name: _spelled_row(subcommand, name)
    )
    groups += draw(st.lists(stray_option, max_size=stray))
    for group in groups:
        items.insert(draw(st.integers(0, len(items))), (True, group))
    if draw(st.sampled_from((False, False, True))):
        # Only operands may follow ``--``, so a skeleton keeps its options in front.
        floor = 1 + max((i for i, (is_option, _) in enumerate(items) if is_option), default=-1)
        items.insert(draw(st.integers(floor if structured else 0, len(items))), (False, ["--"]))
    return subcommand, tuple(token for _, group in items for token in group)


def _git_cases() -> st.SearchStrategy[_Case]:
    return _argv_cases(_SUBCOMMANDS, _OPERAND_SLOTS, _SKELETONS)


# ---------------------------------------------------------------------------
# Property A: shared refs
# ---------------------------------------------------------------------------

_HEAD_STABLE_EFFECTS = (GitHeadEffect.NONE, GitHeadEffect.PATH_RESTORE)
_OUTCOME_SUBCOMMANDS = ("branch", "checkout", "switch", "symbolic-ref")

_PINNED_CASES: tuple[_Case, ...] = (
    # Spellings that rewrite a branch ref (develop is checked out in the primary).
    ("checkout", ("-Bdevelop", _SHA_A)),
    ("checkout", ("-fB", "develop", _SHA_A)),
    ("checkout", ("--ignore-other-worktrees", "-Bdevelop")),
    ("checkout", ("--ignore-other-worktrees", "-Bdevelop", "feat")),
    ("checkout", ("feat", "-B", "develop")),
    ("checkout", (_SHA_A, "-B", "develop")),
    ("checkout", ("-B", "review", _SHA_B)),
    ("switch", ("-Cdevelop",)),
    ("switch", ("-C", "develop", "feat")),
    ("switch", ("-fC", "develop", "feat")),
    ("switch", ("--force-create=develop", "feat")),
    ("switch", ("--force-c", "develop", "feat")),
    ("switch", ("--force-create", "develop", "feat")),
    ("branch", ("-m", "develop", "renamed")),
    ("branch", ("-M", "develop", "renamed")),
    ("branch", ("--move", "review", "renamed")),
    ("branch", ("-fm", "feat", "renamed")),
    ("branch", ("-m", "renamed")),
    ("branch", ("-c", "feat", "copy")),
    ("branch", ("-f", "feat", _SHA_B)),
    ("branch", ("feat", _SHA_B, "-f")),
    ("branch", ("-fq", "feat")),
    ("branch", ("--forc", "feat", _SHA_B)),
    ("symbolic-ref", ("HEAD", "refs/heads/feat")),
    ("symbolic-ref", ("-q", "HEAD", "refs/heads/feat")),
    ("symbolic-ref", ("-m", "why", "HEAD", "refs/heads/feat")),
    ("symbolic-ref", ("-mwhy", "HEAD", "refs/heads/feat")),
    ("symbolic-ref", ("refs/heads/new-0", "refs/heads/feat")),
    ("update-ref", ("-dm", "why", "refs/heads/develop")),
    ("update-ref", ("-d", "refs/heads/review")),
    ("update-ref", ("refs/heads/review", _SHA_B)),
    ("update-ref", ("--no-deref", "HEAD", _SHA_A)),
    ("update-ref", ("-d", "--no-deref", "HEAD", _SHA_B)),
    ("update-ref", ("HEAD", _SHA_A)),
    ("reset", ("--hard", _SHA_A)),
    ("reset", ("--soft", "HEAD~1")),
    ("reset", ("--har", "other")),
    ("push", ("-o", "x", ".", "HEAD:develop")),
    ("push", ("-o", "x", ".", "other:develop")),
    ("push", (".", "other:review")),
    ("push", ("--repo=origin", ".", "other:develop")),
    ("push", ("-d", ".", "review")),
    ("fetch", ("-u", ".", "other:develop")),
    ("fetch", ("--update-head-ok", ".", "other:review")),
    ("fetch", ("-u", "origin", "develop:develop")),
    # Head effects with no rewrite of a checked-out ref.
    ("checkout", ("-b", "new-1", _SHA_A)),
    ("checkout", ("-fb", "new-1")),
    ("checkout", ("-bnew-1",)),
    ("checkout", ("--orphan", "new-1")),
    ("checkout", ("--orph=new-1",)),
    ("checkout", ("-d",)),
    ("checkout", ("--detach",)),
    ("checkout", ("-t", "origin/y")),
    ("checkout", ("-b", "new-1", "-t", "origin/y")),
    ("checkout", ("y",)),
    ("switch", ("y",)),
    ("checkout", ("develop", "--")),
    ("checkout", ("-",)),
    ("checkout", ("@{-1}",)),
    ("checkout", ("develop...other",)),
    ("checkout", ("other", "--pathspec-from-file=empty.lst")),
    ("checkout", ("--detach", "--no-detach", "other")),
    ("switch", ("-c", "new-1")),
    ("switch", ("-cnew-1",)),
    ("switch", ("--create=new-1",)),
    ("switch", ("feat",)),
    ("switch", ("-",)),
    ("switch", ("--detach",)),
    ("switch", ("-d",)),
    # NONE and PATH_RESTORE outcomes leave every HEAD and branch alone.
    ("checkout", ()),
    ("checkout", ("-f",)),
    ("checkout", ("-q",)),
    ("checkout", ("--", "file.txt")),
    ("checkout", ("develop", "--", "file.txt")),
    ("checkout", ("feat", "file.txt")),
    ("checkout", (".",)),
    ("checkout", (":/",)),
    ("checkout", ("-p",)),
    ("switch", ()),
    ("branch", ()),
    ("branch", ("-a",)),
    ("branch", ("--list",)),
    ("branch", ("-vv",)),
    ("branch", ("--show-current",)),
    ("branch", ("-d", "feat")),
    ("branch", ("-D", "feat")),
    ("branch", ("-D", "review")),
    ("branch", ("--contains",)),
    ("branch", ("--merged",)),
    ("branch", ("--no-merged",)),
    ("symbolic-ref", ("HEAD",)),
    ("reset", ("--", "file.txt")),
    ("reset", ("-p",)),
    ("fetch", ("origin",)),
    ("push", ("origin", "develop:develop")),
)


def _with_pinned_cases(test: Callable[..., None]) -> Callable[..., None]:
    for case in reversed(_PINNED_CASES):
        test = example(case=case)(test)
    return test


def _head_effect(run: _Run) -> GitHeadEffect | None:
    """The classifier's HEAD effect; ``None`` where no outcome concept applies.

    An absent outcome for the four outcome-classified subcommands means the argv
    rewrites no branch and has no HEAD effect.
    """
    if run.subcommand not in _OUTCOME_SUBCOMMANDS:
        return None
    return run.outcome.head_effect if run.outcome is not None else GitHeadEffect.NONE


def _describe_ref(value: _RefValue | None) -> str:
    if value is None:
        return "absent"
    sha, symref = value
    return f"symref {symref}" if symref else sha[:7]


def _describe_head(view: _View) -> str:
    sha = (view.head_sha or "unborn")[:7]
    return f"{view.head_ref or 'detached'}@{sha}"


def _shared_ref_violations(run: _Run) -> list[str]:
    """Where the classifier under-reports what Git did to shared refs.

    A checked-out branch that changed must be named by a tuple, or a tuple must
    be ambiguous. When the HEAD effect is NONE or PATH_RESTORE, no HEAD may
    change except that of a worktree sitting on a branch the command rewrites
    by name: renaming a checked-out branch rewrites that worktree's HEAD symref
    as part of the rewrite, which the named tuple already accounts for.
    """
    problems: list[str] = []
    if not run.ambiguous:
        for ref, (before, after) in run.checked_out_ref_changes.items():
            if ref not in run.named_refs:
                problems.append(
                    f"checked-out {ref} changed {_describe_ref(before)} -> "
                    f"{_describe_ref(after)} but is not named"
                )
    if _head_effect(run) in _HEAD_STABLE_EFFECTS:
        for label, before_view, after_view in run.head_changes:
            if before_view.head_ref not in run.named_refs:
                problems.append(
                    f"HEAD effect {_head_effect(run)} but {label} HEAD changed "
                    f"{_describe_head(before_view)} -> {_describe_head(after_view)}"
                )
    return problems


def _failure_message(run: _Run, problems: list[str]) -> str:
    return (
        f"Property A violated by `{run.command}` (exit {run.returncode})\n"
        "  expected: every checked-out branch that changed is named by a classifier tuple "
        "or a tuple is ambiguous; a NONE/PATH_RESTORE HEAD effect changes no HEAD\n"
        f"  actual: {'; '.join(problems)}\n"
        f"  classifier tuples: {list(run.tuples)}\n"
        f"  outcome: {run.outcome}\n"
        f"  git stderr: {run.stderr.strip()!r}\n"
        f"  {run.git_version}"
    )


def _over_block_label(run: _Run) -> str | None:
    """A low-cardinality label when the guard would deny although nothing changed."""
    denies = run.ambiguous or any(
        target in run.owners or target == "HEAD" for target in run.named_refs
    )
    if not denies or run.checked_out_ref_changes or run.head_changes:
        return None
    return f"over-block:{run.subcommand}:{'ambiguous' if run.ambiguous else 'names-ref'}"


def _assert_shared_refs_sound(run: _Run) -> None:
    event(f"git:{run.subcommand}:{'ok' if run.returncode == 0 else 'rejected'}")
    problems = _shared_ref_violations(run)
    assert not problems, _failure_message(run, problems)
    label = _over_block_label(run)
    if label is not None:
        event(label)


@pytest.fixture(scope="module")
def git_base(tmp_path_factory: pytest.TempPathFactory) -> _Base:
    return _build_base(tmp_path_factory.mktemp("differential-base"))


@pytest.mark.timeout(300)
@settings(max_examples=200, deadline=None)
@_with_pinned_cases
@given(case=_git_cases())
def test_classifier_accounts_for_shared_ref_effects(
    git_base: _Base, tmp_path_factory: pytest.TempPathFactory, case: _Case
) -> None:
    subcommand, argv = case
    with _sandbox(git_base, tmp_path_factory.mktemp) as sandbox:
        run = _run_differential(sandbox, subcommand, argv)
    _assert_shared_refs_sound(run)


# ---------------------------------------------------------------------------
# Property C: HEAD of the primary checkout
# ---------------------------------------------------------------------------

_HEAD_MOVING_SUBCOMMANDS = ("checkout", "switch")

# Spellings that only restore paths, so the primary keeps what it has checked out.
_PATH_ONLY_CASES: tuple[_Case, ...] = (
    ("checkout", ("--", "file.txt")),
    ("checkout", ("other", "--", "file.txt")),
    ("checkout", ("other", "file.txt")),
    ("checkout", (".",)),
    ("checkout", ("--ours", "file.txt")),
    ("checkout", ("-p",)),
    ("checkout", ("-f",)),
)

# Spellings that change the primary's HEAD name, attachment or identity.
_HEAD_MOVING_CASES: tuple[_Case, ...] = (
    ("checkout", ("-b", "new-1", _SHA_A)),
    ("checkout", ("-Bdevelop", _SHA_A)),
    ("checkout", ("--detach",)),
    ("checkout", ("--orphan", "new-1")),
    ("checkout", ("-",)),
    ("checkout", ("@{-1}",)),
    ("checkout", ("other",)),
    ("checkout", (_SHA_A,)),
    ("checkout", ("-t", "origin/y")),
    ("switch", ("other",)),
    ("switch", ("-c", "new-1")),
    ("switch", ("-Cdevelop", "feat")),
    ("switch", ("--detach", _SHA_A)),
    ("switch", ("-",)),
    ("switch", ("y",)),
)


def _with_primary_head_examples(test: Callable[..., None]) -> Callable[..., None]:
    for case in reversed(_PATH_ONLY_CASES + _HEAD_MOVING_CASES):
        test = example(case=case)(test)
    return test


def _primary_head_verdict(run: _Run, primary: Path) -> str | None:
    """The primary-head rule's verdict on the command *run* executed, issued from *primary*."""
    walked = walk_evaluated_segments(run.command, str(primary))
    assert walked is not None, f"`{run.command}` could not be tokenized ({run.git_version})"
    flags = command_channel_flags(live_command_text(run.command))
    cache: dict[Any, Any] = {}
    analysed = [analyze_git_segment(segment, flags, cache) for segment in walked]
    facts = [segment_facts for segment_facts in analysed if segment_facts is not None]
    assert len(facts) == 1, f"`{run.command}` classified {len(facts)} Git segments"
    anchor = git_ops_guard._lazy_anchor(primary, cache)
    verdict: str | None = git_ops_guard._primary_head_violation(facts[0], anchor)
    return verdict


def _primary_head_change(run: _Run) -> tuple[_View, _View] | None:
    for label, before, after in run.head_changes:
        if label == "primary":
            return before, after
    return None


def _primary_head_problems(run: _Run, verdict: str | None) -> list[str]:
    problems: list[str] = []
    change = _primary_head_change(run)
    if change is not None and verdict is None:
        problems.append(
            f"primary HEAD changed {_describe_head(change[0])} -> {_describe_head(change[1])} "
            "but the rule allows it"
        )
    if (run.subcommand, run.argv) in _PATH_ONLY_CASES and verdict is not None:
        problems.append(f"a path-only spelling is denied as {verdict!r}")
    return problems


def _primary_head_failure_message(run: _Run, verdict: str | None, problems: list[str]) -> str:
    return (
        f"Property C violated by `{run.command}` (exit {run.returncode})\n"
        "  expected: a checkout or switch that changed the primary's HEAD is denied, and "
        "a path-only spelling is allowed\n"
        f"  actual: {'; '.join(problems)}\n"
        f"  rule verdict: {verdict!r}\n"
        f"  outcome: {run.outcome}\n"
        f"  git stderr: {run.stderr.strip()!r}\n"
        f"  {run.git_version}"
    )


@pytest.mark.timeout(300)
@settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.filter_too_much])
@_with_primary_head_examples
@given(case=_git_cases().filter(lambda case: case[0] in _HEAD_MOVING_SUBCOMMANDS))
def test_primary_head_rule_denies_every_checkout_and_switch_that_moves_the_primary_head(
    git_base: _Base, tmp_path_factory: pytest.TempPathFactory, case: _Case
) -> None:
    subcommand, argv = case
    with _sandbox(git_base, tmp_path_factory.mktemp) as sandbox:
        run = _run_differential(sandbox, subcommand, argv)
        verdict = _primary_head_verdict(run, sandbox.primary)
    event(
        f"git:{run.subcommand}:{'moved' if _primary_head_change(run) else 'kept'}:"
        f"{'denied' if verdict else 'allowed'}"
    )
    problems = _primary_head_problems(run, verdict)
    assert not problems, _primary_head_failure_message(run, verdict, problems)


# ---------------------------------------------------------------------------
# Property D: destructive effects
# ---------------------------------------------------------------------------

_EDITED_PATHS = ("file.txt", "d/x", "local.txt", "hidden/h.txt")
_EARLIER_COMMITTER_DATE = "2025-06-01T00:00:00+0000"


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _build_destructive_base(base: _Base, root: Path) -> _Base:
    """A copy of *base* holding the state a destructive command can destroy.

    ``develop`` gains a local commit written with an earlier committer date than
    any later command uses, so it diverges from ``origin/develop`` and an amend
    always writes a different commit. Four tracked files carry unstaged edits;
    ``hidden/h.txt`` is named by no argv pool, so only a whole-tree operand can
    revert every edit. The untracked entries are a file, a directory holding a
    file, an empty directory, a nested repository and an ignored file.
    """
    tree = root / "tree"
    shutil.copytree(base.tree, tree, symlinks=True)
    primary = tree / "primary"
    with _pinned_git_environment(base.home):
        _git(primary, "remote", "set-url", "origin", str(tree / "origin.git"))
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv("GIT_COMMITTER_DATE", _EARLIER_COMMITTER_DATE)
            _write(primary / "local.txt", "local\n")
            _write(primary / "hidden" / "h.txt", "hidden\n")
            _git(primary, "add", "local.txt", "hidden/h.txt")
            _git(primary, "commit", "-q", "-m", "local")
        _git(primary, "fetch", "-q", "origin")
        for relpath in _EDITED_PATHS:
            path = primary / relpath
            _write(path, path.read_text(encoding="utf-8") + "edited\n")
        _write(primary / "scratch.tmp", "scratch\n")
        _write(primary / "untracked_dir" / "inner.txt", "inner\n")
        (primary / "empty_dir").mkdir()
        _write(primary / "ignored.log", "ignored\n")
        with (primary / ".git" / "info" / "exclude").open("a", encoding="utf-8") as exclude:
            exclude.write("*.log\n")
        _git(primary, "init", "-q", "--template=", "nested")
        _write(primary / "nested" / "n.txt", "nested\n")
    return _Base(tree, base.home, base.sha_a, base.sha_b, base.git_version)


class _Snapshot(NamedTuple):
    head_ref: str
    head: str
    parent: str
    edits: Mapping[str, bytes | None]
    untracked: frozenset[str]
    disk: frozenset[str]
    remote: Mapping[str, str]


def _disk_entries(worktree: Path) -> frozenset[str]:
    entries: set[str] = set()
    for directory, names, files in os.walk(worktree):
        here = Path(directory)
        if here == worktree and ".git" in names:
            names.remove(".git")
        entries.update((here / name).relative_to(worktree).as_posix() for name in (*names, *files))
    return frozenset(entries)


def _tracked_entries(worktree: Path) -> frozenset[str]:
    """Every tracked path and each directory that contains one."""
    listing = _git(worktree, "ls-files", "-z", check=False).stdout
    tracked: set[str] = set()
    for relpath in filter(None, listing.split("\0")):
        tracked.add(relpath)
        tracked.update(
            parent.as_posix() for parent in PurePosixPath(relpath).parents if parent.name
        )
    return frozenset(tracked)


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def _remote_heads(origin: Path) -> dict[str, str]:
    listing = _git(origin, "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads")
    return dict(line.split(" ", 1) for line in listing.stdout.splitlines())


def _snapshot(sandbox: _Sandbox) -> _Snapshot:
    primary = sandbox.primary
    disk = _disk_entries(primary)
    return _Snapshot(
        head_ref=_git(primary, "symbolic-ref", "-q", "HEAD", check=False).stdout.strip(),
        head=_git(primary, "rev-parse", "--verify", "-q", "HEAD", check=False).stdout.strip(),
        parent=_git(primary, "rev-parse", "--verify", "-q", "HEAD^", check=False).stdout.strip(),
        edits={relpath: _read_bytes(primary / relpath) for relpath in _EDITED_PATHS},
        untracked=disk - _tracked_entries(primary),
        disk=disk,
        remote=_remote_heads(primary.parent / "origin.git"),
    )


def _rewritten_non_fast_forward(
    origin: Path, before: Mapping[str, str], after: Mapping[str, str]
) -> bool:
    """Whether a branch now points at a commit that does not contain its old tip."""
    for ref, old in before.items():
        new = after.get(ref)
        if new is None or new == old:
            continue
        if _git(origin, "merge-base", "--is-ancestor", old, new, check=False).returncode:
            return True
    return False


def _observed_effect(
    origin: Path, subcommand: str, before: _Snapshot, after: _Snapshot
) -> str | None:
    """The destructive effect named after *subcommand*, when Git had it between the snapshots.

    An amend replaces the commit the current branch ends in. A hard reset reverts
    some edit; a whole-tree restore reverts every edit. A clean removes some
    untracked entry. A force push moves a remote branch to a commit that does not
    contain its old tip.
    """
    reverted = sum(after.edits[relpath] != before.edits[relpath] for relpath in _EDITED_PATHS)
    if subcommand == "commit":
        replaced = (
            after.head_ref == before.head_ref
            and after.head != before.head
            and after.parent == before.parent
        )
        return "amend" if replaced else None
    if subcommand == "reset":
        return "hard-reset" if reverted else None
    if subcommand == "checkout":
        return "whole-tree-restore" if reverted == len(_EDITED_PATHS) else None
    if subcommand == "clean":
        return "clean" if before.untracked - after.disk else None
    if subcommand == "push":
        rewritten = _rewritten_non_fast_forward(origin, before.remote, after.remote)
        return "force-push" if rewritten else None
    return None


@dataclass(frozen=True, slots=True)
class _DestructiveRun:
    subcommand: str
    argv: tuple[str, ...]
    returncode: int
    stderr: str
    effect: str | None
    git_version: str

    @property
    def command(self) -> str:
        return shlex.join(["git", self.subcommand, *self.argv])


def _run_destructive(
    sandbox: _Sandbox, subcommand: str, argv: tuple[str, ...], *, require_force: bool
) -> _DestructiveRun:
    """Run ``git <subcommand> <argv>`` in the dirty primary and name its destructive effect."""
    if not require_force:
        _git(sandbox.primary, "config", "clean.requireForce", "false")
    before = _snapshot(sandbox)
    # An interactive clean takes its menu answer from stdin, and ``c`` cleans.
    stdin_text = "c\n" if subcommand == "clean" else ""
    completed = _git(sandbox.primary, subcommand, *argv, check=False, stdin_text=stdin_text)
    after = _snapshot(sandbox)
    effect = _observed_effect(sandbox.primary.parent / "origin.git", subcommand, before, after)
    return _DestructiveRun(
        subcommand, argv, completed.returncode, completed.stderr, effect, sandbox.git_version
    )


_DIRTY_SUBCOMMANDS = ("commit", "reset", "clean", "checkout", "push")

_DIRTY_REVISIONS = (
    "HEAD",
    "HEAD~",
    "HEAD~1",
    "HEAD~2",
    "develop",
    "feat",
    "other",
    "origin/develop",
)
_DIRTY_TRACKED = ("file.txt", "d/x", "d", "local.txt")
_WHOLE_TREE_SPELLINGS = (".", "./", ":/", ":(top)", ":(top).", "*", "d/..", ":(top)*")
_DIRTY_RESTORE = (*_DIRTY_TRACKED, *_WHOLE_TREE_SPELLINGS)
_DIRTY_UNTRACKED = (
    "scratch.tmp",
    "untracked_dir",
    "empty_dir",
    "nested",
    "ignored.log",
    ".",
    "file.txt",
)
_ORIGIN = ("origin",)
_DIRTY_REFSPECS = (
    "develop",
    "feat",
    "other",
    "HEAD",
    "develop:develop",
    "HEAD:develop",
    "feat:develop",
    "other:develop",
    "develop:other",
    "feat:other",
    "develop:feat",
    "other:feat",
    "feat:review",
    "develop:y",
    "+develop",
    "+feat",
    "+develop:develop",
    "+feat:develop",
    "+HEAD:develop",
    "+develop:other",
    "+feat:other",
    "+develop:feat",
    "+other:feat",
    "+other:review",
    "+feat:y",
)

# A revision operand of checkout always has a path after it, so no generated
# checkout switches branches: a forced switch discards edits as well, but it is
# not a whole-tree restore.
_DIRTY_SLOTS: Mapping[str, tuple[tuple[str, ...], ...]] = {
    "commit": (_DIRTY_TRACKED,) * 2,
    "reset": (_DIRTY_REVISIONS, _DIRTY_TRACKED, _DIRTY_TRACKED),
    "clean": (_DIRTY_UNTRACKED,) * 2,
    "checkout": (_DIRTY_RESTORE,) * 3,
    "push": (_ORIGIN, _DIRTY_REFSPECS, _DIRTY_REFSPECS),
}

_DIRTY_SKELETONS: Mapping[str, tuple[_Skeleton, ...]] = {
    "commit": (
        _sk("amend", 0, _DIRTY_TRACKED),
        _sk("amend message", 0, _DIRTY_TRACKED),
        _sk("amend all", 0),
        _sk("amend edit|reset-author|signoff|allow-empty|quiet|date|author|dry-run", 0),
        _sk("all message", 0),
        _sk("allow-empty message", 0),
        _sk("message", 1, _DIRTY_TRACKED, _DIRTY_TRACKED),
        _sk("fixup|squash|reuse-message|reedit-message|template|cleanup all", 0),
    ),
    "reset": (
        _sk("hard", 0, _DIRTY_REVISIONS),
        _sk("hard soft|mixed|merge|keep", 0, _DIRTY_REVISIONS),
        _sk("soft|mixed|merge|keep", 0, _DIRTY_REVISIONS),
        _sk("", 0, _DIRTY_REVISIONS),
        _sk("", 2, _DIRTY_REVISIONS, _DIRTY_TRACKED, _DIRTY_TRACKED),
        _sk("patch|quiet|refresh|intent-to-add", 0, _DIRTY_REVISIONS),
    ),
    "clean": (
        _sk("", 0, _DIRTY_UNTRACKED, _DIRTY_UNTRACKED),
        _sk("force", 0, _DIRTY_UNTRACKED, _DIRTY_UNTRACKED),
        _sk("force d", 0, _DIRTY_UNTRACKED, _DIRTY_UNTRACKED),
        _sk("force x|X", 0, _DIRTY_UNTRACKED, _DIRTY_UNTRACKED),
        _sk("force d x|X", 0, _DIRTY_UNTRACKED, _DIRTY_UNTRACKED),
        _sk("d|x|X", 0, _DIRTY_UNTRACKED, _DIRTY_UNTRACKED),
        _sk("force force d", 0, _DIRTY_UNTRACKED, _DIRTY_UNTRACKED),
        _sk("force dry-run|interactive|quiet", 0, _DIRTY_UNTRACKED, _DIRTY_UNTRACKED),
        _sk("force exclude", 0, _DIRTY_UNTRACKED, _DIRTY_UNTRACKED),
        _sk("dry-run d|x", 0, _DIRTY_UNTRACKED, _DIRTY_UNTRACKED),
    ),
    "checkout": (
        _sk("force", 0, _DIRTY_RESTORE, _DIRTY_RESTORE),
        _sk("force|quiet|merge|overlay|ours|theirs|patch", 0, _DIRTY_RESTORE, _DIRTY_RESTORE),
        _sk("", 1, _DIRTY_RESTORE, _DIRTY_RESTORE, _DIRTY_RESTORE),
        _sk("", 2, _DIRTY_REVISIONS, _DIRTY_RESTORE, _DIRTY_RESTORE),
        _sk("force", 2, _DIRTY_REVISIONS, _DIRTY_RESTORE),
    ),
    "push": (
        _sk("", 1, _ORIGIN, _DIRTY_REFSPECS, _DIRTY_REFSPECS),
        _sk("force", 0, _ORIGIN, _DIRTY_REFSPECS, _DIRTY_REFSPECS),
        _sk("force set-upstream", 1, _ORIGIN, _DIRTY_REFSPECS),
        _sk("force-with-lease", 0, _ORIGIN, _DIRTY_REFSPECS, _DIRTY_REFSPECS),
        _sk("force-with-lease force-if-includes", 1, _ORIGIN, _DIRTY_REFSPECS),
        _sk(
            "force dry-run|atomic|porcelain|quiet|verbose|follow-tags|push-option",
            1,
            _ORIGIN,
            _DIRTY_REFSPECS,
        ),
        _sk(
            "set-upstream|atomic|quiet|follow-tags|verify|force-if-includes",
            1,
            _ORIGIN,
            _DIRTY_REFSPECS,
        ),
        _sk("all|tags|mirror|prune|branches", 1, _ORIGIN),
        _sk("all|mirror force|force-with-lease", 1, _ORIGIN),
    ),
}

# Commit, reset, clean and push may take any option of their table except those
# that wait for a terminal, sign, read a pathspec file or redirect the push away
# from origin. Checkout takes only the options that restore paths or force a
# restore: those that create, detach or switch branches stay out.
_DIRTY_STRAYS: Mapping[str, tuple[str, ...]] = {
    "commit": tuple(
        name
        for name in sorted(_GIT_SUBCOMMAND_TABLES["commit"])
        if name
        not in ("interactive", "patch", "gpg-sign", "pathspec-from-file", "pathspec-file-nul")
    ),
    "reset": tuple(
        name
        for name in sorted(_GIT_SUBCOMMAND_TABLES["reset"])
        if name not in ("pathspec-from-file", "pathspec-file-nul")
    ),
    "clean": tuple(sorted(_GIT_SUBCOMMAND_TABLES["clean"])),
    "checkout": (
        "force",
        "quiet",
        "merge",
        "overlay",
        "ours",
        "theirs",
        "patch",
        "guess",
        "progress",
        "ignore-other-worktrees",
        "recurse-submodules",
    ),
    "push": tuple(
        name
        for name in sorted(_GIT_SUBCOMMAND_TABLES["push"])
        if name not in ("repo", "receive-pack", "exec", "signed")
    ),
}


def _dirty_cases() -> st.SearchStrategy[_Case]:
    return _argv_cases(_DIRTY_SUBCOMMANDS, _DIRTY_SLOTS, _DIRTY_SKELETONS, _DIRTY_STRAYS)


# Spellings of a destructive command, each with a state in which Git acts on it
# or refuses; the classifier must flag every one that acted.
_DESTRUCTIVE_CASES: tuple[_Case, ...] = (
    ("commit", ("--amend",)),
    ("commit", ("--amen",)),
    ("commit", ("--am", "--no-edit")),
    ("commit", ("--amend", "-m", "amended")),
    ("commit", ("-a", "--amend")),
    ("commit", ("--amend", "d/x")),
    ("commit", ("--amend", "--no-amend", "-a", "-m", "x")),
    ("commit", ("-a", "-m", "--amend")),
    ("commit", ("-a", "-m", "x", "--no-amend")),
    ("commit", ("-a", "-m", "x")),
    ("commit", ("--allow-empty", "-m", "x")),
    ("commit", ("-m", "x")),
    ("push", ("-fu", "origin", "develop")),
    ("push", ("-qf", "origin", "develop")),
    ("push", ("-f", "origin", "develop")),
    ("push", ("origin", "develop", "-f")),
    ("push", ("--force-w", "origin", "develop")),
    ("push", ("--force-with-lease", "origin", "develop")),
    ("push", ("--force-with-lease=develop", "origin", "develop")),
    ("push", ("origin", "+develop")),
    ("push", ("origin", "+HEAD:develop")),
    ("push", ("origin", "+feat:develop")),
    ("push", ("-f",)),
    ("push", ("--forc", "origin", "develop")),
    ("push", ("origin", "develop")),
    ("push", ("-u", "origin", "develop")),
    ("push", ("--force-if-includes", "origin", "develop")),
    ("push", ("--recurse-submodules=check", "origin", "develop")),
    ("push", ("origin", "+other:review")),
    ("push", ("-n", "-f", "origin", "develop")),
    ("push", ("origin", "feat:review")),
    ("push", ("--mirror", "origin")),
    ("push", ("origin", "--mirror")),
    ("push", ("--all", "origin")),
    ("push", ("--all", "-f", "origin")),
    ("reset", ("--hard", "HEAD~")),
    ("reset", ("--hard",)),
    ("reset", ("--har", "HEAD~")),
    ("reset", ("--h", "HEAD~")),
    ("reset", ("--hard", "HEAD~2")),
    ("reset", ("--hard", "other")),
    ("reset", ("--soft", "--hard", "HEAD~")),
    ("reset", ("--hard", "--soft", "HEAD~")),
    ("reset", ("--hard-foo",)),
    ("reset", ("--soft", "HEAD~")),
    ("reset", ("--mixed", "HEAD~")),
    ("reset", ("--merge", "HEAD~")),
    ("reset", ("--keep", "HEAD~")),
    ("reset", ("--", "file.txt")),
    ("clean", ("--forc",)),
    ("clean", ("-f",)),
    ("clean", ("-df",)),
    ("clean", ("-fd",)),
    ("clean", ("-xdf",)),
    ("clean", ("-fx",)),
    ("clean", ("-fX",)),
    ("clean", ("-ff",)),
    ("clean", ("-ffd",)),
    ("clean", ("-dff",)),
    ("clean", ("-f", "-e", "*.tmp")),
    ("clean", ("-f", "scratch.tmp")),
    ("clean", ("-f", "file.txt")),
    ("clean", ("-d",)),
    ("clean", ("-n",)),
    ("clean", ("-nd",)),
    ("clean", ("--dry-run", "-d")),
    ("clean", ("-nf",)),
    ("clean", ("-i",)),
    ("clean", ("-fi",)),
    ("clean", ("-f", "-n", "--no-dry-run")),
    ("checkout", ("-f",)),
    ("checkout", ("--force",)),
    ("checkout", ("--forc",)),
    ("checkout", ("-f", "--")),
    ("checkout", ("--",)),
    ("checkout", (".",)),
    ("checkout", ("./",)),
    ("checkout", (":/",)),
    ("checkout", (":(top)",)),
    ("checkout", (":(top).",)),
    ("checkout", ("--", ".")),
    ("checkout", ("--", "./")),
    ("checkout", ("--", ":/")),
    ("checkout", ("--", "*")),
    ("checkout", ("--", "d/..")),
    ("checkout", ("--", ":(top)*")),
    ("checkout", ("*",)),
    ("checkout", (".", "--")),
    ("checkout", (".", "origin", "main")),
    ("checkout", ("HEAD", ".")),
    ("checkout", ("feat", ".")),
    ("checkout", ("HEAD", "--", ".")),
    ("checkout", ("-f", ".")),
    ("checkout", ("-q", "--", ".")),
    ("checkout", ("--", "file.txt")),
    ("checkout", ("--", "d")),
    ("checkout", ("file.txt",)),
    ("checkout", ("HEAD", "file.txt")),
    ("checkout", ("-q",)),
    ("checkout", ("feat",)),
)

# Cleans that act without `-f` because the repository's configuration waives the
# force requirement.
_UNFORCED_CLEAN_CASES: tuple[_Case, ...] = (
    ("clean", ("-d",)),
    ("clean", ("-x",)),
    ("clean", ("-X",)),
    ("clean", ()),
    ("clean", ("-dx",)),
    ("clean", ("-n",)),
    ("clean", ("-i",)),
)


def _with_destructive_examples(test: Callable[..., None]) -> Callable[..., None]:
    for case in reversed(_UNFORCED_CLEAN_CASES):
        test = example(case=case, require_force=False)(test)
    for case in reversed(_DESTRUCTIVE_CASES):
        test = example(case=case, require_force=True)(test)
    return test


def _destructive_failure_message(run: _DestructiveRun, risky: tuple[str, ...] | None) -> str:
    return (
        f"Property D violated by `{run.command}` (exit {run.returncode})\n"
        "  expected: a destructive effect Git produced is named by "
        "risky_git_operation as a blocked operation\n"
        f"  actual: effect {run.effect!r} observed but risky_git_operation returned {risky!r}\n"
        f"  git stderr: {run.stderr.strip()!r}\n"
        f"  {run.git_version}"
    )


@pytest.fixture(scope="module")
def destructive_base(git_base: _Base, tmp_path_factory: pytest.TempPathFactory) -> _Base:
    return _build_destructive_base(git_base, tmp_path_factory.mktemp("destructive-base"))


@pytest.mark.timeout(300)
@settings(max_examples=200, deadline=None)
@_with_destructive_examples
@given(case=_dirty_cases(), require_force=st.booleans())
def test_risky_operation_is_detected_for_every_destructive_effect(
    destructive_base: _Base,
    tmp_path_factory: pytest.TempPathFactory,
    case: _Case,
    require_force: bool,
) -> None:
    subcommand, argv = case
    with _sandbox(destructive_base, tmp_path_factory.mktemp) as sandbox:
        run = _run_destructive(sandbox, subcommand, argv, require_force=require_force)
    risky = risky_git_operation(subcommand, argv, RISKY_GIT_OPERATIONS)
    event(f"git:{subcommand}:{'ok' if run.returncode == 0 else 'rejected'}")
    event(f"effect:{subcommand}:{run.effect or 'none'}")
    if run.effect is not None:
        assert risky is not None, _destructive_failure_message(run, risky)
    elif risky is not None:
        event(f"over-block-d:{subcommand}")
