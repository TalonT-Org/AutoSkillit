"""Tests for pipeline.branch_guard — protected-branch validation."""

import pytest

from autoskillit.core.branch_guard import is_protected_branch

_DEFAULTS = ["main", "integration", "stable"]

# ---------- standard protected list ----------


@pytest.mark.parametrize("branch", ["main", "integration", "stable"])
def test_default_protected_branches_are_rejected(branch: str) -> None:
    assert is_protected_branch(branch, protected=_DEFAULTS) is True


@pytest.mark.parametrize(
    "branch",
    [
        "feat/add-widget",
        "fix/issue-42",
        "impl-20260311-123456",
        "main-backup",
        "integration-test",
    ],
)
def test_non_protected_branches_are_allowed(branch: str) -> None:
    assert is_protected_branch(branch, protected=_DEFAULTS) is False


# ---------- custom protected list ----------


def test_custom_protected_list_overrides_defaults() -> None:
    custom = ["release", "production"]
    assert is_protected_branch("release", protected=custom) is True
    assert is_protected_branch("main", protected=custom) is False


def test_empty_protected_list_allows_everything() -> None:
    assert is_protected_branch("main", protected=[]) is False


# ---------- edge cases ----------


def test_empty_string_is_not_protected() -> None:
    assert is_protected_branch("", protected=_DEFAULTS) is False


def test_case_sensitive_match() -> None:
    """Branch names are case-sensitive in git."""
    assert is_protected_branch("Main", protected=_DEFAULTS) is False
    assert is_protected_branch("MAIN", protected=_DEFAULTS) is False
