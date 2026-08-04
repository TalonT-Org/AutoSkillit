"""Tests for core/types/_type_results_execution.py — execution-scoped type module."""

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


class TestExecutionTypesImport:
    """Each execution-scoped type is importable from the new module."""

    def test_session_telemetry_importable(self):
        from autoskillit.core.types._type_results_execution import SessionTelemetry

        assert hasattr(SessionTelemetry, "empty")

    def test_recipe_identity_importable(self):
        from autoskillit.core.types._type_results_execution import RecipeIdentity

        assert hasattr(RecipeIdentity, "empty")

    def test_ci_run_scope_importable(self):
        from autoskillit.core.types._type_results_execution import CIRunScope

        assert CIRunScope().workflow is None

    def test_backward_compat_via_core_gateway(self):
        """All moved types are still importable from autoskillit.core."""
        import inspect

        from autoskillit.core import (
            CIRunScope,
            RecipeIdentity,
            SessionTelemetry,
        )

        assert all(inspect.isclass(cls) for cls in [SessionTelemetry, RecipeIdentity, CIRunScope])


class TestExecutionTypesNotInResults:
    """Moved types must no longer appear in _type_results.__all__."""

    def test_moved_types_absent_from_results_all(self):
        from autoskillit.core.types._type_results import __all__ as results_all

        moved = {"SessionTelemetry", "RecipeIdentity", "CIRunScope"}
        overlap = moved & set(results_all)
        assert not overlap, f"Types still in _type_results.__all__: {overlap}"

    def test_provider_outcome_in_results_all(self):
        from autoskillit.core.types._type_results import __all__ as results_all

        assert "ProviderOutcome" in results_all

    def test_present_in_execution_all(self):
        from autoskillit.core.types._type_results_execution import (
            __all__ as exec_all,
        )

        expected = {"SessionTelemetry", "RecipeIdentity", "CIRunScope"}
        assert expected == set(exec_all)

    def test_skill_result_still_uses_provider_outcome(self):
        """SkillResult.provider field default_factory references ProviderOutcome."""
        from autoskillit.core import ProviderOutcome, SkillResult

        sr = SkillResult.crashed(Exception("test"))
        assert isinstance(sr.provider, ProviderOutcome)

    def test_execution_identity_is_cycle_free_and_gateway_exported(self):
        from autoskillit.core import (
            BackendPinResolution,
            ChildExecutionIdentity,
            ExecutionIdentity,
        )
        from autoskillit.core.types._type_execution_identity import __all__ as identity_all

        resolution = BackendPinResolution("codex", "recipe_step", "agent_backend.x")
        assert resolution.backend == "codex"
        assert set(identity_all) == {
            "BackendPinResolution",
            "ChildExecutionIdentity",
            "ExecutionIdentity",
        }
        children = (
            ChildExecutionIdentity("b", "role", "plan", "definition"),
            ChildExecutionIdentity("a", "role", "plan", "definition"),
        )
        assert [
            child["task_id"]
            for child in ExecutionIdentity(children=children).to_dict()["children"]
        ] == ["a", "b"]
