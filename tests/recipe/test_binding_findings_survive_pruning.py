"""Declaration binding errors survive ingredient-based step pruning."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from autoskillit.recipe._api import load_and_validate
from autoskillit.recipe.api._api_listing import validate_from_path

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _install_recipe(tmp_path: Path, name: str, content: str) -> None:
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True, exist_ok=True)
    (recipes_dir / f"{name}.yaml").write_text(textwrap.dedent(content))


@pytest.mark.parametrize(
    ("producer_tool", "producer_args", "rule", "step"),
    [
        (
            "run_python",
            "{callable: autoskillit.smoke_utils.check_loop_iteration, step_name: maybe}",
            "missing-ingredient",
            "bem",
        ),
        ("run_cmd", "{cmd: echo producer}", "invalid-tool-invocation-binding", "maybe"),
    ],
    ids=["missing-skill-input", "pruned-tool-binding"],
)
def test_binding_findings_survive_pruning(
    tmp_path: Path, producer_tool: str, producer_args: str, rule: str, step: str
) -> None:
    _install_recipe(
        tmp_path,
        "probe-prune",
        """
        name: probe-prune
        description: Probe whether pruning hides a declaration binding error.
        kitchen_rules:
          - Follow the declared routes.
        ingredients:
          flag:
            description: Whether to run the producer step.
            default: "false"
        steps:
          maybe:
            tool: _PRODUCER_TOOL_
            with: _PRODUCER_ARGS_
            skip_when_false: inputs.flag
            capture:
              issue_numbers: "${{ result.next_iteration }}"
            on_success: bem
            on_failure: done
            on_skip: bem
          bem:
            tool: run_skill
            with:
              skill_command: "/autoskillit:build-execution-map"
              skill_inputs:
                base_ref: "main"
              cwd: "/tmp"
              step_name: bem
              output_dir: "."
            capture:
              execution_map: "${{ result.execution_map }}"
            on_success: emit
            on_failure: done
          emit:
            tool: run_cmd
            with:
              cmd: "echo 'execution_map=${{ context.execution_map }}'"
              cwd: "/tmp"
              step_name: emit
            on_success: done
            on_failure: done
          done:
            action: stop
            message: Emit the L3 sentinel block with success=true and reason=Completed.
        """.replace("_PRODUCER_TOOL_", producer_tool).replace("_PRODUCER_ARGS_", producer_args),
    )

    result = load_and_validate(
        "probe-prune", project_dir=tmp_path, ingredient_overrides={"flag": "false"}
    )

    assert result["valid"] is False
    path_result = validate_from_path(
        tmp_path / ".autoskillit/recipes/probe-prune.yaml",
        ingredient_overrides={"flag": "false"},
    )
    assert path_result["valid"] is False
    for findings in (result["suggestions"], path_result["findings"]):
        assert any(
            finding.get("rule") == rule
            and finding.get("step") == step
            and finding.get("severity") == "error"
            for finding in findings
        )
