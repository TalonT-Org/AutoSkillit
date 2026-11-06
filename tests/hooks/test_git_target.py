"""Target and identity analysis of walked Git segments (hooks/_runtime/_git_target.py).

Members: checkout, linked-worktree, ``.git`` and bare-repository targets, the
non-repository target, unproven-cwd and ``-C`` rows, layout-override and
unparsed-global-option rows, unreadable Git output, dynamic command and
subcommand identity, the alias channel, alias expansion, ``git-<builtin>`` and
external commands, ``resolve_git_target_for_path`` and the builtin/mutation
subcommand sets.

Every row analyses a whole segment produced by ``walk_evaluated_segments``, in
real repositories under ``tmp_path_factory`` with Git configuration isolated
from the host. No row runs the analysed command; the analysis only queries
``rev-parse`` and ``config --get``.
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The sys.path bootstrap for bare-name hook-sibling imports is centralized in
# tests/conftest.py.
import _git_target  # type: ignore[import-not-found]
import pytest
from _command_classification import live_command_text  # type: ignore[import-not-found]
from _git_outcome import GitHeadEffect  # type: ignore[import-not-found]
from _git_target import (  # type: ignore[import-not-found]
    _GIT_BUILTIN_SUBCOMMANDS,
    _GIT_MUTATION_SUBCOMMANDS,
    GitSegmentFacts,
    GitTarget,
    GitTargetKind,
    analyze_git_segment,
    command_channel_flags,
    resolve_git_target_for_path,
)
from _shell_segment_walk import walk_evaluated_segments  # type: ignore[import-not-found]

from tests._git_topology import add_linked_worktree, commit_file, init_checkout

pytestmark = [
    pytest.mark.layer("hooks"),
    pytest.mark.medium,
    pytest.mark.skipif(shutil.which("git") is None, reason="git binary required"),
]

_GIT_TIMEOUT_SECONDS = 10
_INHERITED_REPOSITORY_ENV = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_TEST_DISALLOW_ABBREVIATED_OPTIONS",
)
_REPOSITORY_ALIASES = {
    "co": "checkout",
    "nb": "checkout -b",
    "ext": "lfs",
    "sh": "!git checkout -b x",
}


@functools.cache
def _git_version() -> str:
    result = subprocess.run(
        ["git", "--version"],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_SECONDS,
        check=False,
    )
    return result.stdout.strip() or "<git version unavailable>"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_SECONDS,
    )


def _isolate_git_environment(mp: pytest.MonkeyPatch, home: Path, ceiling: Path) -> None:
    """Point Git's user, system and discovery state into the test tree."""
    home.mkdir(parents=True, exist_ok=True)
    mp.setenv("HOME", str(home))
    mp.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    mp.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    mp.setenv("GIT_CONFIG_NOSYSTEM", "1")
    mp.setenv("GIT_CEILING_DIRECTORIES", str(ceiling))
    for name in _INHERITED_REPOSITORY_ENV:
        mp.delenv(name, raising=False)


@dataclass(frozen=True)
class Topology:
    """Canonical paths of the repositories and directories the rows analyse."""

    primary: Path
    subdir: Path
    primary_link: Path
    linked: Path
    dot_git: Path
    bare: Path
    plain: Path
    symlink_parent: Path


@pytest.fixture(autouse=True)
def _isolated_git_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    _isolate_git_environment(monkeypatch, tmp_path / "home", tmp_path_factory.getbasetemp())


