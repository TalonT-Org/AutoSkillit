"""Flag metadata coverage: every ClaudeFlags member must be categorized."""

from __future__ import annotations

import pytest

from autoskillit.core import NON_VARIADIC_CLAUDE_FLAGS, VARIADIC_CLAUDE_FLAGS, ClaudeFlags

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def test_every_claude_flag_is_categorized():
    all_flags = frozenset(ClaudeFlags)
    categorized = VARIADIC_CLAUDE_FLAGS | NON_VARIADIC_CLAUDE_FLAGS
    uncategorized = all_flags - categorized
    assert not uncategorized, (
        f"ClaudeFlags members not categorized as variadic (repeating) "
        f"or non-variadic (single-occurrence): {uncategorized}. "
        f"Add to VARIADIC_CLAUDE_FLAGS or NON_VARIADIC_CLAUDE_FLAGS."
    )


def test_variadic_and_non_variadic_are_disjoint():
    overlap = VARIADIC_CLAUDE_FLAGS & NON_VARIADIC_CLAUDE_FLAGS
    assert not overlap, f"Flags in both sets: {overlap}"


def test_all_categorized_flags_are_valid_members():
    all_flags = frozenset(ClaudeFlags)
    categorized = VARIADIC_CLAUDE_FLAGS | NON_VARIADIC_CLAUDE_FLAGS
    invalid = categorized - all_flags
    assert not invalid, f"Categorized flags not in ClaudeFlags: {invalid}"
