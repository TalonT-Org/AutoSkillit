"""Real-Git differential soundness of the guard's ref-effect classification.

A generated Git argv is classified by the guard's own machinery, run for real
in a repository with a primary and a linked worktree, and Git's observable
effect on every worktree is compared with what the classifier predicted.

Members: ``_build_base`` and ``_sandbox`` (fixture repositories),
``_run_differential`` (the shared driver returning a ``_Run``), ``_git_cases``
(argv strategy), the Property A section (shared refs) and the Property C
section (HEAD of the primary checkout).
"""

from __future__ import annotations

import contextlib
import os
import shlex
import shutil
import subprocess
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
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
)
from _git_target import (  # type: ignore[import-not-found]
    GitTargetKind,
    analyze_git_segment,
    command_channel_flags,
    resolve_git_target_for_path,
)
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


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run Git under the environment pinned by ``_pinned_git_environment``."""
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
        errors="replace",
        stdin=subprocess.DEVNULL,
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
def _git_cases(draw: st.DrawFn) -> _Case:
    """A subcommand with options and operands in any order and an optional ``--``.

    Three in four draws start from a well-formed skeleton and add at most one
    stray option; the rest are free-form combinations of table rows and operands.
    """
    subcommand = draw(st.sampled_from(_SUBCOMMANDS))
    table = _GIT_SUBCOMMAND_TABLES[subcommand]
    structured = draw(st.sampled_from((True, True, True, False)))
    if structured:
        rows, minimum, pools = draw(st.sampled_from(_SKELETONS[subcommand]))
        stray = 1
    else:
        rows, minimum, pools, stray = (), 0, _OPERAND_SLOTS[subcommand], 3
    operands = pools[: draw(st.integers(minimum, len(pools)))]
    items = [(False, [draw(st.sampled_from(pool))]) for pool in operands]
    groups = [
        draw(_spelled_row(subcommand, draw(st.sampled_from(alternatives.split("|")))))
        for alternatives in rows
    ]
    stray_option = st.sampled_from(sorted(table)).flatmap(
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