@pytest.fixture(scope="module")
def topology(tmp_path_factory: pytest.TempPathFactory) -> Topology:
    root = tmp_path_factory.mktemp("git-target").resolve()
    with pytest.MonkeyPatch.context() as mp:
        _isolate_git_environment(mp, root / "build-home", tmp_path_factory.getbasetemp())
        primary = init_checkout(root / "primary")
        commit_file(primary, "sub/tracked.txt", "tracked\n", "add tracked file")
        _git(primary, "branch", "develop")
        for name, value in _REPOSITORY_ALIASES.items():
            _git(primary, "config", f"alias.{name}", value)
        linked = add_linked_worktree(primary, "review")
        bare = root / "bare.git"
        _git(root, "init", "--bare", "-q", str(bare))
        plain = root / "plain"
        plain.mkdir()
        primary_link = root / "primary-link"
        primary_link.symlink_to(primary)
        symlink_parent = root / "w"
        symlink_parent.mkdir()
        (symlink_parent / "lnk").symlink_to(primary / "sub")
    return Topology(
        primary=primary,
        subdir=primary / "sub",
        primary_link=primary_link,
        linked=linked,
        dot_git=primary / ".git",
        bare=bare,
        plain=plain,
        symlink_parent=symlink_parent,
    )


def _context(command: str, result: object) -> str:
    return f"{command!r} -> {result} ({_git_version()})"


def _classified(command: str, cwd: str | Path) -> list[GitSegmentFacts]:
    walked = walk_evaluated_segments(command, str(cwd))
    assert walked is not None, f"{command!r} could not be tokenized ({_git_version()})"
    flags = command_channel_flags(live_command_text(command))
    cache: dict[Any, Any] = {}
    analysed = [analyze_git_segment(segment, flags, cache) for segment in walked]
    return [facts for facts in analysed if facts is not None]


def _only(command: str, cwd: str | Path) -> GitSegmentFacts:
    classified = _classified(command, cwd)
    assert len(classified) == 1, (
        f"{command!r} classified {len(classified)} Git segments ({_git_version()})"
    )
    return classified[0]


def _assert_resolved(
    command: str,
    target: GitTarget,
    *,
    cwd: Path,
    git_dir: Path,
    common_dir: Path,
    is_main: bool,
    toplevel: Path | None,
) -> None:
    context = _context(command, target)
    assert target.kind == GitTargetKind.RESOLVED, context
    assert target.reason == "", context
    assert target.cwd == str(cwd.resolve()), context
    assert target.worktree_git_dir == str(git_dir.resolve()), context
    assert target.common_git_dir == str(common_dir.resolve()), context
    assert target.is_main_worktree is is_main, context
    assert target.toplevel == (None if toplevel is None else str(toplevel.resolve())), context


def _assert_main_checkout(command: str, target: GitTarget, topology: Topology, cwd: Path) -> None:
    _assert_resolved(
        command,
        target,
        cwd=cwd,
        git_dir=topology.dot_git,
        common_dir=topology.dot_git,
        is_main=True,
        toplevel=topology.primary,
    )


def _assert_unresolved(command: str, target: GitTarget, reason: str) -> None:
    context = _context(command, target)
    assert target.kind == GitTargetKind.UNRESOLVED, context
    assert target.reason == reason, context


def _assert_unknown_identity(command: str, facts: GitSegmentFacts) -> None:
    context = _context(command, facts)
    assert facts.identity_unknown is True, context
    assert facts.outcome is not None, context
    assert facts.outcome.ref_ambiguous is True, context
    assert facts.outcome.head_effect == GitHeadEffect.AMBIGUOUS, context


@pytest.mark.parametrize("start", ["primary", "subdir", "primary_link"])
def test_primary_checkout_directories_resolve_to_the_canonical_main_worktree(
    topology: Topology, start: str
) -> None:
    command = "git checkout -b x"
    cwd: Path = getattr(topology, start)

    facts = _only(command, cwd)

    _assert_main_checkout(command, facts.target, topology, cwd)
    assert facts.identity_unknown is False, _context(command, facts)


@pytest.mark.parametrize(
    ("command_template", "start"),
    [("git checkout -b x", "linked"), ("cd {linked} && git checkout -b x", "primary")],
    ids=["linked-cwd", "certain-cd"],
)
def test_linked_worktree_resolves_to_a_non_main_worktree_of_the_same_repository(
    topology: Topology, command_template: str, start: str
) -> None:
    command = command_template.format(linked=topology.linked)

    facts = _only(command, getattr(topology, start))

    _assert_resolved(
        command,
        facts.target,
        cwd=topology.linked,
        git_dir=topology.dot_git / "worktrees" / topology.linked.name,
        common_dir=topology.dot_git,
        is_main=False,
        toplevel=topology.linked,
    )


