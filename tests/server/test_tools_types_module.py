"""Verify server-layer TypedDicts are importable from server/tools/_types."""

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


class TestServerToolTypesImport:
    """REQ-RELOC-001: Types exist in server/tools/_types.py."""

    def test_kitchen_status_result_importable(self):
        from autoskillit.server.tools._types import KitchenStatusResult

        assert "package_version" in KitchenStatusResult.__optional_keys__

    def test_token_summary_result_importable(self):
        from autoskillit.server.tools._types import TokenSummaryResult

        assert "model_totals" in TokenSummaryResult.__optional_keys__

    def test_timing_summary_result_importable(self):
        from autoskillit.server.tools._types import TimingSummaryResult

        assert "steps" in TimingSummaryResult.__optional_keys__

    def test_run_skill_result_importable(self):
        from autoskillit.server.tools._types import RunSkillResult

        assert "success" in RunSkillResult.__required_keys__

    def test_run_cmd_result_importable(self):
        from autoskillit.server.tools._types import RunCmdResult

        assert "success" in RunCmdResult.__required_keys__

    def test_test_check_result_importable(self):
        from autoskillit.server.tools._types import TestCheckResult

        assert "passed" in TestCheckResult.__required_keys__

    def test_merge_worktree_result_importable(self):
        from autoskillit.server.tools._types import MergeWorktreeResult

        assert "merge_succeeded" in MergeWorktreeResult.__optional_keys__


class TestServerToolTypesNotInCore:
    """REQ-RELOC-002/003: Types removed from core re-export surface."""

    def test_types_not_in_core_types_all(self):
        from autoskillit.core.types._type_results import __all__ as results_all

        relocated = {
            "KitchenStatusResult",
            "TokenSummaryResult",
            "TimingSummaryResult",
            "RunSkillResult",
            "RunCmdResult",
            "TestCheckResult",
            "MergeWorktreeResult",
        }
        assert relocated.isdisjoint(set(results_all))

    def test_model_total_entry_still_in_core(self):
        from autoskillit.core.types._type_results import ModelTotalEntry

        assert "model" in ModelTotalEntry.__required_keys__
