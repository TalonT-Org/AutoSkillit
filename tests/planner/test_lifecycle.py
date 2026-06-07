"""Tests for autoskillit.planner.lifecycle — category enum immunity and registry behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("planner"), pytest.mark.small, pytest.mark.feature("planner")]


def test_lifecycle_category_enum_exhaustive() -> None:
    """Importing lifecycle module does not raise — every enum member is in defaults."""
    from autoskillit.planner.lifecycle import (  # noqa: F401
        LIFECYCLE_CATEGORY_DEFAULTS,
        LifecycleCategory,
    )


def test_record_lifecycle_event_preserves_all_categories(tmp_path: Path) -> None:
    """Multi-write round-trip preserves all category data (regression for the data-loss bug)."""
    from autoskillit.planner.lifecycle import (
        LifecycleCategory,
        load_lifecycle_registry,
        record_lifecycle_event,
    )

    record_lifecycle_event(
        tmp_path,
        LifecycleCategory.ARCHIVED_STUBS,
        {"P1-A1-WP1": {"reason": "elaboration_failed_orphan"}},
    )
    record_lifecycle_event(
        tmp_path,
        LifecycleCategory.VOIDED_WPS,
        {"P2-A1-WP1": {"merged_into": "P2-A1-WP2", "reason": "duplicate"}},
    )

    registry = load_lifecycle_registry(tmp_path)
    assert "archived_stubs" in registry
    assert registry["archived_stubs"]["P1-A1-WP1"]["reason"] == "elaboration_failed_orphan"
    assert "voided_wps" in registry
    assert registry["voided_wps"]["P2-A1-WP1"]["merged_into"] == "P2-A1-WP2"


def test_check_assignment_completeness_exempts_archived_stubs() -> None:
    """Assignment is exempt when an archived_stubs WP maps to its (phase, assignment) pair."""
    from autoskillit.planner.validation import _check_assignment_completeness

    assignment_results = {
        "P1-A1": {"phase_number": 1, "assignment_number": 1},
    }
    wp_results: dict = {}
    lifecycle_registry = {
        "archived_stubs": {"P1-A1-WP1": {"reason": "elaboration_failed_orphan"}},
        "voided_phases": [],
        "voided_assignments": [],
        "absorbed": {},
        "voided_wps": {},
    }

    findings = _check_assignment_completeness(assignment_results, wp_results, lifecycle_registry)
    assert not any(f["check"] == "assignment_completeness" for f in findings)


def test_check_dep_references_exempts_archived_stubs() -> None:
    """dep_references check exempts deps in archived_stubs (same as voided_wps)."""
    from autoskillit.planner.validation import _check_dep_references

    wp_results = {
        "P1-A1-WP1": {"id": "P1-A1-WP1", "depends_on": ["P1-A2-WP1"]},
    }
    lifecycle_registry = {
        "archived_stubs": {"P1-A2-WP1": {"reason": "elaboration_failed_orphan"}},
        "voided_phases": [],
        "voided_assignments": [],
        "absorbed": {},
        "voided_wps": {},
    }

    findings = _check_dep_references(wp_results, lifecycle_registry)
    assert not any(f["check"] == "dep_references" for f in findings)


def test_load_lifecycle_registry_defaults_cover_all_categories(tmp_path: Path) -> None:
    """load_lifecycle_registry returns a dict with all category keys even when no file exists."""
    from autoskillit.planner.lifecycle import LifecycleCategory, load_lifecycle_registry

    registry = load_lifecycle_registry(tmp_path / "nonexistent")
    for cat in LifecycleCategory:
        assert cat.value in registry, f"{cat.value} missing from defaults"
