"""Declared test-support scopes must cover the static dependents they replace (#5195)."""

from pathlib import Path

import pytest

from tests._test_filter import (
    TEST_HELPER_CASCADE,
    build_test_import_index,
    resolve_support_dependents,
)

pytestmark = [pytest.mark.layer("arch"), pytest.mark.medium]

_TESTS_ROOT = Path(__file__).resolve().parent.parent


def _covers(declared: frozenset[str], target: str) -> bool:
    return any(target == entry or target.startswith(f"{entry}/") for entry in declared)


def test_declared_helper_targets_exist() -> None:
    missing: list[str] = []
    for key, targets in TEST_HELPER_CASCADE.items():
        if not (_TESTS_ROOT.parent / key).is_file():
            missing.append(f"{key}: missing support module")
        missing.extend(
            f"{key}: missing target {target}"
            for target in sorted(targets)
            if not (_TESTS_ROOT / target).exists()
        )
    assert not missing, "Declared support paths must exist:\n" + "\n".join(missing)


def test_declared_helpers_cover_static_dependents() -> None:
    import_index = build_test_import_index(_TESTS_ROOT)
    uncovered: list[str] = []
    for key, targets in TEST_HELPER_CASCADE.items():
        missing = sorted(
            target
            for target in resolve_support_dependents(key, import_index).targets
            if not _covers(targets, target)
        )
        if missing:
            uncovered.append(f"{key}: {', '.join(missing)}")
    assert not uncovered, (
        "Each declared entry bypasses the static dependent scan and must list these targets:\n"
        + "\n".join(uncovered)
    )
