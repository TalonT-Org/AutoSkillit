"""Frozen ledger guarding Git-tracked recipe discovery names."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._tracked_recipes import tracked_recipe_names

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_LEDGER_PATH = Path(__file__).with_name("recipe_name_ledger.txt")


def _read_ledger_lines() -> list[str]:
    return [
        stripped
        for raw_line in _LEDGER_PATH.read_text(encoding="utf-8").splitlines()
        if (stripped := raw_line.strip()) and not stripped.startswith("#")
    ]


_TRACKED_RECIPE_NAMES = set(tracked_recipe_names(_PROJECT_ROOT))


def test_no_silent_recipe_additions() -> None:
    ledger_names = set(_read_ledger_lines())
    missing = sorted(_TRACKED_RECIPE_NAMES - ledger_names)
    assert not missing, (
        f"Tracked recipe name(s) missing from {_LEDGER_PATH}: {missing}. "
        "Add them to the frozen ledger in this change."
    )


def test_no_silent_recipe_removals() -> None:
    ledger_names = set(_read_ledger_lines())
    removed = sorted(ledger_names - _TRACKED_RECIPE_NAMES)
    assert not removed, (
        f"Recipe ledger name(s) are no longer tracked: {removed}. "
        "Remove or rename their ledger entries in this change."
    )


def test_ledger_is_sorted() -> None:
    assert _LEDGER_PATH.is_file(), f"Missing recipe name ledger: {_LEDGER_PATH}"
    raw_text = _LEDGER_PATH.read_text(encoding="utf-8")
    assert raw_text.strip(), f"Recipe name ledger is empty: {_LEDGER_PATH}"
    ledger_lines = _read_ledger_lines()
    ledger_names = set(ledger_lines)
    assert len(ledger_lines) == len(ledger_names), (
        f"{_LEDGER_PATH} contains duplicate recipe names."
    )
    assert ledger_lines == sorted(ledger_lines), f"{_LEDGER_PATH} recipe names must be sorted."
