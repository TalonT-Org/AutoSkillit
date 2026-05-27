"""Unit tests for _push_trigger_applies_to_branch and _has_merge_group_trigger."""

import pytest

from autoskillit.execution.merge_queue import (
    _has_merge_group_trigger,
    _has_pull_request_trigger_for_base,
    _push_trigger_applies_to_branch,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


def test_branch_filter_excludes_feature():
    text = "on:\n  push:\n    branches:\n      - main\n      - stable\n"
    assert _push_trigger_applies_to_branch(text, "feature/foo") is False


def test_branch_filter_matches_listed_branch():
    text = "on:\n  push:\n    branches:\n      - main\n      - stable\n"
    assert _push_trigger_applies_to_branch(text, "main") is True


def test_no_branch_filter_matches_all():
    text = "on:\n  push:\n"
    assert _push_trigger_applies_to_branch(text, "feature/anything") is True


def test_scalar_push_form_matches_all():
    assert _push_trigger_applies_to_branch("on: push\n", "feature/x") is True


def test_list_push_form_matches_all():
    assert _push_trigger_applies_to_branch("on: [push, pull_request]\n", "feature/x") is True


def test_branches_ignore_excludes_listed():
    text = "on:\n  push:\n    branches-ignore:\n      - main\n      - stable\n"
    assert _push_trigger_applies_to_branch(text, "main") is False


def test_branches_ignore_allows_unlisted():
    text = "on:\n  push:\n    branches-ignore:\n      - main\n      - stable\n"
    assert _push_trigger_applies_to_branch(text, "feature/foo") is True


def test_glob_pattern_matches():
    text = "on:\n  push:\n    branches:\n      - 'feature/**'\n"
    assert _push_trigger_applies_to_branch(text, "feature/impl-20260401") is True


def test_glob_pattern_excludes_non_matching():
    text = "on:\n  push:\n    branches:\n      - 'feature/**'\n"
    assert _push_trigger_applies_to_branch(text, "main") is False


def test_no_push_trigger_returns_false():
    text = "on:\n  pull_request:\n    branches: [main]\n"
    assert _push_trigger_applies_to_branch(text, "main") is False


def test_yaml_parse_failure_falls_back_to_heuristic_safe():
    # Invalid YAML that doesn't contain any push trigger substrings
    assert _push_trigger_applies_to_branch(": [\n", "main") is False


def test_main_only_push_excludes_pr_batch_branch():
    """push: branches: [main] must NOT fire for pr-batch/* branches.

    This is the exact failure mode from issue #2599: the recipe derived
    ci_event='push' for main, then reused it for pr-batch/pr-merge-* branches
    where GitHub Actions never fires.
    """
    text = "on:\n  push:\n    branches:\n      - main\n"
    assert _push_trigger_applies_to_branch(text, "pr-batch/pr-merge-20260518-194529") is False


def test_main_only_push_excludes_worktree_branch():
    """push: branches: [main] must NOT fire for impl-* worktree branches."""
    text = "on:\n  push:\n    branches:\n      - main\n"
    assert _push_trigger_applies_to_branch(text, "impl-rectify-ci-event-20260518-180226") is False


# ---------------------------------------------------------------------------
# _has_merge_group_trigger tests
# ---------------------------------------------------------------------------


def test_merge_group_in_on_list():
    assert _has_merge_group_trigger("on: [push, merge_group]") is True


def test_merge_group_as_dict_key():
    assert _has_merge_group_trigger("on:\n  merge_group:\n  push:") is True


def test_merge_group_scalar():
    assert _has_merge_group_trigger("on: merge_group") is True


def test_no_merge_group_trigger():
    assert _has_merge_group_trigger("on: [push, pull_request]") is False


def test_merge_group_in_comment_only():
    assert _has_merge_group_trigger("# merge_group\non: push") is False


def test_merge_group_in_shell_string():
    text = "on: push\njobs:\n  test:\n    steps:\n      - run: echo merge_group"
    assert _has_merge_group_trigger(text) is False


def test_yaml_parse_failure_falls_back_to_heuristic():
    assert _has_merge_group_trigger("{invalid yaml merge_group") is True


# ---------------------------------------------------------------------------
# _has_pull_request_trigger_for_base tests
# ---------------------------------------------------------------------------


def test_pr_trigger_branches_matches_base():
    text = "on:\n  pull_request:\n    branches: [develop]\n"
    assert _has_pull_request_trigger_for_base(text, "develop") is True


def test_pr_trigger_branches_excludes_other_base():
    text = "on:\n  pull_request:\n    branches: [main]\n"
    assert _has_pull_request_trigger_for_base(text, "develop") is False


def test_pr_trigger_branches_ignore_allows_base():
    text = "on:\n  pull_request:\n    branches-ignore: [release-*]\n"
    assert _has_pull_request_trigger_for_base(text, "develop") is True


def test_pr_trigger_no_branches_filter_matches_all():
    text = "on:\n  pull_request:\n"
    assert _has_pull_request_trigger_for_base(text, "develop") is True


def test_pr_trigger_list_form_matches_all():
    assert _has_pull_request_trigger_for_base("on: [pull_request]", "main") is True


def test_pr_trigger_scalar_form_matches_all():
    assert _has_pull_request_trigger_for_base("on: pull_request", "main") is True


def test_no_pr_trigger_returns_false():
    text = "on:\n  push:\n    branches: [main]\n"
    assert _has_pull_request_trigger_for_base(text, "main") is False


def test_pr_trigger_yaml_parse_failure_falls_back_to_substring():
    assert _has_pull_request_trigger_for_base("{invalid yaml pull_request", "main") is True


def test_pr_trigger_yaml_parse_failure_no_substring_returns_false():
    assert _has_pull_request_trigger_for_base("{invalid yaml on: push", "main") is False
