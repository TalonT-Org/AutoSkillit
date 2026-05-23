"""Structural guard: validates test file split imports."""

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def test_classify_fix_tools_importable():
    from autoskillit.server.tools.tools_git import classify_fix

    assert callable(classify_fix)


def test_merge_worktree_tools_importable():
    from autoskillit.server.tools.tools_git import merge_worktree

    assert callable(merge_worktree)


def test_branch_tools_importable():
    from autoskillit.server.tools.tools_git import check_pr_mergeable, create_unique_branch

    assert callable(create_unique_branch)
    assert callable(check_pr_mergeable)
