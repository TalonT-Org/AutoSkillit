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


class TestToolFailureEnvelope:
    """Verify ToolFailureEnvelope TypedDict and factory helpers."""

    def test_tool_failure_envelope_importable(self):
        from autoskillit.server.tools._types import ToolFailureEnvelope

        assert "success" in ToolFailureEnvelope.__required_keys__

    def test_tool_failure_envelope_required_keys(self):
        from autoskillit.server.tools._types import ToolFailureEnvelope

        expected = {"success", "error", "stage", "retriable"}
        assert expected <= ToolFailureEnvelope.__required_keys__

    def test_tool_failure_envelope_optional_keys(self):
        from autoskillit.server.tools._types import ToolFailureEnvelope

        assert "user_visible_message" in ToolFailureEnvelope.__optional_keys__

    def test_tool_failure_envelope_success_type(self):
        import typing

        from autoskillit.server.tools._types import ToolFailureEnvelope

        hints = typing.get_type_hints(ToolFailureEnvelope)
        assert typing.get_args(hints["success"]) == (False,)

    def test_server_failure_envelope_factory(self):
        from autoskillit.server.tools._types import server_failure_envelope

        result = server_failure_envelope(ValueError("boom"), "init")
        assert result["success"] is False
        assert result["retriable"] is True
        assert result["stage"] == "init"
        assert "ValueError" in result["error"]
        assert "boom" in result["error"]

    def test_input_failure_envelope_factory(self):
        from autoskillit.server.tools._types import input_failure_envelope

        result = input_failure_envelope("bad input", "validate")
        assert result["success"] is False
        assert result["retriable"] is False
        assert result["stage"] == "validate"
        assert result["error"] == "bad input"

    def test_server_failure_envelope_user_visible_message(self):
        from autoskillit.server.tools._types import server_failure_envelope

        result = server_failure_envelope(RuntimeError("oops"), "startup")
        assert "user_visible_message" in result
        assert len(result["user_visible_message"]) > 0

    def test_tool_failure_envelope_in_all(self):
        from autoskillit.server.tools._types import __all__ as types_all

        assert "ToolFailureEnvelope" in types_all
        assert "server_failure_envelope" in types_all
        assert "input_failure_envelope" in types_all
        assert "_validate_result" in types_all

    def test_tool_failure_envelope_distinct_from_kitchen_envelope(self):
        from autoskillit.server.tools._types import ToolFailureEnvelope
        from autoskillit.server.tools.tools_kitchen import (
            _kitchen_failure_envelope,
        )

        assert "retriable" in ToolFailureEnvelope.__required_keys__
        all_keys = ToolFailureEnvelope.__required_keys__ | ToolFailureEnvelope.__optional_keys__
        assert "kitchen" not in all_keys
        result = _kitchen_failure_envelope(ValueError("x"), "test")
        assert isinstance(result, str)