@pytest.mark.parametrize("name", ["dot_git", "bare"])
def test_git_directory_and_bare_repository_resolve_as_main_without_a_toplevel(
    topology: Topology, name: str
) -> None:
    command = "git update-ref refs/heads/d x"
    path: Path = getattr(topology, name)

    facts = _only(command, path)

    _assert_resolved(
        command,
        facts.target,
        cwd=path,
        git_dir=path,
        common_dir=path,
        is_main=True,
        toplevel=None,
    )


@pytest.mark.parametrize("lang", [None, "de_DE.UTF-8"], ids=["default-locale", "de_DE"])
def test_directory_outside_any_repository_is_not_a_repository(
    topology: Topology, monkeypatch: pytest.MonkeyPatch, lang: str | None
) -> None:
    if lang is not None:
        monkeypatch.setenv("LANG", lang)
        monkeypatch.setenv("LC_ALL", lang)
        monkeypatch.setenv("LANGUAGE", "de")
    command = "git checkout -b x"

    facts = _only(command, topology.plain)

    context = _context(command, facts.target)
    assert facts.target.kind == GitTargetKind.NOT_A_REPOSITORY, context
    assert facts.target.reason == "", context
    assert facts.target.cwd == str(topology.plain), context


@pytest.mark.parametrize(
    ("command_template", "start"),
    [("git checkout -b x", ""), ("true || cd {linked}; git checkout -b x", "primary")],
    ids=["empty-cwd", "cd-after-or"],
)
def test_unproven_working_directory_is_unresolved_cwd_unknown(
    topology: Topology, command_template: str, start: str
) -> None:
    command = command_template.format(linked=topology.linked)

    facts = _only(command, getattr(topology, start) if start else "")

    _assert_unresolved(command, facts.target, "cwd_unknown")


def test_dash_c_relative_path_from_a_linked_worktree_resolves_to_the_main_worktree(
    topology: Topology,
) -> None:
    command = f"git -C {os.path.relpath(topology.primary, topology.linked)} checkout -b x"

    facts = _only(command, topology.linked)

    _assert_main_checkout(command, facts.target, topology, topology.primary)


