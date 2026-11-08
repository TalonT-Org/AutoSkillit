"""Live completeness of the Git option tables against ``--git-completion-helper-all``.

Members: _parse_completion_helper, _audit_table, git_env, git_repo, git_version,
test_table_agrees_with_live_completion_helper,
test_builtin_inventory_agrees_with_live_list_cmds.

``git <sub> --git-completion-helper-all`` prints one line of whitespace-separated
tokens. Before the first bare ``--`` come the long options, where a trailing
``=`` marks a required value; after it come the ``--no-<x>`` negations (a few
``--no-`` spellings are listed before it). Short options are never printed, so
the check covers long rows only. The comparison runs only on the Git the tables
were derived from: at runtime an unknown option already fails closed, so a newer
Git produces denies, not bypasses.

The builtin subcommand inventory ``_GIT_BUILTIN_SUBCOMMANDS`` is held to the same
reference Git: it must equal ``git --list-cmds=builtins`` exactly.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NamedTuple

import pytest

from autoskillit.hooks._classification._option_grammar import OptionArity, OptionDef
from autoskillit.hooks._runtime._git_option_tables import (
    _GIT_SUBCOMMAND_TABLES,
    _GIT_TABLES_REFERENCE_VERSION,
)
from autoskillit.hooks._runtime._git_target import _GIT_BUILTIN_SUBCOMMANDS
from tests._git_topology import AMBIENT_GIT_VARIABLES, init_empty_checkout

pytestmark = [
    pytest.mark.layer("hooks"),
    pytest.mark.medium,
    pytest.mark.skipif(shutil.which("git") is None, reason="git binary required"),
]

_MIN_LIVE_NAMES = 3


class _Audit(NamedTuple):
    unknown: list[str]
    arity: list[str]
    negations: list[str]


def _parse_completion_helper(output: str) -> tuple[list[str], list[str]]:
    """Split helper output into (long options, negations after the bare ``--``)."""
    tokens = output.split()
    if "--" not in tokens:
        return tokens, []
    divider = tokens.index("--")
    return tokens[:divider], tokens[divider + 1 :]


def _negation_row(name: str, table: Mapping[str, OptionDef]) -> OptionDef | None:
    """The negatable long row whose ``--no-`` spelling is *name*."""
    if not name.startswith("no-"):
        return None
    row = table.get(name[3:])
    return row if row is not None and row.has_long and row.negatable else None


def _audit_table(
    table: Mapping[str, OptionDef], long_options: Sequence[str], negations: Sequence[str]
) -> _Audit:
    unknown: list[str] = []
    arity: list[str] = []
    bad_negations: list[str] = []
    for token in long_options:
        name = token.removeprefix("--").removesuffix("=")
        row = table.get(name)
        if row is not None and row.has_long:
            if token.endswith("=") != (row.arity == OptionArity.REQUIRED):
                arity.append(f"{token} (table arity: {row.arity})")
        elif _negation_row(name, table) is None or token.endswith("="):
            unknown.append(token)
    # NONE and OPTIONAL both print bare, so the converse covers REQUIRED rows only.
    for name, row in table.items():
        if row.has_long and row.arity == OptionArity.REQUIRED and f"--{name}=" not in long_options:
            arity.append(f"--{name} (table arity: {row.arity}, not listed with '=')")
    for token in negations:
        name = token.removeprefix("--")
        if _negation_row(name, table) is None:
            bad_negations.append(token)
    return _Audit(unknown, arity, bad_negations)


def _run_git(env: Mapping[str, str], cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, env=dict(env), capture_output=True, text=True, timeout=30
    )


@pytest.fixture(scope="module")
def git_env(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    root = tmp_path_factory.mktemp("git-option-contract-home")
    home = root / "home"
    xdg_config_home = root / "xdg-config"
    home.mkdir()
    xdg_config_home.mkdir()
    env = {k: v for k, v in os.environ.items() if k not in AMBIENT_GIT_VARIABLES}
    env.update(
        HOME=str(home),
        XDG_CONFIG_HOME=str(xdg_config_home),
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
    )
    return env


@pytest.fixture(scope="module")
def git_repo(tmp_path_factory: pytest.TempPathFactory, git_env: dict[str, str]) -> Path:
    # The helper only needs a repository to run in, not one with history. The
    # topology builder reads the process environment, so it runs under the same
    # isolation as every other Git call in this module.
    with pytest.MonkeyPatch.context() as patch:
        for name in AMBIENT_GIT_VARIABLES:
            patch.delenv(name, raising=False)
        for name in ("HOME", "XDG_CONFIG_HOME", "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM"):
            patch.setenv(name, git_env[name])
        return init_empty_checkout(tmp_path_factory.mktemp("git-option-contract-repo"))


@pytest.fixture(scope="module")
def git_version(git_env: dict[str, str], git_repo: Path) -> tuple[str, tuple[int, int]]:
    result = _run_git(git_env, git_repo, "--version")
    text = result.stdout.strip()
    assert result.returncode == 0, f"git --version failed: {result.stderr!r}"
    match = re.search(r"(\d+)\.(\d+)", text)
    assert match is not None, f"unparseable git --version output: {text!r}"
    return text, (int(match.group(1)), int(match.group(2)))


@pytest.mark.parametrize("subcommand", sorted(_GIT_SUBCOMMAND_TABLES))
def test_table_agrees_with_live_completion_helper(
    subcommand: str,
    git_env: dict[str, str],
    git_repo: Path,
    git_version: tuple[str, tuple[int, int]],
) -> None:
    version_text, version = git_version
    table = _GIT_SUBCOMMAND_TABLES[subcommand]
    result = _run_git(git_env, git_repo, subcommand, "--git-completion-helper-all")
    assert result.returncode == 0, (
        f"{version_text}: git {subcommand} --git-completion-helper-all failed: "
        f"rc={result.returncode}, stderr={result.stderr!r}"
    )
    long_options, negations = _parse_completion_helper(result.stdout)
    assert len(long_options) >= _MIN_LIVE_NAMES, (
        f"{version_text}: git {subcommand} helper listed too few options: {result.stdout!r}"
    )
    audit = _audit_table(table, long_options, negations)

    if version != _GIT_TABLES_REFERENCE_VERSION:
        pytest.skip(
            f"{version_text} differs from the tables' reference Git "
            f"{_GIT_TABLES_REFERENCE_VERSION}; live names missing from the "
            f"{subcommand} table: {audit.unknown + audit.negations}"
        )

    assert not audit.unknown, (
        f"{version_text}: git {subcommand} lists long options with no table row: {audit.unknown}"
    )
    assert not audit.arity, (
        f"{version_text}: git {subcommand} value arity disagrees with the table "
        f"(a trailing '=' marks REQUIRED): {audit.arity}"
    )
    assert not audit.negations, (
        f"{version_text}: git {subcommand} lists negations whose table row is "
        f"missing or not negatable: {audit.negations}"
    )


def test_builtin_inventory_agrees_with_live_list_cmds(
    git_env: dict[str, str],
    git_repo: Path,
    git_version: tuple[str, tuple[int, int]],
) -> None:
    version_text, version = git_version
    result = _run_git(git_env, git_repo, "--list-cmds=builtins")
    assert result.returncode == 0, (
        f"{version_text}: git --list-cmds=builtins failed: "
        f"rc={result.returncode}, stderr={result.stderr!r}"
    )
    live = frozenset(result.stdout.split())
    assert len(live) >= _MIN_LIVE_NAMES, (
        f"{version_text}: git --list-cmds=builtins listed too few names: {result.stdout!r}"
    )
    missing = sorted(live - _GIT_BUILTIN_SUBCOMMANDS)
    stale = sorted(_GIT_BUILTIN_SUBCOMMANDS - live)

    if version != _GIT_TABLES_REFERENCE_VERSION:
        pytest.skip(
            f"{version_text} differs from the tables' reference Git "
            f"{_GIT_TABLES_REFERENCE_VERSION}; builtins missing from the inventory: "
            f"{missing}; stale inventory names: {stale}"
        )

    assert not missing and not stale, (
        f"{version_text}: _GIT_BUILTIN_SUBCOMMANDS disagrees with git --list-cmds=builtins: "
        f"missing={missing} stale={stale}"
    )
