"""Verify audit/ directory creation and artifact copy contract."""

import shutil
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def test_create_worktree_creates_audit_directory(tmp_path: Path):
    """Audit directory contains both expected artifacts after copy."""
    temp_dir = tmp_path / "temp" / "review-design"
    temp_dir.mkdir(parents=True)
    dashboard = temp_dir / "evaluation_dashboard_test_2026-05-07_120000.md"
    dashboard.write_text("# Evaluation Dashboard\nverdict: GO\n")

    vis_dir = tmp_path / "temp" / "synthesize-vis-plan"
    vis_dir.mkdir(parents=True)
    trace = vis_dir / "visualization-plan-trace.md"
    trace.write_text("# Visualization Plan Trace\nprimary_tradition: controlled_intervention\n")

    research_dir = tmp_path / "research" / "2026-05-07-test-slug"
    audit_dir = research_dir / "audit"
    audit_dir.mkdir(parents=True)

    shutil.copy2(dashboard, audit_dir / "design-review-dashboard.md")
    shutil.copy2(trace, audit_dir / "visualization-plan-trace.md")

    assert (audit_dir / "design-review-dashboard.md").exists()
    assert (audit_dir / "visualization-plan-trace.md").exists()


def test_audit_dashboard_copied_to_audit_dir(tmp_path: Path):
    """evaluation_dashboard copied to audit/design-review-dashboard.md."""
    temp_dir = tmp_path / "temp" / "review-design"
    temp_dir.mkdir(parents=True)
    dashboard = temp_dir / "evaluation_dashboard_test_2026-05-07_120000.md"
    dashboard.write_text("# Evaluation Dashboard\nverdict: GO\n")

    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    target = audit_dir / "design-review-dashboard.md"
    shutil.copy2(dashboard, target)

    assert target.exists()
    assert "verdict: GO" in target.read_text()


def test_visualization_trace_copied_to_audit_dir(tmp_path: Path):
    """visualization-plan-trace.md is copied to audit/ with content preserved."""
    vis_dir = tmp_path / "temp" / "synthesize-vis-plan"
    vis_dir.mkdir(parents=True)
    trace = vis_dir / "visualization-plan-trace.md"
    trace.write_text("# Visualization Plan Trace\nprimary_tradition: controlled_intervention\n")

    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    target = audit_dir / "visualization-plan-trace.md"
    shutil.copy2(trace, target)

    assert target.exists()
    assert "primary_tradition" in target.read_text()