def test_dash_c_tilde_path_resolves_through_the_hook_home(
    topology: Topology, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "tilde-home"
    home.mkdir()
    (home / "primary").symlink_to(topology.primary)
    monkeypatch.setenv("HOME", str(home))
    command = "git -C ~/primary checkout -b x"

    facts = _only(command, topology.plain)

    _assert_main_checkout(command, facts.target, topology, topology.primary)


def test_dash_c_dynamic_value_is_unresolved_dynamic_cwd(
    topology: Topology, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("P", raising=False)
    command = "git -C $P checkout -b x"

    facts = _only(command, topology.linked)

    _assert_unresolved(command, facts.target, "dynamic_cwd")


@pytest.mark.parametrize("tail", ["new-sub", "new-sub/deeper/still"])
def test_dash_c_missing_path_resolves_through_its_nearest_existing_ancestor(
    topology: Topology, tail: str
) -> None:
    command = f"git -C {topology.primary / tail} checkout -b x"

    facts = _only(command, topology.plain)

    _assert_main_checkout(command, facts.target, topology, topology.primary)


def test_dash_c_missing_path_in_a_non_repository_is_not_a_repository(
    topology: Topology,
) -> None:
    command = f"git -C {topology.plain / 'new'} checkout -b x"

    facts = _only(command, topology.primary)

    context = _context(command, facts.target)
    assert facts.target.kind == GitTargetKind.NOT_A_REPOSITORY, context
    assert facts.target.cwd == str(topology.plain), context


def test_dash_c_dotdot_after_a_symlink_is_resolved_physically(topology: Topology) -> None:
    command = f"git -C {topology.symlink_parent}/lnk/.. checkout -b x"

    facts = _only(command, topology.plain)

    _assert_main_checkout(command, facts.target, topology, topology.primary)


@pytest.mark.parametrize(
    "command_template",
    [
        "GIT_DIR={git_dir} git update-ref refs/heads/d x",
        "export GIT_WORK_TREE={primary}; git checkout -b x",
        "git --git-dir={git_dir} update-ref refs/heads/d x",
        "git --bare update-ref refs/heads/d x",
        "git -c core.worktree={primary} checkout -b x",
    ],
)
def test_layout_override_channel_is_unresolved_layout_override(
    topology: Topology, command_template: str
) -> None:
    command = command_template.format(primary=topology.primary, git_dir=topology.dot_git)

    facts = _only(command, topology.plain)

    _assert_unresolved(command, facts.target, "layout_override")


def test_layout_variable_in_the_hook_environment_is_unresolved_layout_override(
    topology: Topology, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_DIR", str(topology.dot_git))
    command = "git checkout -b x"

    facts = _only(command, topology.primary)

    _assert_unresolved(command, facts.target, "layout_override")


def test_unrecognized_global_option_is_unresolved_unparsed_global_options(
    topology: Topology,
) -> None:
    command = f"git --frobnicate -C {topology.primary} update-ref refs/heads/develop x"

    facts = _only(command, topology.linked)

    _assert_unresolved(command, facts.target, "unparsed_global_options")


@pytest.mark.parametrize(
    "command",
    ["P=checkout; git $P -B develop", "G=git; $G update-ref refs/heads/d x"],
    ids=["dynamic-subcommand", "dynamic-command-word"],
)
def test_dynamic_command_or_subcommand_has_unknown_identity_and_an_ambiguous_outcome(
    topology: Topology, command: str
) -> None:
    facts = _only(command, topology.primary)

    _assert_main_checkout(command, facts.target, topology, topology.primary)
    _assert_unknown_identity(command, facts)


@pytest.mark.parametrize(
    "command",
    [
        "git -c alias.x=y x",
        "HOME=/h git x",
        "export XDG_CONFIG_HOME=/x; git x",
        "export GIT_CONFIG_COUNT=1; git x",
    ],
)
def test_alias_channel_with_a_non_builtin_subcommand_has_unknown_identity(
    topology: Topology, command: str
) -> None:
    facts = _only(command, topology.primary)

    _assert_main_checkout(command, facts.target, topology, topology.primary)
    _assert_unknown_identity(command, facts)


def test_alias_channel_does_not_unsettle_a_builtin_subcommand(topology: Topology) -> None:
    command = "git -c alias.x=y checkout -b x"

    facts = _only(command, topology.primary)

    context = _context(command, facts)
    assert facts.invocation.subcommand == "checkout", context
    assert facts.identity_unknown is False, context
    assert facts.outcome is not None, context
    assert facts.outcome.head_effect == GitHeadEffect.MOVES, context


@pytest.mark.parametrize(
    "command", ["git co -b x", "git nb x"], ids=["plain-alias", "alias-with-arguments"]
)
def test_repository_alias_is_expanded_to_its_builtin_invocation(
    topology: Topology, command: str
) -> None:
    facts = _only(command, topology.primary)

    context = _context(command, facts)
    assert facts.invocation.subcommand == "checkout", context
    assert facts.invocation.flags == ["-b", "x"], context
    assert facts.identity_unknown is False, context
    assert facts.outcome is not None, context
    assert facts.outcome.head_effect == GitHeadEffect.MOVES, context


def test_global_alias_from_the_ambient_config_is_expanded(
    topology: Topology, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "alias-home"
    home.mkdir()
    config = home / ".gitconfig"
    config.write_text("[alias]\n\tgco = checkout\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    command = "git gco -b x"

    facts = _only(command, topology.primary)

    context = _context(command, facts)
    assert facts.invocation.subcommand == "checkout", context
    assert facts.invocation.flags == ["-b", "x"], context
    assert facts.identity_unknown is False, context


@pytest.mark.parametrize(
    "command",
    ["git sh", "git ext version"],
    ids=["shell-alias", "alias-to-non-builtin"],
)
def test_opaque_alias_has_unknown_identity_and_an_ambiguous_outcome(
    topology: Topology, command: str
) -> None:
    facts = _only(command, topology.primary)

    _assert_main_checkout(command, facts.target, topology, topology.primary)
    _assert_unknown_identity(command, facts)


@pytest.mark.parametrize("command", ["git lfs version", "git-frobnicate --x"])
def test_external_git_command_is_not_classified(topology: Topology, command: str) -> None:
    classified = _classified(command, topology.primary)

    assert classified == [], _context(command, classified)


@pytest.mark.parametrize(
    ("command", "subcommand", "flags"),
    [
        ("/usr/lib/git-core/git-checkout -b x", "checkout", ["-b", "x"]),
        ("git-update-ref refs/heads/d x", "update-ref", ["refs/heads/d", "x"]),
    ],
)
def test_git_builtin_executable_is_analysed_as_the_subcommand(
    topology: Topology, command: str, subcommand: str, flags: list[str]
) -> None:
    facts = _only(command, topology.primary)

    _assert_main_checkout(command, facts.target, topology, topology.primary)
    context = _context(command, facts)
    assert facts.invocation.subcommand == subcommand, context
    assert facts.invocation.flags == flags, context
    assert facts.identity_unknown is False, context


@pytest.mark.parametrize("start", ["plain", "linked"])
@pytest.mark.parametrize("tail", ["develop", "feature/missing"])
def test_ref_path_resolves_the_primary_from_any_working_directory(
    topology: Topology, monkeypatch: pytest.MonkeyPatch, start: str, tail: str
) -> None:
    monkeypatch.chdir(getattr(topology, start))
    heads = topology.dot_git / "refs" / "heads"
    path = heads / tail

    target = resolve_git_target_for_path(str(path), {})

    _assert_resolved(
        str(path),
        target,
        cwd=heads,
        git_dir=topology.dot_git,
        common_dir=topology.dot_git,
        is_main=True,
        toplevel=None,
    )


def test_repository_is_queried_once_per_canonical_path_per_cache(
    topology: Topology, monkeypatch: pytest.MonkeyPatch
) -> None:
    queries: list[tuple[str, ...]] = []
    real_git_result = _git_target._git_result

    def recording_git_result(cwd: str, *args: str) -> Any:
        queries.append((cwd, *args))
        return real_git_result(cwd, *args)

    monkeypatch.setattr(_git_target, "_git_result", recording_git_result)
    cache: dict[Any, Any] = {}

    first = resolve_git_target_for_path(str(topology.subdir), cache)
    queries_after_first = len(queries)
    second = resolve_git_target_for_path(str(topology.primary_link / "sub"), cache)

    assert first == second, _git_version()
    assert queries_after_first > 0, _git_version()
    assert len(queries) == queries_after_first, f"{queries} ({_git_version()})"


@pytest.mark.parametrize(
    "stdout", [b"/r/\xff\n/r/\xff\n", b"/only/one\n"], ids=["not_utf8", "one_line"]
)
def test_unreadable_rev_parse_output_is_an_unresolved_git_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stdout: bytes
) -> None:
    def fake_git_result(cwd: str, *args: str) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr=b"")

    monkeypatch.setattr(_git_target, "_git_result", fake_git_result)

    target = resolve_git_target_for_path(str(tmp_path), {})

    _assert_unresolved(str(tmp_path), target, "git_query_failed")


def test_mutation_subcommands_are_builtin_subcommands() -> None:
    assert _GIT_MUTATION_SUBCOMMANDS <= _GIT_BUILTIN_SUBCOMMANDS, sorted(
        _GIT_MUTATION_SUBCOMMANDS - _GIT_BUILTIN_SUBCOMMANDS
    )
