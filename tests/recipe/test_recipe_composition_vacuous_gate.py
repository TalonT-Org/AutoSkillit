"""Focused composition coverage after capability-derived gates were removed.

Recipe admission finalizes the composed, post-prune graph without treating a reachable
child as a vacuous exemption.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.recipe import _api as recipe_api

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def _write_project_recipe(project_dir: Path, name: str, content: str) -> None:
    recipe_dir = project_dir / ".autoskillit" / "recipes"
    recipe_dir.mkdir(parents=True, exist_ok=True)
    (recipe_dir / f"{name}.yaml").write_text(content, encoding="utf-8")


def test_truthy_composition_gate_admits_a_non_vacuous_child(tmp_path: Path) -> None:
    """A composed child becomes the finalized entrypoint instead of an exemption."""
    _write_project_recipe(
        tmp_path,
        "composition-parent",
        """
name: composition-parent
description: Parent with an explicitly enabled child
kitchen_rules: [test]
ingredients:
  include_child:
    description: Enable the child recipe
    default: "false"
    hidden: true
steps:
  compose_child:
    sub_recipe: composition-child
    gate: include_child
    on_success: done
    on_failure: escalate
  done:
    action: stop
    message: Composition completed. Emit your L3 sentinel JSON block.
""".lstrip(),
    )
    sub_recipe_dir = tmp_path / ".autoskillit" / "recipes" / "sub-recipes"
    sub_recipe_dir.mkdir(parents=True)
    (sub_recipe_dir / "composition-child.yaml").write_text(
        """
name: composition-child
description: Child admitted into the parent flow
kitchen_rules: [test]
steps:
  admitted:
    action: confirm
    message: child active
    on_success: done
    on_failure: escalate
""".lstrip(),
        encoding="utf-8",
    )

    result = recipe_api.load_and_validate(
        "composition-parent",
        project_dir=tmp_path,
        ingredient_overrides={"include_child": "true"},
        include_finalized_projection=True,
    )

    assert result["valid"] is True, result.get("suggestions", [])
    assert result["post_prune_step_names"] == ["composition_child_admitted", "done"]
    projection = result["_finalized_projection"]
    assert projection.entrypoint == "composition_child_admitted"
    assert projection.ordered_step_names == ("composition_child_admitted", "done")
    assert ("composition_child_admitted", "success", "done") in {
        (edge.source, edge.edge_type, edge.target) for edge in projection.ordered_flow_edges
    }


def test_semantics_and_projection_observe_the_post_prune_graph(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The served semantic pass and finalized projection run after route repair."""
    _write_project_recipe(
        tmp_path,
        "post-prune-ordering",
        """
name: post-prune-ordering
description: A guarded middle step
kitchen_rules: [test]
ingredients:
  enabled:
    description: Keep the guarded step
    default: "false"
steps:
  entry:
    tool: run_cmd
    with:
      cmd: echo entry
    on_success: guarded
    on_failure: done
  guarded:
    action: confirm
    message: guarded
    skip_when_false: inputs.enabled
    on_skip: done
    on_success: done
    on_failure: done
  done:
    action: stop
    message: done
""".lstrip(),
    )
    semantic_step_orders: list[tuple[str, ...]] = []

    def capture_semantic_order(ctx: object) -> list[object]:
        semantic_step_orders.append(tuple(ctx.recipe.steps))  # type: ignore[attr-defined]
        return []

    monkeypatch.setattr(recipe_api, "run_semantic_rules", capture_semantic_order)

    result = recipe_api.load_and_validate(
        "post-prune-ordering",
        project_dir=tmp_path,
        ingredient_overrides={"enabled": "false"},
        include_finalized_projection=True,
    )

    assert semantic_step_orders == [
        ("entry", "guarded", "done"),
        ("entry", "done"),
    ]
    projection = result["_finalized_projection"]
    assert projection.ordered_step_names == ("entry", "done")
    assert ("entry", "success", "done") in {
        (edge.source, edge.edge_type, edge.target) for edge in projection.ordered_flow_edges
    }
