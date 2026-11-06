"""Unit matrix for the Git outcome classifiers in hooks/_runtime/_git_outcome.py.

Members: checkout, switch, branch and symbolic-ref outcome matrices
(``git_ref_outcome``), dynamic-token ambiguity rows, the ``parse_update_ref``,
``parse_reset``, ``parse_push`` and ``parse_fetch`` parser rows, the
``is_ref_mutation`` rows, and the ``risky_git_operation`` predicate registry and
outcome rows.

Rows follow Git 2.43's case partitions. Argv is space-separated shorthand split
on whitespace; ``5f3c9e1`` stands for a commit and ``feat`` for a branch name.
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from autoskillit.hooks._runtime._git_outcome import (
    _RISKY_PREDICATES,
    GitHeadEffect,
    git_ref_outcome,
    is_ref_mutation,
    parse_fetch,
    parse_push,
    parse_reset,
    parse_update_ref,
    risky_git_operation,
)
from autoskillit.hooks._runtime._hook_constants import RISKY_GIT_OPERATIONS

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]

_NONE = GitHeadEffect.NONE
_MOVES = GitHeadEffect.MOVES
_PATH_RESTORE = GitHeadEffect.PATH_RESTORE
_AMBIGUOUS = GitHeadEffect.AMBIGUOUS


def _argv(text: str) -> list[str]:
    return text.split()


def _id(argv: str) -> str:
    return argv or "no-arguments"


def _head_cases(
    effect: GitHeadEffect, argvs: tuple[str, ...], rewritten: str | None = None
) -> list[object]:
    return [pytest.param(argv, effect, rewritten, id=_id(argv)) for argv in argvs]


def _head_and_rewrite(subcommand: str, argv: str) -> tuple[GitHeadEffect, str | None]:
    outcome = git_ref_outcome(subcommand, _argv(argv))
    assert outcome is not None, f"git {subcommand} {argv} produced no outcome"
    return outcome.head_effect, outcome.rewritten_ref


_CHECKOUT_ROWS = [
    *_head_cases(
        _MOVES,
        (
            "-b x 5f3c9e1",
            "-fb x",
            "-bx",
            "--orphan x",
            "--orph=x",
            "-d",
            "--detach",
            "-t origin/y",
            "-b x -t origin/y",
        ),
    ),
    *_head_cases(
        _MOVES,
        (
            "-B develop 5f3c9e1",
            "-Bdevelop",
            "-fB develop",
            "5f3c9e1 -B develop",
            "--ignore-other-worktrees -Bdevelop",
        ),
        "develop",
    ),
    *_head_cases(
        _PATH_RESTORE,
        (
            "-- f",
            "feat -- f",
            "feat f1 f2",
            "-p",
            "-p other",
            "--ours f",
            "--theirs f",
            "-2 f",
            "-3 f",
            "--ours feat",
        ),
    ),
    *_head_cases(_AMBIGUOUS, ("--pathspec-from-file=x", "other --pathspec-from-file=empty.lst")),
    *_head_cases(_NONE, ("", "-f", "-q")),
    *_head_cases(_MOVES, ("feat --", "-", "@{-1}", "main...other")),
    *_head_cases(_AMBIGUOUS, ("a b -- f", "--bogus x", "--o x")),
    *_head_cases(_AMBIGUOUS, ("feat", "file.txt", ":/.")),
    *_head_cases(_PATH_RESTORE, (".", "./sub/f", "/abs", ":/")),
    *_head_cases(_AMBIGUOUS, ("--detach --no-detach other",)),
]

_SWITCH_ROWS = [
    *_head_cases(_MOVES, ("-c x", "-cx", "--create=x", "feat", "-", "--detach", "-d")),
    *_head_cases(
        _MOVES,
        (
            "-C develop",
            "-Cdevelop",
            "-fC develop",
            "--force-create develop",
            "--force-create=develop",
            "--force-c develop",
        ),
        "develop",
    ),
    *_head_cases(_NONE, ("",)),
    *_head_cases(_AMBIGUOUS, ("a b",)),
]


@pytest.mark.parametrize(("argv", "effect", "rewritten"), _CHECKOUT_ROWS)
def test_checkout_outcome(argv: str, effect: GitHeadEffect, rewritten: str | None) -> None:
    assert _head_and_rewrite("checkout", argv) == (effect, rewritten)


@pytest.mark.parametrize(("argv", "effect", "rewritten"), _SWITCH_ROWS)
def test_switch_outcome(argv: str, effect: GitHeadEffect, rewritten: str | None) -> None:
    assert _head_and_rewrite("switch", argv) == (effect, rewritten)


def _branch_view(argv: str) -> tuple[str | None, bool, GitHeadEffect]:
    outcome = git_ref_outcome("branch", _argv(argv))
    assert outcome is not None, f"git branch {argv} produced no outcome"
    return outcome.rewritten_ref, outcome.rewrites_current_branch, outcome.head_effect


def _branch_cases(rewritten: str, argvs: tuple[str, ...]) -> list[object]:
    return [pytest.param(argv, rewritten, id=argv) for argv in argvs]


_BRANCH_REWRITE_ROWS = [
    *_branch_cases("x", ("-f x 5f3c9e1", "x 5f3c9e1 -f", "-fq x", "--forc x")),
    *_branch_cases("old", ("-m old new", "-M old new", "--move old new", "-fm old new")),
    *_branch_cases("b", ("-c a b",)),
]

_BRANCH_NO_OUTCOME_ARGVS = (
    "",
    "-a",
    "--list",
    "-vv",
    "--show-current",
    "-d x",
    "-D x",
    "--contains",
    "--merged",
    "--no-merged",
    # Git takes --list as the value of --contains and exits 129; the table reads
    # two options. Both readings are list mode, so the verdict is the same.
    "--contains --list",
)


@pytest.mark.parametrize(("argv", "rewritten"), _BRANCH_REWRITE_ROWS)
def test_branch_rewrites_the_named_ref_without_touching_head(argv: str, rewritten: str) -> None:
    assert _branch_view(argv) == (rewritten, False, _NONE)


def test_branch_move_with_one_operand_renames_the_current_branch() -> None:
    assert _branch_view("-m new") == (None, True, _MOVES)


@pytest.mark.parametrize("argv", _BRANCH_NO_OUTCOME_ARGVS, ids=_id)
def test_branch_list_and_delete_modes_have_no_outcome(argv: str) -> None:
    assert git_ref_outcome("branch", _argv(argv)) is None


_SYMBOLIC_REF_MOVES_ARGVS = (
    "HEAD refs/heads/x",
    "-q HEAD refs/heads/x",
    "-m why HEAD refs/heads/x",
    "-mwhy HEAD refs/heads/x",
)


@pytest.mark.parametrize("argv", _SYMBOLIC_REF_MOVES_ARGVS, ids=_id)
def test_symbolic_ref_head_assignment_moves_head_to_the_target(argv: str) -> None:
    outcome = git_ref_outcome("symbolic-ref", _argv(argv))

    assert outcome is not None
    assert outcome.rewritten_ref == "refs/heads/x"
    assert outcome.start_point == "refs/heads/x"
    assert outcome.head_effect == _MOVES
    assert not outcome.ref_ambiguous


def test_symbolic_ref_of_another_ref_rewrites_that_ref_only() -> None:
    outcome = git_ref_outcome("symbolic-ref", _argv("refs/heads/a refs/heads/b"))

    assert outcome is not None
    assert outcome.rewritten_ref == "refs/heads/a"
    assert outcome.head_effect == _NONE
    assert not outcome.ref_ambiguous


def test_symbolic_ref_read_has_no_outcome() -> None:
    assert git_ref_outcome("symbolic-ref", _argv("HEAD")) is None


_DYNAMIC_REF_ROWS = [
    pytest.param("checkout", "-B $X", True, id="checkout-B-dynamic-name"),
    pytest.param("checkout", "-B x $SHA", True, id="checkout-B-dynamic-start-point"),
    pytest.param("checkout", "-B x 5f3c9e1", False, id="checkout-B-literal"),
    pytest.param("switch", "-C $X", True, id="switch-C-dynamic-name"),
    pytest.param("branch", "-m $X new", True, id="branch-move-dynamic-source"),
    pytest.param("branch", "-m old new", False, id="branch-move-literal"),
    pytest.param("symbolic-ref", "HEAD $X", True, id="symbolic-ref-dynamic-target"),
    pytest.param("symbolic-ref", "$X refs/heads/y", True, id="symbolic-ref-dynamic-name"),
    pytest.param("symbolic-ref", "HEAD refs/heads/y", False, id="symbolic-ref-literal"),
    pytest.param("checkout", "--bogus x", True, id="checkout-unresolved"),
    pytest.param("switch", "--bogus x", True, id="switch-unresolved"),
    pytest.param("branch", "--bogus x", True, id="branch-unresolved"),
    pytest.param("symbolic-ref", "--bogus x", True, id="symbolic-ref-unresolved"),
]


@pytest.mark.parametrize(("subcommand", "argv", "ambiguous"), _DYNAMIC_REF_ROWS)
def test_dynamic_or_unresolved_tokens_make_the_ref_ambiguous(
    subcommand: str, argv: str, ambiguous: bool
) -> None:
    outcome = git_ref_outcome(subcommand, _argv(argv))

    assert outcome is not None
    assert outcome.ref_ambiguous is ambiguous


def test_dynamic_deciding_operand_is_an_ambiguous_head_effect() -> None:
    assert _head_and_rewrite("checkout", "$X") == (_AMBIGUOUS, None)


def test_unresolved_checkout_is_an_ambiguous_head_effect() -> None:
    outcome = git_ref_outcome("checkout", _argv("--bogus x"))

    assert outcome is not None
    assert outcome.head_effect == _AMBIGUOUS
    assert outcome.ref_ambiguous


@pytest.mark.parametrize("subcommand", ["update-ref", "reset", "push", "fetch", "commit"])
def test_other_subcommands_have_no_ref_outcome(subcommand: str) -> None:
    assert git_ref_outcome(subcommand, _argv("origin main")) is None


@pytest.mark.parametrize(
    ("argv", "delete", "stdin"),
    [
        pytest.param("-dm why refs/heads/d", True, False, id="-dm-delete"),
        pytest.param("--std", False, True, id="--std-prefix"),
    ],
)
def test_parse_update_ref(argv: str, delete: bool, stdin: bool) -> None:
    parsed = parse_update_ref(_argv(argv))

    assert parsed.unresolved is None
    assert parsed.delete is delete
    assert parsed.stdin is stdin


def test_parse_update_ref_keeps_the_ref_as_an_operand_after_a_reason() -> None:
    assert parse_update_ref(_argv("-dm why refs/heads/d")).operands == ("refs/heads/d",)


@pytest.mark.parametrize(
    ("argv", "mode", "path_mode"),
    [
        pytest.param("--soft feat --", "soft", False, id="soft-commit-mode"),
        pytest.param("feat -- f", None, True, id="paths-after-dashdash"),
        pytest.param("feat f", None, True, id="two-operands"),
        pytest.param("-p", None, True, id="patch"),
        pytest.param("--har", "hard", False, id="mode-prefix"),
    ],
)
def test_parse_reset(argv: str, mode: str | None, path_mode: bool) -> None:
    parsed = parse_reset(_argv(argv))

    assert parsed.unresolved is None
    assert parsed.mode == mode
    assert parsed.path_mode is path_mode


def test_parse_reset_names_the_commit_before_dashdash() -> None:
    assert parse_reset(_argv("--soft feat --")).commit == "feat"


def test_parse_reset_pathspec_from_file_is_unresolved() -> None:
    assert parse_reset(_argv("--pathspec-from-file=x feat")).unresolved is not None


@pytest.mark.parametrize(
    ("argv", "remote", "refspecs"),
    [
        pytest.param("-o origin . HEAD:develop", ".", ("HEAD:develop",), id="push-option-value"),
        pytest.param("--repo=r", "r", (), id="repo-option"),
        pytest.param("--repo=r . HEAD:develop", ".", ("HEAD:develop",), id="operand-wins"),
        pytest.param("origin HEAD", "origin", ("HEAD",), id="named-remote"),
    ],
)
def test_parse_push(argv: str, remote: str, refspecs: tuple[str, ...]) -> None:
    parsed = parse_push(_argv(argv))

    assert parsed.unresolved is None
    assert parsed.remote == remote
    assert parsed.refspecs == refspecs


def test_parse_fetch_reports_the_refspec_destination() -> None:
    parsed = parse_fetch(_argv("origin develop:develop"))

    assert parsed.unresolved is None
    assert parsed.remote == "origin"
    assert parsed.refspecs == ("develop:develop",)


_REF_MUTATION_ROWS = [
    pytest.param("update-ref", "refs/heads/x HEAD", True, id="update-ref"),
    pytest.param("reset", "HEAD~", True, id="reset-commit"),
    pytest.param("reset", "-- f", False, id="reset-paths"),
    pytest.param("checkout", "-Bdevelop", True, id="checkout-B"),
    pytest.param("branch", "-m a b", True, id="branch-move"),
    pytest.param("checkout", "-b f", False, id="checkout-b"),
    pytest.param("branch", "-a", False, id="branch-list"),
    pytest.param("push", "origin HEAD", False, id="push-named-remote"),
    pytest.param("push", "-u origin main", False, id="push-set-upstream-named-remote"),
    pytest.param("push", ". HEAD:develop", True, id="push-dot-remote"),
    pytest.param("push", "../repo x", True, id="push-relative-path-remote"),
    pytest.param("push", "$R x", True, id="push-dynamic-remote"),
    pytest.param("fetch", "origin", False, id="fetch-no-refspec"),
    pytest.param("fetch", "origin develop:develop", True, id="fetch-refspec-destination"),
    pytest.param("fetch", ". a:b", True, id="fetch-dot-refspec"),
    pytest.param("<unresolved>", "", True, id="unresolved-subcommand"),
    pytest.param("$SUB", "", True, id="dynamic-subcommand"),
]


@pytest.mark.parametrize(("subcommand", "argv", "expected"), _REF_MUTATION_ROWS)
def test_is_ref_mutation(subcommand: str, argv: str, expected: bool) -> None:
    assert is_ref_mutation(subcommand, _argv(argv)) is expected


def missing_predicates(
    predicates: Mapping[tuple[str, ...], object], ops: frozenset[tuple[str, ...]]
) -> set[tuple[str, ...]]:
    """The operations in *ops* that *predicates* has no entry for."""
    return set(ops) - set(predicates)


def test_risky_predicates_are_keyed_exactly_by_the_risky_operations() -> None:
    assert set(_RISKY_PREDICATES) == RISKY_GIT_OPERATIONS
    assert not missing_predicates(_RISKY_PREDICATES, RISKY_GIT_OPERATIONS)


@pytest.mark.parametrize("op", sorted(RISKY_GIT_OPERATIONS), ids=lambda op: " ".join(op))
def test_a_predicate_map_missing_one_operation_is_reported(op: tuple[str, ...]) -> None:
    canary = {key: predicate for key, predicate in _RISKY_PREDICATES.items() if key != op}

    assert set(canary) != RISKY_GIT_OPERATIONS
    assert missing_predicates(canary, RISKY_GIT_OPERATIONS) == {op}


_UNRESOLVED = ("<unresolved>",)
_AMEND = ("commit", "--amend")
_FORCE = ("push", "--force")
_FORCE_SHORT = ("push", "-f")
_LEASE = ("push", "--force-with-lease")
_HARD = ("reset", "--hard")
_CLEAN = ("clean", "-f")
_CLEAN_DIRECTORIES = ("clean", "-fd")
_CHECKOUT_TREE = ("checkout", ".")
_CHECKOUT_DASHDASH_TREE = ("checkout", "--", ".")


def _risky_cases(expected: tuple[str, ...] | None, commands: tuple[str, ...]) -> list[object]:
    """Rows from ``"<subcommand> <argv>"`` shorthand that all expect *expected*."""
    cases = []
    for command in commands:
        subcommand, _, argv = command.partition(" ")
        cases.append(pytest.param(subcommand, argv, expected, id=command))
    return cases


_RISKY_ROWS = [
    *_risky_cases(
        _FORCE,
        (
            "push --force origin main",
            "push -f origin main",
            "push -fu origin main",
            "push -qf origin main",
            "push --no-force --force origin main",
            "push origin +main",
            "push origin +HEAD:main",
            "push -u origin main +feat",
        ),
    ),
    *_risky_cases(
        _LEASE,
        (
            "push --force-with-lease origin main",
            "push --force-w origin main",
            "push --force-with-lease=main origin main",
        ),
    ),
    *_risky_cases(_HARD, ("reset --hard HEAD~", "reset --har HEAD~", "reset --h HEAD~")),
    *_risky_cases(_HARD, ("reset --soft --hard HEAD~", "reset --mixed --hard")),
    *_risky_cases(
        _CLEAN,
        (
            "clean -f",
            "clean --forc",
            "clean -df",
            "clean -xdf",
            "clean -fx",
            "clean -ff",
            "clean -d",
            "clean -x",
            "clean -n --no-dry-run",
            "clean -e pattern -f",
            "clean -i",
            "clean -fi",
            "clean --interactive -d",
            "clean -n -i --no-dry-run",
        ),
    ),
    *_risky_cases(_AMEND, ("commit --amend", "commit --amen", "commit --am --no-edit")),
    *_risky_cases(_AMEND, ("commit -m x --no-amend --amend",)),
    *_risky_cases(
        _CHECKOUT_TREE,
        (
            "checkout .",
            "checkout -f",
            "checkout --force",
            "checkout ./",
            "checkout :/",
            "checkout :(top)",
            "checkout :(top).",
            "checkout . origin main",
            "checkout feat .",
            "checkout *",
            "checkout d/..",
            "checkout :(top)*",
        ),
    ),
    *_risky_cases(
        _CHECKOUT_DASHDASH_TREE,
        (
            "checkout -- .",
            "checkout -- ./",
            "checkout -- a .",
            "checkout -- *",
            "checkout -- d/..",
            "checkout -- :(top)*",
        ),
    ),
]

_ALLOWED_ROWS = [
    *_risky_cases(
        None,
        (
            "commit -m --amend",
            "commit -m x --no-amend",
            "commit --amend --no-amend",
            "commit -m x",
            "commit -F --amend",
        ),
    ),
    *_risky_cases(
        None,
        (
            "push origin main",
            "push -u origin main",
            "push --force-if-includes origin main",
            "push --recurse-submodules=check origin main",
            "push --force --no-force origin main",
            "push --repo=+r origin main",
            "push origin main:+refs",
        ),
    ),
    *_risky_cases(None, ("reset --soft HEAD~", "reset --hard --soft HEAD~", "reset HEAD~")),
    *_risky_cases(
        None,
        (
            "clean -n",
            "clean -nd",
            "clean --dry-run -d",
            "clean -fni",
            "clean -f -n",
        ),
    ),
    *_risky_cases(
        None,
        (
            "checkout -- a.txt",
            "checkout feature",
            "checkout -b x",
            "checkout -f feature",
            "checkout :/.",
            "checkout -p",
            "checkout feat -- a.txt",
            "checkout -- d/*",
            "checkout -- *.txt",
            "checkout -- ./d",
        ),
    ),
]

_UNRESOLVED_ROWS = [
    *_risky_cases(_UNRESOLVED, ("push --forc origin main", "push --bogus", "reset --hard-foo")),
    *_risky_cases(_UNRESOLVED, ("commit --bogus", "clean --bogus", "checkout --bogus x")),
]


@pytest.mark.parametrize(
    ("subcommand", "argv", "expected"), [*_RISKY_ROWS, *_ALLOWED_ROWS, *_UNRESOLVED_ROWS]
)
def test_risky_git_operation_judges_the_outcome_not_the_spelling(
    subcommand: str, argv: str, expected: tuple[str, ...] | None
) -> None:
    assert risky_git_operation(subcommand, _argv(argv), RISKY_GIT_OPERATIONS) == expected


@pytest.mark.parametrize("op", sorted(RISKY_GIT_OPERATIONS), ids=lambda op: " ".join(op))
def test_each_operation_is_detected_in_its_own_spelling_when_it_is_the_only_one_blocked(
    op: tuple[str, ...],
) -> None:
    assert risky_git_operation(op[0], list(op[1:]), frozenset({op})) == op


@pytest.mark.parametrize(
    ("subcommand", "argv", "blocked", "expected"),
    [
        pytest.param("push", "-f origin main", RISKY_GIT_OPERATIONS, _FORCE, id="push-f-all"),
        pytest.param(
            "push", "-f origin main", frozenset({_FORCE_SHORT}), _FORCE_SHORT, id="push-f-short"
        ),
        pytest.param("push", "-f origin main", frozenset({_FORCE}), _FORCE, id="push-f-long-only"),
        pytest.param("clean", "-fd", RISKY_GIT_OPERATIONS, _CLEAN, id="clean-fd-all"),
        pytest.param(
            "clean", "-fd", frozenset({_CLEAN_DIRECTORIES}), _CLEAN_DIRECTORIES, id="clean-fd-only"
        ),
        pytest.param("clean", "-f", frozenset({_CLEAN_DIRECTORIES}), None, id="clean-f-not-fd"),
        pytest.param("push", "--force origin main", frozenset({_HARD}), None, id="other-op"),
        pytest.param("push", "--forc origin main", frozenset({_HARD}), None, id="other-unparsed"),
        pytest.param(
            "checkout", "-- .", frozenset({_CHECKOUT_TREE}), None, id="dashdash-not-tree"
        ),
        pytest.param(
            "checkout",
            "-- .",
            frozenset({_CHECKOUT_DASHDASH_TREE}),
            _CHECKOUT_DASHDASH_TREE,
            id="dashdash-tree",
        ),
        pytest.param(
            "checkout",
            ". origin main",
            frozenset({_CHECKOUT_DASHDASH_TREE}),
            None,
            id="tree-not-dashdash",
        ),
    ],
)
def test_risky_git_operation_reports_only_blocked_operations_deterministically(
    subcommand: str,
    argv: str,
    blocked: frozenset[tuple[str, ...]],
    expected: tuple[str, ...] | None,
) -> None:
    assert risky_git_operation(subcommand, _argv(argv), blocked) == expected


def test_an_unresolved_subcommand_is_reported_unresolved_for_any_blocked_set() -> None:
    assert risky_git_operation("<unresolved>", [], RISKY_GIT_OPERATIONS) == _UNRESOLVED
    assert risky_git_operation("<unresolved>", [], frozenset({_HARD})) == _UNRESOLVED
