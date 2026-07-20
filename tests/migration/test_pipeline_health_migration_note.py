"""Tests for the diagnostics pipeline-health config migration note."""

import pytest

from autoskillit.core import load_yaml, pkg_root

pytestmark = [pytest.mark.layer("migration"), pytest.mark.small]


def test_pipeline_health_config_rename_has_migration_note() -> None:
    """The persisted diagnostics key rename must have actionable migration guidance."""
    note_path = pkg_root() / "migrations" / "0.10.884-to-0.10.885.yaml"
    note = load_yaml(note_path)
    changes = note["changes"]
    change = next(
        item
        for item in changes
        if item.get("detect", {}).get("key") == "diagnostics.post_run_analysis"
    )

    assert note["from_version"] == "0.10.884"
    assert note["to_version"] == "0.10.885"
    assert change["instruction"].strip()
    assert "post_run_analysis" in change["example_before"]
    assert "pipeline_health" in change["example_after"]
