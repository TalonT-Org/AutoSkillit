"""Tests for `_check_input_contracts` validation rules and the resolver wiring
path through `run_skill`.
"""

from __future__ import annotations

import json

import pytest

from tests.server._input_contract_test_helpers import _make_input_contract_resolver

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestInputContractValidation:
    """_check_input_contracts validates file_path and directory_path inputs."""

    def test_file_path_input_rejects_nonexistent_path(self, tmp_path):
        from autoskillit.server._guards import _check_input_contracts

        resolver = _make_input_contract_resolver()
        result = _check_input_contracts(
            "/resolve-failures /nonexistent/worktree /nonexistent/plan.md main",
            str(tmp_path),
            resolver,
        )
        assert result is not None
        parsed = json.loads(result)
        assert parsed["success"] is False
        assert parsed["subtype"] == "gate_error"

    def test_file_path_input_rejects_directory_as_file(self, tmp_path):
        from autoskillit.server._guards import _check_input_contracts

        worktree = tmp_path / "worktree"
        worktree.mkdir()
        plan_as_dir = tmp_path / "plan.md"
        plan_as_dir.mkdir()
        resolver = _make_input_contract_resolver()
        result = _check_input_contracts(
            f"/resolve-failures {worktree} {plan_as_dir} main",
            str(tmp_path),
            resolver,
        )
        assert result is not None
        parsed = json.loads(result)
        assert parsed["success"] is False

    def test_file_path_input_accepts_file_without_extension(self, tmp_path):
        from autoskillit.server._guards import _check_input_contracts

        worktree = tmp_path / "worktree"
        worktree.mkdir()
        plan_no_ext = tmp_path / "plan_no_ext"
        plan_no_ext.write_text("content")
        resolver = _make_input_contract_resolver()
        result = _check_input_contracts(
            f"/resolve-failures {worktree} {plan_no_ext} main",
            str(tmp_path),
            resolver,
        )
        # plan_no_ext exists as a file so is_file() passes — this tests the file exists check
        assert result is None

    def test_directory_path_input_rejects_file_as_directory(self, tmp_path):
        from autoskillit.server._guards import _check_input_contracts

        file_as_worktree = tmp_path / "worktree"
        file_as_worktree.write_text("I am a file")
        resolver = _make_input_contract_resolver()
        result = _check_input_contracts(
            f"/resolve-failures {file_as_worktree} /some/plan.md main",
            str(tmp_path),
            resolver,
        )
        assert result is not None
        parsed = json.loads(result)
        assert parsed["success"] is False

    def test_directory_path_input_accepts_valid_directory(self, tmp_path):
        from autoskillit.server._guards import _check_input_contracts

        worktree = tmp_path / "worktree"
        worktree.mkdir()
        plan = tmp_path / "plan.md"
        plan.write_text("content")
        resolver = _make_input_contract_resolver()
        result = _check_input_contracts(
            f"/resolve-failures {worktree} {plan} main",
            str(tmp_path),
            resolver,
        )
        assert result is None

    def test_file_path_input_accepts_valid_file(self, tmp_path):
        from autoskillit.server._guards import _check_input_contracts

        worktree = tmp_path / "worktree"
        worktree.mkdir()
        plan = tmp_path / "my-plan.md"
        plan.write_text("content")
        resolver = _make_input_contract_resolver()
        result = _check_input_contracts(
            f"/resolve-failures {worktree} {plan} main",
            str(tmp_path),
            resolver,
        )
        assert result is None

    def test_skill_without_contracts_passes(self, tmp_path):
        from autoskillit.server._guards import _check_input_contracts

        resolver = _make_input_contract_resolver()
        result = _check_input_contracts(
            "/nonexistent-skill-not-in-contracts /some/path.md",
            str(tmp_path),
            resolver,
        )
        assert result is None

    def test_mangled_plan_path_with_timestamp_suffix(self, tmp_path):
        from autoskillit.server._guards import _check_input_contracts

        real_plan = tmp_path / "rectify_foo_2026-05-28_194500.md"
        real_plan.write_text("Dry-walkthrough verified = TRUE")
        worktree = tmp_path / "worktree"
        worktree.mkdir()
        mangled = tmp_path / "rectify_foo_2026-05-28_194500-20260528-201752"
        resolver = _make_input_contract_resolver()
        result = _check_input_contracts(
            f"/resolve-failures {worktree} {mangled} main",
            str(tmp_path),
            resolver,
        )
        assert result is not None
        parsed = json.loads(result)
        assert parsed["success"] is False

    def test_resolver_returns_none_skips_validation(self, tmp_path):
        from autoskillit.server._guards import _check_input_contracts

        result = _check_input_contracts(
            "/resolve-failures /nonexistent/worktree /nonexistent/plan.md main",
            str(tmp_path),
            None,
        )
        assert result is None


class TestInputContractResolver:
    """InputContractResolver loads input specs from skill_contracts.yaml."""

    def test_input_contract_resolver_returns_specs_for_resolve_failures(self):
        from autoskillit.core import InputSpec

        resolver = _make_input_contract_resolver()
        specs = resolver("/resolve-failures /worktrees/foo /plans/bar.md main")
        assert len(specs) == 3
        assert specs[0] == InputSpec(
            name="worktree_path", type="directory_path", required=True, position=0
        )
        assert specs[1] == InputSpec(name="plan_path", type="file_path", required=True, position=1)
        assert specs[2] == InputSpec(
            name="diagnosis_path", type="file_path", required=False, position=2
        )

    def test_input_contract_resolver_returns_empty_for_unknown_skill(self):
        resolver = _make_input_contract_resolver()
        specs = resolver("/unknown-skill-not-in-contracts /some/path")
        assert list(specs) == []


class TestInputContractIntegration:
    """_check_input_contracts fires through run_skill when resolver is wired."""

    @pytest.mark.anyio
    async def test_run_skill_rejects_nonexistent_path_via_input_contract(
        self, tool_ctx_kitchen_open
    ):
        from autoskillit.server.tools.tools_execution import run_skill

        tool_ctx_kitchen_open.input_contract_resolver = _make_input_contract_resolver()
        result = json.loads(
            await run_skill(
                "/resolve-failures /nonexistent/worktree /nonexistent/plan.md main",
                "/tmp",
            )
        )
        assert result["success"] is False
        assert result["subtype"] == "gate_error"
        assert "Missing required" in result["result"] or "does not exist" in result["result"]
        assert tool_ctx_kitchen_open.runner.call_args_list == []
