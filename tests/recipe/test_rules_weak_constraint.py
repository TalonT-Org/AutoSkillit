from __future__ import annotations

import pytest

from autoskillit.recipe.io import _parse_step
from autoskillit.recipe.schema import Recipe
from autoskillit.recipe.validator import run_semantic_rules

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


class TestWeakConstraintRule:
    def _make_recipe_with_kitchen_rules(self, kitchen_rules: list[str]) -> Recipe:
        steps = {
            "run": _parse_step({"tool": "test_check", "on_success": "done"}),
            "done": _parse_step({"action": "stop", "message": "Done"}),
        }
        return Recipe(name="test", description="test", steps=steps, kitchen_rules=kitchen_rules)

    def test_weak_constraint_text_detected(self) -> None:
        wf = self._make_recipe_with_kitchen_rules(["Only use AutoSkillit MCP tools."])
        findings = run_semantic_rules(wf)
        weak = [f for f in findings if f.rule == "weak-constraint-text"]
        assert weak

    def test_detailed_constraints_pass(self) -> None:
        from autoskillit.core.types import PIPELINE_FORBIDDEN_TOOLS

        tool_list = ", ".join(PIPELINE_FORBIDDEN_TOOLS)
        constraint = f"NEVER use native tools ({tool_list}) from the orchestrator."
        wf = self._make_recipe_with_kitchen_rules([constraint])
        findings = run_semantic_rules(wf)
        weak = [f for f in findings if f.rule == "weak-constraint-text"]
        assert not weak
