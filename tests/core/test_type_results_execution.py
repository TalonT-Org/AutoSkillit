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
        from typing import get_type_hints

        from autoskillit.core import (
            BackendAuthorityKind,
            BackendPinResolution,
            ChildExecutionIdentity,
            ChildExecutionIdentityDict,
            ExecutionIdentity,
            ExecutionIdentityDict,
        )
        from autoskillit.core.types._type_execution_identity import __all__ as identity_all

        resolution = BackendPinResolution("codex", "recipe_step", "agent_backend.x")
        assert resolution.backend == "codex"
        assert resolution.kind is None
        assert get_type_hints(BackendPinResolution)["kind"] == BackendAuthorityKind | None
        assert BackendAuthorityKind.__module__.endswith("._type_execution_identity")
        assert set(identity_all) == {
            "BackendAuthorityKind",
            "BackendPinResolution",
            "ChildExecutionIdentity",
            "ChildExecutionIdentityDict",
            "ExecutionIdentity",
            "ExecutionIdentityDict",
        }
        children = (
            ChildExecutionIdentity("b", "role", "plan", "definition"),
            ChildExecutionIdentity("a", "role", "plan", "definition"),
        )
        assert [
            child["task_id"]
            for child in ExecutionIdentity(children=children).to_dict()["children"]
        ] == ["a", "b"]
        assert (
            get_type_hints(ChildExecutionIdentity.to_dict)["return"] is ChildExecutionIdentityDict
        )
        assert get_type_hints(ExecutionIdentity.to_dict)["return"] is ExecutionIdentityDict
        assert (
            get_type_hints(ExecutionIdentityDict)["children"] == list[ChildExecutionIdentityDict]
        )

    def test_execution_identity_typed_dicts_match_persisted_keys(self):
        from autoskillit.core import (
            ChildExecutionIdentity,
            ChildExecutionIdentityDict,
            ExecutionIdentity,
            ExecutionIdentityDict,
        )

        child = ChildExecutionIdentity(
            "task",
            "role",
            "plan",
            "definition",
            requested_backend="codex",
            effective_backend="codex",
            requested_model="gpt-5.6-luna",
            effective_model="gpt-5.6-luna",
            requested_effort="max",
            effective_effort="max",
            session_id="child-session",
        )
        identity = ExecutionIdentity(
            requested_parent_backend="codex",
            effective_parent_backend="codex",
            requested_parent_model="gpt-5.6-luna",
            effective_parent_model="gpt-5.6-luna",
            requested_parent_effort="max",
            effective_parent_effort="max",
            cli_version="1.2.3",
            override_tier="recipe",
            override_key_path="agent_backend.codex",
            parent_session_id="parent-session",
            children=(child,),
        )

        assert set(child.to_dict()) == set(ChildExecutionIdentityDict.__annotations__)
        assert set(identity.to_dict()) == set(ExecutionIdentityDict.__annotations__)
        assert child.to_dict() == {
            "task_id": "task",
            "role": "role",
            "plan_digest": "plan",
            "definition_digest": "definition",
            "requested_backend": "codex",
            "effective_backend": "codex",
            "requested_model": "gpt-5.6-luna",
            "effective_model": "gpt-5.6-luna",
            "requested_effort": "max",
            "effective_effort": "max",
            "session_id": "child-session",
        }
        assert identity.to_dict() == {
            "requested_parent_backend": "codex",
            "effective_parent_backend": "codex",
            "requested_parent_model": "gpt-5.6-luna",
            "effective_parent_model": "gpt-5.6-luna",
            "requested_parent_effort": "max",
            "effective_parent_effort": "max",
            "cli_version": "1.2.3",
            "override_tier": "recipe",
            "override_key_path": "agent_backend.codex",
            "parent_session_id": "parent-session",
            "children": [child.to_dict()],
        }

    @pytest.mark.parametrize(
        "empty_field", ["task_id", "role", "plan_digest", "definition_digest"]
    )
    def test_child_execution_identity_rejects_empty_authority_fields(self, empty_field: str):
        from autoskillit.core import ChildExecutionIdentity

        values = {
            "task_id": "task",
            "role": "role",
            "plan_digest": "plan",
            "definition_digest": "definition",
        }
        values[empty_field] = ""

        with pytest.raises(ValueError):
            ChildExecutionIdentity(**values)

    def test_execution_identity_rejects_duplicate_tasks_and_sorts_children(self):
        from autoskillit.core import ChildExecutionIdentity, ExecutionIdentity

        child_b = ChildExecutionIdentity("b", "role", "plan-b", "definition-b")
        child_a = ChildExecutionIdentity("a", "role", "plan-a", "definition-a")

        assert ExecutionIdentity(children=(child_b, child_a)).children == (child_a, child_b)
        with pytest.raises(ValueError, match="must be unique"):
            ExecutionIdentity(children=(child_a, child_a))
