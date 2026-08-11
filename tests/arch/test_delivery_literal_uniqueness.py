"""Arch guard: delivery-bound numeric literals must appear exactly once in non-hooks src/."""

from __future__ import annotations

import re

import pytest

from tests.arch._helpers import SRC_ROOT

pytestmark = [pytest.mark.layer("arch"), pytest.mark.small]


def _count_literal_occurrences(
    literal: int,
    *,
    exclude_dirs: tuple[str, ...] = ("hooks",),
) -> list[str]:
    """Return list of modules containing the given numeric literal."""
    found: list[str] = []
    pattern = re.compile(rf"\b{literal}\b|{literal:_}")
    for py_file in SRC_ROOT.rglob("*.py"):
        rel = py_file.relative_to(SRC_ROOT)
        if any(part in exclude_dirs for part in rel.parts):
            continue
        source = py_file.read_text()
        if pattern.search(source):
            found.append(str(rel))
    return found


def test_195000_appears_in_exactly_one_non_hooks_module() -> None:
    """195,000 (RECIPE_RESPONSE_MAX_UTF8_BYTES) must be defined in one module."""
    found = _count_literal_occurrences(195_000)
    assert len(found) == 1, (
        f"195_000/195000 must appear in exactly one non-hooks module, found {len(found)}:\n"
        + "\n".join(f"  {f}" for f in sorted(found))
    )


def test_90000_appears_in_exactly_one_non_hooks_module() -> None:
    """90,000 (RECIPE_RESPONSE_DEFAULT_BYTES) must be defined in one module."""
    found = _count_literal_occurrences(90_000)
    assert len(found) == 1, (
        f"90_000/90000 must appear in exactly one non-hooks module, found {len(found)}:\n"
        + "\n".join(f"  {f}" for f in sorted(found))
    )


# Delivery-relevant packages: where recipe-delivery gate literals (client-side
# token/char gates) are expected to live. Scoped narrower than
# `_count_literal_occurrences`'s hooks-only exclusion because these bare
# literals (25_000, 50_000, 500_000) are common round numbers that appear
# incidentally elsewhere in the codebase (timeouts, buffer sizes, etc.) —
# scoping to the packages that actually reason about delivery gates avoids
# false positives from those unrelated numeric literals.
_DELIVERY_RELEVANT_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("config",),
    ("core", "types"),
    ("server",),
    ("execution", "backends"),
)


def _count_scoped_literal_occurrences(literal: int) -> list[str]:
    """Return delivery-relevant modules containing the given numeric literal."""
    found: list[str] = []
    pattern = re.compile(rf"\b{literal}\b|{literal:_}")
    for py_file in SRC_ROOT.rglob("*.py"):
        rel = py_file.relative_to(SRC_ROOT)
        if not any(rel.parts[: len(prefix)] == prefix for prefix in _DELIVERY_RELEVANT_PREFIXES):
            continue
        source = py_file.read_text()
        if pattern.search(source):
            found.append(str(rel))
    return found


def test_25000_appears_in_exactly_one_delivery_relevant_module() -> None:
    """25,000 (CLAUDE_DEFAULT_CLIENT_RESULT_TOKENS) must be defined in one module."""
    found = _count_scoped_literal_occurrences(25_000)
    assert len(found) == 1, (
        f"25_000/25000 must appear in exactly one delivery-relevant module, found {len(found)}:\n"
        + "\n".join(f"  {f}" for f in sorted(found))
    )


def test_50000_appears_in_exactly_one_delivery_relevant_module() -> None:
    """50,000 (CLAUDE_INJECTED_CLIENT_RESULT_TOKENS) must be defined in one module."""
    found = _count_scoped_literal_occurrences(50_000)
    assert len(found) == 1, (
        f"50_000/50000 must appear in exactly one delivery-relevant module, found {len(found)}:\n"
        + "\n".join(f"  {f}" for f in sorted(found))
    )


def test_500000_appears_in_exactly_one_delivery_relevant_module() -> None:
    """500,000 (ANNOTATION_HARD_CAP_CHARS) must be defined in one module."""
    found = _count_scoped_literal_occurrences(500_000)
    assert len(found) == 1, (
        f"500_000/500000 must appear in exactly one delivery-relevant module, "
        f"found {len(found)}:\n" + "\n".join(f"  {f}" for f in sorted(found))
    )
