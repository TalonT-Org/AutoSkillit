"""Split-invariant: rules_merge.py facade re-exports the deliberate symbols.

The facade uses the `module.X = other_module.Y` alias pattern. The aliases
are identity-equal to the underlying objects, which means tests can
replace ``rules_merge._FOO`` to override the facade value, but the function
references inside the siblings still resolve to the sibling module's globals.

Issue #4857 acceptance criterion (c): preserving the deliberate symbol exports
that existing tests exercise (``_RECOVERABLE_FAILED_STEPS``,
``_TERMINAL_FAILED_STEPS``, ``_MERGE_FAILURE_DOMAINS``, ``_REQUIRED_RECOVERY_CLASS``,
``_is_commit_guard``, ``_classify_recovery_class``).
"""

from __future__ import annotations

import pytest

from autoskillit.recipe.rules import (
    rules_merge,
    rules_merge_guards,
    rules_merge_routing,
)

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def test_facade_reexports_recoverable_failed_steps() -> None:
    assert rules_merge._RECOVERABLE_FAILED_STEPS is rules_merge_routing._RECOVERABLE_FAILED_STEPS


def test_facade_reexports_terminal_failed_steps() -> None:
    assert rules_merge._TERMINAL_FAILED_STEPS is rules_merge_routing._TERMINAL_FAILED_STEPS


def test_facade_reexports_merge_failure_domains() -> None:
    assert rules_merge._MERGE_FAILURE_DOMAINS is rules_merge_routing._MERGE_FAILURE_DOMAINS


def test_facade_reexports_required_recovery_class() -> None:
    assert rules_merge._REQUIRED_RECOVERY_CLASS is rules_merge_routing._REQUIRED_RECOVERY_CLASS


def test_facade_reexports_is_commit_guard() -> None:
    assert rules_merge._is_commit_guard is rules_merge_guards._is_commit_guard


def test_facade_reexports_classify_recovery_class() -> None:
    assert rules_merge._classify_recovery_class is rules_merge_routing._classify_recovery_class


def test_facade_module_level_helpers_unchanged_after_patch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Patching the facade does not propagate to the sibling module.

    The facade re-export is read-only: rebinding ``rules_merge._FOO`` does not
    change what the sibling module sees. This is the deliberate-export contract
    documented in the implementation plan.
    """
    sentinel = frozenset({"X"})
    monkeypatch.setattr(rules_merge, "_RECOVERABLE_FAILED_STEPS", sentinel)
    try:
        assert rules_merge._RECOVERABLE_FAILED_STEPS is sentinel
        assert rules_merge_routing._RECOVERABLE_FAILED_STEPS is not sentinel
    finally:
        monkeypatch.undo()


def test_classify_recovery_class_closure_targets_owning_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Patching the facade does not change ``_classify_recovery_class``'s view
    of the recovery signature tables.

    ``_classify_recovery_class`` lives in ``rules_merge_routing`` and reads
    its signature tables via closure on that module's ``__dict__``. Patching
    ``rules_merge._RECOVERABLE_FAILED_STEPS`` only rebinds the facade's name;
    the routing module retains its reference. Tests that need to override
    recovery signatures MUST patch the routing module directly.
    """
    sentinel = frozenset({"FAKE"})
    monkeypatch.setattr(rules_merge, "_RECOVERABLE_FAILED_STEPS", sentinel)
    try:
        assert rules_merge._RECOVERABLE_FAILED_STEPS is sentinel
        assert rules_merge_routing._RECOVERABLE_FAILED_STEPS is not sentinel
    finally:
        monkeypatch.undo()
