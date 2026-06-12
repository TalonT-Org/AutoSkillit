"""Cross-registry dispatch sufficiency: real HOOK_REGISTRY × real BackendCapabilities.

Regression suite for issue #4082 — guards against a class of bug where HOOK_REGISTRY
fix-required entries silently brick dispatch on backends whose applicable_guards do
not cover the hook's script stems. The dispatch gate in tools_execution._check_backend_compat
correctly refuses dispatch in this state, but the bug class had no test that crossed the
HOOK_REGISTRY ↔ BackendCapabilities boundary until now.

These tests exercise the real registries through the real gate logic, not synthetic
monkeypatched copies. This is the defense-in-depth layer that catches reclassifications
which would brick dispatch silently.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.execution.backends import BACKEND_REGISTRY
from autoskillit.hook_registry import HOOK_REGISTRY
from autoskillit.server.tools.tools_execution import _get_fix_required_hook_matchers

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def _collect_fix_required_stems() -> set[str]:
    """Extract unique script stems from all fix-required hooks in HOOK_REGISTRY."""
    stems: set[str] = set()
    for h in HOOK_REGISTRY:
        if h.codex_status == "fix-required":
            stems.update(Path(s).stem for s in h.scripts)
    return stems


def _all_fix_required_matchers() -> list[str]:
    """Extract matchers from all fix-required hooks in HOOK_REGISTRY."""
    return [h.matcher for h in HOOK_REGISTRY if h.codex_status == "fix-required"]


@pytest.mark.parametrize("backend_name,backend_cls", sorted(BACKEND_REGISTRY.items()))
def test_no_backend_bricked_by_fix_required_hooks(backend_name: str, backend_cls: type) -> None:
    """Every backend's applicable_guards must cover all fix-required hook scripts.

    Runs the exact same function the dispatch gate uses — _get_fix_required_hook_matchers —
    against the real HOOK_REGISTRY and the real backend's real applicable_guards set.
    A non-empty result means this backend would be bricked at dispatch time.
    """
    backend = backend_cls()
    blockers = _get_fix_required_hook_matchers(backend.capabilities.applicable_guards)
    assert not blockers, (
        f"Backend {backend_name!r} would be bricked at dispatch by fix-required "
        f"hooks with matchers {blockers}. Either reclassify the hook's codex_status "
        f"or add the missing guard to applicable_guards."
    )


@pytest.mark.parametrize("backend_name,backend_cls", sorted(BACKEND_REGISTRY.items()))
def test_applicable_guards_covers_all_fix_required_scripts(
    backend_name: str, backend_cls: type
) -> None:
    """Set-level invariant: applicable_guards ⊇ fix-required hook script stems.

    A parallel, structural assertion that doesn't go through the gate function.
    Detects the same class of bug from the registry-composition angle.
    """
    backend = backend_cls()
    fix_required_stems = _collect_fix_required_stems()
    missing = fix_required_stems - backend.capabilities.applicable_guards
    assert not missing, (
        f"Backend {backend_name!r} applicable_guards is missing script stems "
        f"{sorted(missing)} required by fix-required hooks in HOOK_REGISTRY."
    )


def test_test_matrix_covers_all_registered_backends() -> None:
    """Meta-test: the parametrized matrix must cover every backend in BACKEND_REGISTRY.

    Prevents silent matrix shrinkage if a backend is removed from parametrization.
    """
    expected = {"claude-code", "codex"}
    actual = set(BACKEND_REGISTRY.keys())
    assert len(actual) >= 2, (
        f"BACKEND_REGISTRY has only {len(actual)} backend(s) — "
        f"expected at least 2 (claude-code + codex). If a backend was removed, "
        f"update this lower bound."
    )
    assert actual == expected, (
        f"BACKEND_REGISTRY keys {sorted(actual)} != expected {sorted(expected)}. "
        f"Update the expected set in this meta-test when backends are added or removed."
    )
