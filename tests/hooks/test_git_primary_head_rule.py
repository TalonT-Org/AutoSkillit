"""Unit matrix for the primary-checkout HEAD rule of git_ops_guard.

Members: TestPrimaryHeadViolation.

Each row hand-builds the facts of one Git segment, so no repository is touched.
The anchor is the callable that yields the canonical common Git directory of the
session's project repository, or ``None`` when that directory is not a repository.
"""

from __future__ import annotations

from collections.abc import Callable

# The sys.path bootstrap for bare-name hook-sibling imports is centralized in
# tests/conftest.py.
import pytest
from _command_classification import GitInvocation  # type: ignore[import-not-found]
from _git_outcome import GitHeadEffect, GitRefOutcome  # type: ignore[import-not-found]
from _git_target import (  # type: ignore[import-not-found]
    GitSegmentFacts,
    GitTarget,
    GitTargetKind,
)

from autoskillit.hooks.guards import git_ops_guard

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]

_COMMON = "/work/repo/.git"
_OTHER_COMMON = "/work/other/.git"

_MAIN_WORKTREE = GitTarget(
    GitTargetKind.RESOLVED, "", "/work/repo", _COMMON, _COMMON, True, "/work/repo"
)
_LINKED_WORKTREE = GitTarget(
    GitTargetKind.RESOLVED,
    "",
    "/work/linked",
    f"{_COMMON}/worktrees/linked",
    _COMMON,
    False,
    "/work/linked",
)
_OTHER_REPOSITORY = GitTarget(
    GitTargetKind.RESOLVED, "", "/work/other", _OTHER_COMMON, _OTHER_COMMON, True, "/work/other"
)
_NOT_A_REPOSITORY = GitTarget(
    GitTargetKind.NOT_A_REPOSITORY, "", "/work/plain", "", "", False, None
)
_UNRESOLVED = GitTarget(GitTargetKind.UNRESOLVED, "cwd_unknown", "", "", "", False, None)


def _facts(
    target: GitTarget,
    effect: GitHeadEffect | None,
    *,
    identity_unknown: bool = False,
    rewrites_current_branch: bool = False,
) -> GitSegmentFacts:
    outcome = (
        None
        if effect is None
        else GitRefOutcome(None, rewrites_current_branch, "HEAD", False, effect)
    )
    invocation = GitInvocation("checkout", [], [], ["git"])
    return GitSegmentFacts(invocation, target, outcome, identity_unknown)


def _anchored_at(common: str | None) -> Callable[[], str | None]:
    return lambda: common


def _never_asked() -> str | None:
    raise AssertionError("the anchor must not be resolved for this row")


_MOVES = GitHeadEffect.MOVES
_AMBIGUOUS = GitHeadEffect.AMBIGUOUS


