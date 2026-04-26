"""Unit tests for _push_trigger_applies_to_branch — the branch-aware push trigger parser."""

import pytest

from autoskillit.execution.merge_queue import _push_trigger_applies_to_branch

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
