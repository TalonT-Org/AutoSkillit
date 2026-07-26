"""Focused source-docstring contract for the run_skill selection boundary."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


def _run_skill_docstring() -> str:
    """Import run_skill and return its source docstring."""
    from autoskillit.server.tools import tools_execution

    return tools_execution.run_skill.__doc__ or ""


class TestRunSkillDocstring:
    """run_skill must document concise, backend-neutral selection semantics."""

    def test_docstring_preserves_compact_result_contract(self) -> None:
        docstring = _run_skill_docstring()
        for field in (
            "success",
            "result",
            "session_id",
            "subtype",
            "is_error",
            "exit_code",
            "needs_retry",
            "retry_reason",
        ):
            assert field in docstring

    def test_docstring_routes_retry_through_recipe(self) -> None:
        docstring = _run_skill_docstring()
        assert "needs_retry" in docstring
        assert "recipe's declared retry route" in docstring

    def test_docstring_has_positive_and_negative_selection_boundaries(self) -> None:
        docstring = _run_skill_docstring().casefold()
        assert "already-selected recipe step" in docstring
        assert "headless recipe orchestrator operating at l2" in docstring
        assert "interactive autoskillit cook/order session" in docstring
        assert "separate l1 headless coding-agent worker" in docstring
        assert "available local skill" in docstring
        assert "load and follow its skill.md" in docstring
        assert "current interactive session" in docstring
        assert "do not call run_skill merely because the skill was named" in docstring

    def test_complete_docstring_is_backend_neutral(self) -> None:
        docstring = _run_skill_docstring().casefold()
        for provider_term in ("claude", "sonnet", "opus"):
            assert provider_term not in docstring