class TestPrimaryHeadViolation:
    @pytest.mark.parametrize(
        ("facts", "expected"),
        [
            pytest.param(_facts(_MAIN_WORKTREE, _MOVES), "moves_primary_head", id="moves-on-main"),
            pytest.param(
                _facts(_MAIN_WORKTREE, GitHeadEffect.NONE, rewrites_current_branch=True),
                "moves_primary_head",
                id="rewrites-current-branch-on-main",
            ),
            pytest.param(
                _facts(_MAIN_WORKTREE, GitHeadEffect.PATH_RESTORE, rewrites_current_branch=True),
                "moves_primary_head",
                id="rewrites-current-branch-outranks-path-restore",
            ),
            pytest.param(
                _facts(_MAIN_WORKTREE, _AMBIGUOUS), "ambiguous_head_effect", id="ambiguous-on-main"
            ),
            pytest.param(
                _facts(_MAIN_WORKTREE, GitHeadEffect.NONE, identity_unknown=True),
                "ambiguous_head_effect",
                id="identity-unknown-on-main",
            ),
            pytest.param(
                _facts(_MAIN_WORKTREE, _MOVES, identity_unknown=True),
                "ambiguous_head_effect",
                id="identity-unknown-outranks-moves-on-main",
            ),
            pytest.param(
                _facts(_MAIN_WORKTREE, None, identity_unknown=True),
                "ambiguous_head_effect",
                id="identity-unknown-without-outcome-on-main",
            ),
            pytest.param(_facts(_UNRESOLVED, _MOVES), "unresolved_target", id="moves-unresolved"),
            pytest.param(
                _facts(_UNRESOLVED, _AMBIGUOUS), "unresolved_target", id="ambiguous-unresolved"
            ),
            pytest.param(
                _facts(_UNRESOLVED, GitHeadEffect.NONE, identity_unknown=True),
                "unresolved_target",
                id="identity-unknown-unresolved",
            ),
            pytest.param(
                _facts(_UNRESOLVED, GitHeadEffect.NONE, rewrites_current_branch=True),
                "unresolved_target",
                id="rewrites-current-branch-unresolved",
            ),
        ],
    )
    def test_names_a_violation_of_the_anchors_main_worktree_or_an_unresolved_target(
        self, facts: GitSegmentFacts, expected: str
    ) -> None:
        assert git_ops_guard._primary_head_violation(facts, _anchored_at(_COMMON)) == expected

    @pytest.mark.parametrize(
        "facts",
        [
            pytest.param(_facts(_LINKED_WORKTREE, _MOVES), id="moves-on-linked"),
            pytest.param(
                _facts(_LINKED_WORKTREE, GitHeadEffect.NONE, rewrites_current_branch=True),
                id="rewrites-current-branch-on-linked",
            ),
            pytest.param(_facts(_LINKED_WORKTREE, _AMBIGUOUS), id="ambiguous-on-linked"),
            pytest.param(
                _facts(_LINKED_WORKTREE, GitHeadEffect.NONE, identity_unknown=True),
                id="identity-unknown-on-linked",
            ),
            pytest.param(_facts(_OTHER_REPOSITORY, _MOVES), id="moves-on-another-common-dir"),
            pytest.param(
                _facts(_OTHER_REPOSITORY, _AMBIGUOUS), id="ambiguous-on-another-common-dir"
            ),
            pytest.param(
                _facts(_OTHER_REPOSITORY, GitHeadEffect.NONE, identity_unknown=True),
                id="identity-unknown-on-another-common-dir",
            ),
            pytest.param(_facts(_NOT_A_REPOSITORY, _MOVES), id="moves-outside-a-repository"),
            pytest.param(
                _facts(_NOT_A_REPOSITORY, GitHeadEffect.NONE, identity_unknown=True),
                id="identity-unknown-outside-a-repository",
            ),
        ],
    )
    def test_ignores_a_head_move_outside_the_anchors_main_worktree(
        self, facts: GitSegmentFacts
    ) -> None:
        assert git_ops_guard._primary_head_violation(facts, _anchored_at(_COMMON)) is None

    @pytest.mark.parametrize(
        "facts",
        [
            pytest.param(_facts(_MAIN_WORKTREE, GitHeadEffect.PATH_RESTORE), id="path-restore"),
            pytest.param(_facts(_MAIN_WORKTREE, GitHeadEffect.NONE), id="none"),
            pytest.param(_facts(_MAIN_WORKTREE, None), id="no-outcome"),
            pytest.param(_facts(_UNRESOLVED, GitHeadEffect.PATH_RESTORE), id="unresolved-restore"),
            pytest.param(_facts(_UNRESOLVED, GitHeadEffect.NONE), id="unresolved-none"),
            pytest.param(_facts(_UNRESOLVED, None), id="unresolved-no-outcome"),
        ],
    )
    def test_ignores_a_command_that_leaves_head_alone_without_resolving_the_anchor(
        self, facts: GitSegmentFacts
    ) -> None:
        assert git_ops_guard._primary_head_violation(facts, _never_asked) is None

    @pytest.mark.parametrize(
        "facts",
        [
            pytest.param(_facts(_MAIN_WORKTREE, _MOVES), id="moves"),
            pytest.param(_facts(_MAIN_WORKTREE, _AMBIGUOUS), id="ambiguous"),
            pytest.param(
                _facts(_MAIN_WORKTREE, GitHeadEffect.NONE, identity_unknown=True),
                id="identity-unknown",
            ),
            pytest.param(
                _facts(_MAIN_WORKTREE, GitHeadEffect.NONE, rewrites_current_branch=True),
                id="rewrites-current-branch",
            ),
        ],
    )
    def test_is_inactive_when_the_anchor_is_not_a_repository(self, facts: GitSegmentFacts) -> None:
        assert git_ops_guard._primary_head_violation(facts, _anchored_at(None)) is None

    @pytest.mark.parametrize("target", [_LINKED_WORKTREE, _NOT_A_REPOSITORY], ids=lambda t: t.cwd)
    def test_does_not_resolve_the_anchor_for_a_target_that_is_no_main_worktree(
        self, target: GitTarget
    ) -> None:
        facts = _facts(target, _MOVES)
        assert git_ops_guard._primary_head_violation(facts, _never_asked) is None

    def test_an_unresolvable_anchor_raises_only_for_a_command_that_needs_it(self) -> None:
        def failing_anchor() -> str | None:
            raise RuntimeError("cook anchor repository unresolved")

        assert (
            git_ops_guard._primary_head_violation(
                _facts(_MAIN_WORKTREE, GitHeadEffect.NONE), failing_anchor
            )
            is None
        )
        with pytest.raises(RuntimeError, match="unresolved"):
            git_ops_guard._primary_head_violation(_facts(_MAIN_WORKTREE, _MOVES), failing_anchor)
