"""Tests for CloneGuardPolicy construction and decision logic."""

from __future__ import annotations

import pytest

from autoskillit.execution.clone_guard import build_clone_guard_policy, is_clone_commit_skill

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def test_policy_for_readonly_skill():
    policy = build_clone_guard_policy(
        readonly_skill=True,
        has_write_scope=True,
        is_clone_commit=False,
        is_worktree=False,
    )
    assert policy.should_fire(success=True) is True
    assert policy.should_fire(success=False) is True
    assert policy.selective_revert is True


def test_policy_for_clone_commit_skill_success():
    policy = build_clone_guard_policy(
        readonly_skill=False,
        has_write_scope=True,
        is_clone_commit=True,
        is_worktree=False,
    )
    assert policy.should_fire(success=True) is False
    assert policy.should_fire(success=False) is False


def test_policy_for_clone_commit_skill_readonly_success():
    policy = build_clone_guard_policy(
        readonly_skill=True,
        has_write_scope=True,
        is_clone_commit=True,
        is_worktree=False,
    )
    assert policy.should_fire(success=True) is False
    assert policy.should_fire(success=False) is False


def test_policy_for_normal_write_skill():
    policy = build_clone_guard_policy(
        readonly_skill=False,
        has_write_scope=True,
        is_clone_commit=False,
        is_worktree=False,
    )
    assert policy.should_fire(success=True) is True
    assert policy.should_fire(success=False) is True
    assert policy.selective_revert is True


def test_policy_for_worktree_skill():
    """Worktree skill: fire_on_failure=False because is_worktree=True."""
    policy = build_clone_guard_policy(
        is_worktree=True,
        readonly_skill=False,
        has_write_scope=True,
        is_clone_commit=False,
    )
    assert policy.should_fire(success=True) is False
    assert policy.should_fire(success=False) is False


def test_policy_should_snapshot():
    for kwargs, expected in [
        (
            dict(
                is_worktree=True,
                readonly_skill=False,
                has_write_scope=False,
                is_clone_commit=False,
            ),
            True,
        ),
        (
            dict(
                is_worktree=False,
                readonly_skill=True,
                has_write_scope=False,
                is_clone_commit=False,
            ),
            True,
        ),
        (
            dict(
                is_worktree=False,
                readonly_skill=False,
                has_write_scope=True,
                is_clone_commit=False,
            ),
            True,
        ),
        (
            dict(
                is_worktree=False,
                readonly_skill=False,
                has_write_scope=False,
                is_clone_commit=False,
            ),
            False,
        ),
    ]:
        policy = build_clone_guard_policy(**kwargs)
        assert policy.should_snapshot is expected, f"Failed for {kwargs}"


def test_policy_selective_revert():
    for kwargs, expected in [
        (
            dict(
                readonly_skill=True,
                has_write_scope=False,
                is_clone_commit=False,
                is_worktree=False,
            ),
            True,
        ),
        (
            dict(
                readonly_skill=False,
                has_write_scope=True,
                is_clone_commit=False,
                is_worktree=False,
            ),
            True,
        ),
        (
            dict(
                readonly_skill=False,
                has_write_scope=False,
                is_clone_commit=False,
                is_worktree=False,
            ),
            False,
        ),
    ]:
        policy = build_clone_guard_policy(**kwargs)
        assert policy.selective_revert is expected, f"Failed for {kwargs}"


def test_policy_for_write_scope_under_exclude():
    """When write scope is entirely under exclude_prefix, guard must not fire on success."""
    policy = build_clone_guard_policy(
        readonly_skill=True,
        has_write_scope=True,
        is_clone_commit=False,
        is_worktree=False,
        writes_under_exclude=True,
    )
    assert policy.should_fire(success=True) is False
    assert policy.should_fire(success=False) is True
    assert policy.selective_revert is True
    assert policy.should_snapshot is True


def test_policy_output_dir_equals_cwd_suppresses_guard():
    """output_dir == cwd: writes_under_exclude=True → guard suppressed on success."""
    policy = build_clone_guard_policy(
        readonly_skill=False,
        has_write_scope=True,
        is_clone_commit=False,
        is_worktree=False,
        writes_under_exclude=True,
    )
    assert policy.should_fire(success=True) is False
    assert policy.should_fire(success=False) is True


def test_policy_output_dir_outside_cwd_does_not_suppress_guard():
    """outside-cwd write_watch_dirs: writes_under_exclude=False → guard fires on success."""
    policy = build_clone_guard_policy(
        readonly_skill=False,
        has_write_scope=True,
        is_clone_commit=False,
        is_worktree=False,
        writes_under_exclude=False,
    )
    assert policy.should_fire(success=True) is True
    assert policy.should_fire(success=False) is True


def test_policy_for_worktree_skill_failure_no_fire():
    """Worktree skill must not fire the guard on failure regardless of write scope."""
    policy = build_clone_guard_policy(
        is_worktree=True,
        readonly_skill=False,
        has_write_scope=False,
        is_clone_commit=False,
    )
    assert policy.should_fire(success=False) is False


class TestIsCloneCommitSkillPositive:
    def test_resolve_failures(self):
        assert is_clone_commit_skill("/autoskillit:resolve-failures")

    def test_resolve_failures_with_args(self):
        assert is_clone_commit_skill("/autoskillit:resolve-failures issue=42")

    def test_resolve_review(self):
        assert is_clone_commit_skill("/autoskillit:resolve-review")

    def test_resolve_review_with_args(self):
        assert is_clone_commit_skill("/autoskillit:resolve-review pr=99")

    def test_resolve_merge_conflicts(self):
        assert is_clone_commit_skill("/autoskillit:resolve-merge-conflicts")

    def test_resolve_merge_conflicts_with_args(self):
        assert is_clone_commit_skill("/autoskillit:resolve-merge-conflicts branch=main")


class TestIsCloneCommitSkillNegative:
    def test_resolve_ci(self):
        assert not is_clone_commit_skill("/autoskillit:resolve-ci")

    def test_review_pr(self):
        assert not is_clone_commit_skill("/autoskillit:review-pr")

    def test_rectify(self):
        assert not is_clone_commit_skill("/autoskillit:rectify plan.md")

    def test_plain_resolve(self):
        assert not is_clone_commit_skill("resolve")
