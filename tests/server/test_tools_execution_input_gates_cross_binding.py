"""Cross-binding tests for prepare-pr and open-integration-pr, validating how
the bundled manifest resolves comma-joined plans / conflict reports to the
correct path positions.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestPreparePrCrossBinding:
    """Bundled prepare-pr cross-binding: comma-joined plans must bind plan_paths at position 0."""

    def test_prepare_pr_actual_recipe_shape_accepts_comma_plan_paths(self, tmp_path) -> None:
        """Bundled recipe shape with comma-joined plans must clear the gate."""
        from autoskillit.recipe._contracts_manifest import resolve_input_specs
        from autoskillit.server._guards import _check_input_contracts

        plan_a = tmp_path / "plan_a.md"
        plan_a.write_text("plan a")
        plan_b = tmp_path / "plan_b.md"
        plan_b.write_text("plan b")
        joined = f"{plan_a},{plan_b}"
        cmd = f"/autoskillit:prepare-pr {joined} my-run main 123 true"
        result = _check_input_contracts(cmd, str(tmp_path), resolve_input_specs)
        assert result is None, f"Expected acceptance, got: {result!r}"

    def test_prepare_pr_plan_list_and_conflict_report_bind_separately(self, tmp_path) -> None:
        """Comma-joined plans plus optional conflict-report must bind as two path specs."""
        from autoskillit.recipe._contracts_manifest import resolve_input_specs
        from autoskillit.server._guards import _check_input_contracts

        plan_a = tmp_path / "plan_a.md"
        plan_a.write_text("plan a")
        plan_b = tmp_path / "plan_b.md"
        plan_b.write_text("plan b")
        conflict = tmp_path / "conflict.md"
        conflict.write_text("conflict")
        joined = f"{plan_a},{plan_b}"
        cmd = f"/autoskillit:prepare-pr {joined} my-run main 123 true {conflict}"
        result = _check_input_contracts(cmd, str(tmp_path), resolve_input_specs)
        assert result is None, f"Expected acceptance, got: {result!r}"

    def test_prepare_pr_missing_conflict_report_is_rejected(self, tmp_path) -> None:
        """When conflict-report is missing, the scalar spec at path position 1 fails."""
        from autoskillit.recipe._contracts_manifest import resolve_input_specs
        from autoskillit.server._guards import _check_input_contracts

        plan_a = tmp_path / "plan_a.md"
        plan_a.write_text("plan a")
        plan_b = tmp_path / "plan_b.md"
        plan_b.write_text("plan b")
        joined = f"{plan_a},{plan_b}"
        cmd = f"/autoskillit:prepare-pr {joined} my-run main 123 true /tmp/does-not-exist.md"
        result = _check_input_contracts(cmd, str(tmp_path), resolve_input_specs)
        assert result is not None
        parsed = json.loads(result)
        assert parsed["success"] is False
        assert "conflict_report_path" in parsed["result"]


class TestOpenIntegrationPrExactShape:
    """Bundled open-integration-pr shape: conflict_report_paths must bind path position 1."""

    def _make_recipe_shape_files(self, tmp_path: Path) -> tuple[Path, Path, Path, Path]:
        pr_order = tmp_path / "pr_order.json"
        pr_order.write_text("{}")
        conflict_a = tmp_path / "conflict_a.md"
        conflict_a.write_text("a")
        conflict_b = tmp_path / "conflict_b.md"
        conflict_b.write_text("b")
        domain_partitions = tmp_path / "domain_partitions.json"
        domain_partitions.write_text("{}")
        return pr_order, conflict_a, conflict_b, domain_partitions

    def test_open_integration_pr_recipe_shape_accepts_conflict_list(self, tmp_path: Path) -> None:
        """Comma-joined conflict reports + named domain partitions must validate."""
        from autoskillit.recipe._contracts_manifest import resolve_input_specs
        from autoskillit.server._guards import _check_input_contracts

        pr_order, conflict_a, conflict_b, domain_partitions = self._make_recipe_shape_files(
            tmp_path
        )
        cmd = (
            f"/autoskillit:open-integration-pr batch-branch main {pr_order} GO "
            f'"{conflict_a},{conflict_b}" domain_partitions_path={domain_partitions}'
        )
        result = _check_input_contracts(cmd, str(tmp_path), resolve_input_specs)
        assert result is None, f"Expected acceptance, got: {result!r}"

    def test_open_integration_pr_missing_conflict_member_rejected(self, tmp_path: Path) -> None:
        """Missing conflict-report member must be rejected as conflict_report_paths."""
        from autoskillit.recipe._contracts_manifest import resolve_input_specs
        from autoskillit.server._guards import _check_input_contracts

        pr_order, conflict_a, _conflict_b, domain_partitions = self._make_recipe_shape_files(
            tmp_path
        )
        missing = tmp_path / "missing_conflict.md"
        cmd = (
            f"/autoskillit:open-integration-pr batch-branch main {pr_order} GO "
            f'"{conflict_a},{missing}" domain_partitions_path={domain_partitions}'
        )
        result = _check_input_contracts(cmd, str(tmp_path), resolve_input_specs)
        assert result is not None
        parsed = json.loads(result)
        assert parsed["success"] is False
        assert "conflict_report_paths" in parsed["result"]
