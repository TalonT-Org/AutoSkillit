"""Flag metadata coverage: every CodexFlags member must be categorized."""

from __future__ import annotations

import pytest

from autoskillit.execution.backends.codex import (
    NON_VARIADIC_CODEX_FLAGS,
    VARIADIC_CODEX_FLAGS,
    CodexFlags,
)
from autoskillit.execution.headless._headless_helpers import _CODEX_VALUE_BEARING_FLAGS

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def test_every_codex_flag_is_categorized():
    all_flags = frozenset(CodexFlags)
    categorized = VARIADIC_CODEX_FLAGS | NON_VARIADIC_CODEX_FLAGS
    uncategorized = all_flags - categorized
    assert not uncategorized, (
        f"CodexFlags members not categorized as variadic (repeating) "
        f"or non-variadic (single-occurrence): {uncategorized}. "
        f"Add to VARIADIC_CODEX_FLAGS or NON_VARIADIC_CODEX_FLAGS."
    )


def test_variadic_and_non_variadic_codex_are_disjoint():
    overlap = VARIADIC_CODEX_FLAGS & NON_VARIADIC_CODEX_FLAGS
    assert not overlap, f"Flags in both sets: {overlap}"


def test_all_categorized_codex_flags_are_valid_members():
    all_flags = frozenset(CodexFlags)
    categorized = VARIADIC_CODEX_FLAGS | NON_VARIADIC_CODEX_FLAGS
    invalid = categorized - all_flags
    assert not invalid, f"Categorized flags not in CodexFlags: {invalid}"


def test_codex_value_bearing_flags_subset_of_categorized():
    categorized = VARIADIC_CODEX_FLAGS | NON_VARIADIC_CODEX_FLAGS
    all_flags = frozenset(CodexFlags)
    for entry in _CODEX_VALUE_BEARING_FLAGS:
        assert entry in all_flags, (
            f"_CODEX_VALUE_BEARING_FLAGS entry {entry!r} is not a live CodexFlags member"
        )
        assert entry in categorized, (
            f"_CODEX_VALUE_BEARING_FLAGS entry {entry!r} is not in "
            f"VARIADIC_CODEX_FLAGS or NON_VARIADIC_CODEX_FLAGS"
        )


def test_codex_model_aliases_and_effort_mapping_key_parity():
    from autoskillit.core import CODEX_EFFORT_MAPPING, CODEX_MODEL_ALIASES

    assert CODEX_MODEL_ALIASES.keys() == CODEX_EFFORT_MAPPING.keys(), (
        f"CODEX_MODEL_ALIASES keys {set(CODEX_MODEL_ALIASES)} != "
        f"CODEX_EFFORT_MAPPING keys {set(CODEX_EFFORT_MAPPING)}"
    )
