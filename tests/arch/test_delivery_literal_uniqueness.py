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
