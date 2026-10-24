"""Declaration binding errors survive ingredient-based step pruning."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from autoskillit.recipe._api import load_and_validate

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _install_recipe(tmp_path: Path, name: str, content: str) -> None:
    recipes_dir = tmp_path / ".autoskillit" / "recipes"
    recipes_dir.mkdir(parents=True, exist_ok=True)
    (recipes_dir / f"{name}.yaml").write_text(textwrap.dedent(content))


def test_missing_ingredient_survives_pruned_producer(tmp_path: Path) -> None:
    _install_recipe(
        tmp_path,
        "probe-prune",
        """
        name: probe-prune
        description: Probe whether pruning hides a missing skill input finding.
        kitchen_rules:
          - Follow the declared routes.
        ingredients:
          flag:
            description: Whether to run the producer step.
            default: "false"
        steps:
          maybe:
            tool: run_python
            with:
              callable: autoskillit.smoke_utils.check_loop_iteration
              step_name: maybe
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
        """,
    )

    result = load_and_validate(
        "probe-prune", project_dir=tmp_path, ingredient_overrides={"flag": "false"}
    )

    assert result["valid"] is False
    assert any(
        suggestion.get("rule") == "missing-ingredient"
        and suggestion.get("step") == "bem"
        and suggestion.get("severity") == "error"
        for suggestion in result["suggestions"]
    )


def test_pruned_step_binding_failure_is_reported(tmp_path: Path) -> None:
    _install_recipe(
        tmp_path,
        "pruned-binding-failure",
        """
        name: pruned-binding-failure
        description: Probe whether pruning hides a tool binding failure.
        kitchen_rules:
          - Follow the declared routes.
        ingredients:
          flag:
            description: Whether to run the producer step.
            default: "false"
        steps:
          maybe:
            tool: run_cmd
            with:
              cmd: "echo producer"
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
        """,
    )

    result = load_and_validate(
        "pruned-binding-failure",
        project_dir=tmp_path,
        ingredient_overrides={"flag": "false"},
    )

    assert result["valid"] is False
    assert any(
        suggestion.get("rule") == "invalid-tool-invocation-binding"
        and suggestion.get("step") == "maybe"
        and suggestion.get("severity") == "error"
        for suggestion in result["suggestions"]
    )
