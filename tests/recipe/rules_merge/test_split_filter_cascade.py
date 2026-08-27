"""Split-invariant: every rules_merge* source file has a cascade entry, and
the entries point at files that exist on disk.

Issue #4857 acceptance criterion (d): the test-filter cascade is updated for
every new rule path. Pre-existing siblings ``rules_merge_context`` and
``rules_merge_queue`` were silently missing; this test guards against that
gap re-emerging.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import tests._test_filter as _test_filter

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RULES_DIR = _REPO_ROOT / "src" / "autoskillit" / "recipe" / "rules"
_CASCADE = _test_filter.MODULE_CASCADE_RECIPE

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


@pytest.mark.parametrize(
    "stem",
    [
        "rules_merge",
        "rules_merge_routing",
        "rules_merge_guards",
        "rules_merge_wait",
        "rules_merge_enrollment",
        "rules_merge_push_symmetry",
        "rules_merge_context",
        "rules_merge_queue",
    ],
)
def test_module_cascade_includes_each_sibling(stem: str) -> None:
    assert stem in _CASCADE, (
        f"Missing {_test_filter.__name__}.MODULE_CASCADE_RECIPE entry for "
        f"{stem!r}. Available merge entries: "
        f"{sorted(k for k in _CASCADE if k.startswith('rules_merge'))!r}"
    )
    assert _CASCADE[stem] == frozenset({"recipe"})


def test_facade_cascade_key_preserved() -> None:
    """The original ``rules_merge`` entry must remain unchanged after the split."""
    assert _CASCADE["rules_merge"] == frozenset({"recipe"})


def test_cascade_includes_pre_existing_merge_siblings() -> None:
    """Pre-existing siblings must keep their cascade coverage.

    The split closes a pre-existing gap: ``rules_merge_context`` and
    ``rules_merge_queue`` were silently missing from the cascade before #4857.
    """
    assert "rules_merge_context" in _CASCADE
    assert "rules_merge_queue" in _CASCADE


@pytest.mark.parametrize(
    "stem",
    [
        "rules_merge",
        "rules_merge_routing",
        "rules_merge_guards",
        "rules_merge_wait",
        "rules_merge_enrollment",
        "rules_merge_push_symmetry",
        "rules_merge_context",
        "rules_merge_queue",
    ],
)
def test_cascade_keys_resolve_to_existing_files(stem: str) -> None:
    """Cascade keys must point at a real file under ``src/autoskillit/recipe/rules/``.

    The cascade-map guard at ``tests/arch/test_cascade_map_guard.py`` checks
    AST-consumer presence but NOT file existence. This test catches typos like
    ``rules_merge_guaurds`` that the existing guard would miss.
    """
    target = _RULES_DIR / f"{stem}.py"
    assert target.exists(), f"Cascade stem {stem!r} resolves to {target}, which does not exist."
