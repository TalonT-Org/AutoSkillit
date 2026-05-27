"""Tests for CloneGuardPolicy construction and decision logic."""

from __future__ import annotations

import pytest

from autoskillit.execution.clone_guard import build_clone_guard_policy

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
    assert policy.should_fire(success=False) is True


def test_policy_for_clone_commit_skill_readonly_success():
    policy = build_clone_guard_policy(
        readonly_skill=True,
        has_write_scope=True,
        is_clone_commit=True,
        is_worktree=False,
    )
    assert policy.should_fire(success=True) is False
    assert policy.should_fire(success=False) is True


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
    policy = build_clone_guard_policy(
        is_worktree=True,
        readonly_skill=False,
        has_write_scope=True,
        is_clone_commit=False,
    )
    assert policy.should_fire(success=True) is False
    assert policy.should_fire(success=False) is True


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
