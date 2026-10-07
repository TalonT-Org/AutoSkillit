from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from autoskillit.core import TokenMeasure

from ._measure_helpers import _metric_payload
from ._view_common import SKILL_LEVELS, _window_level_blocks


def _skill_blocks(
    sessions: Sequence[Mapping[str, Any]],
    children: Sequence[Mapping[str, Any]],
    *,
    generated_at_ms: int,
    definitions: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    skill_rows: list[Mapping[str, Any]] = [
        row
        for row in sessions
        if row.get("level") in SKILL_LEVELS and isinstance(row.get("skill"), str)
    ]
    child_blocks = {
        (window, tuple(levels)): rows
        for window, levels, rows in _window_level_blocks(children, generated_at_ms=generated_at_ms)
    }
    blocks = []
    for window, levels, selected in _window_level_blocks(
        skill_rows, generated_at_ms=generated_at_ms
    ):
        groups: dict[tuple[str, str, str], list[Mapping[str, Any]]] = {}
        for row in selected:
            identity = (
                row["skill"],
                row.get("harness") or "unknown",
                row.get("provider") or "unknown",
            )
            groups.setdefault(identity, []).append(row)
        rows_out = []
        selected_children = child_blocks.get((window, tuple(levels)), [])
        for skill, harness, provider in sorted(groups):
            runs = groups[(skill, harness, provider)]
            related = [
                row
                for row in selected_children
                if row.get("skill") == skill
                and row.get("harness") == harness
                and row.get("_parent_provider") == provider
            ]
            related_roles = sorted({row["role"] for row in related})
            metric = _metric_payload(
                runs,
                sample_unit="skill-run",
                definition_roles=related_roles,
                definitions=definitions,
            )
            step_groups: dict[tuple[str | None, str | None], list[Mapping[str, Any]]] = {}
            for run in runs:
                recipe = run.get("recipe") if isinstance(run.get("recipe"), str) else None
                step = run.get("step") if isinstance(run.get("step"), str) else None
                step_groups.setdefault((recipe, step), []).append(run)
            recipe_steps = []
            for recipe, step in sorted(
                step_groups,
                key=lambda value: (
                    value[0] is None,
                    value[0] or "",
                    value[1] is None,
                    value[1] or "",
                ),
            ):
                step_runs = step_groups[(recipe, step)]
                step_children = [
                    row
                    for row in related
                    if row.get("recipe") == recipe and row.get("step") == step
                ]
                step_metric = _metric_payload(
                    step_runs,
                    sample_unit="skill-run",
                    definition_roles=sorted({row["role"] for row in step_children}),
                    definitions=definitions,
                )
                recipe_steps.append(
                    {
                        "recipe": recipe,
                        "step": step,
                        **step_metric,
                        "exact_retransmission": TokenMeasure.unavailable().to_dict(),
                    }
                )
            role_groups: dict[tuple[str, str, str], list[Mapping[str, Any]]] = {}
            for child in related:
                key = (child["role"], child["provider"], child["harness"])
                role_groups.setdefault(key, []).append(child)
            child_roles = []
            for role, role_provider, role_harness in sorted(role_groups):
                role_runs = role_groups[(role, role_provider, role_harness)]
                role_metric = _metric_payload(
                    role_runs,
                    sample_unit="child-invocation",
                    definition_roles=[role],
                    definitions=definitions,
                )
                child_roles.append(
                    {
                        "role": role,
                        "provider": role_provider,
                        "harness": role_harness,
                        "models": sorted(
                            {
                                row["model"]
                                for row in role_runs
                                if isinstance(row.get("model"), str)
                            }
                        ),
                        **role_metric,
                    }
                )
            rows_out.append(
                {
                    "skill": skill,
                    "harness": harness,
                    "provider": provider,
                    **metric,
                    "exact_retransmission": TokenMeasure.unavailable().to_dict(),
                    "recipe_steps": recipe_steps,
                    "child_roles": child_roles,
                }
            )
        blocks.append({"window": window, "levels": levels, "rows": rows_out})
    return blocks


def _role_blocks(
    children: Sequence[Mapping[str, Any]],
    *,
    generated_at_ms: int,
    definitions: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    role_rows: list[Mapping[str, Any]] = [
        row for row in children if row.get("level") in SKILL_LEVELS
    ]
    blocks = []
    for window, levels, selected in _window_level_blocks(
        role_rows, generated_at_ms=generated_at_ms
    ):
        groups: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
        for row in selected:
            groups.setdefault((row["role"], row["provider"]), []).append(row)
        rows_out = []
        for role, provider in sorted(groups):
            harness_groups: dict[str, list[Mapping[str, Any]]] = {}
            for row in groups[(role, provider)]:
                harness_groups.setdefault(row["harness"], []).append(row)
            harnesses = []
            for harness in sorted(harness_groups):
                invocations = harness_groups[harness]
                metric = _metric_payload(
                    invocations,
                    sample_unit="child-invocation",
                    definition_roles=[role],
                    definitions=definitions,
                )
                harnesses.append(
                    {
                        "harness": harness,
                        "models": sorted(
                            {
                                row["model"]
                                for row in invocations
                                if isinstance(row.get("model"), str)
                            }
                        ),
                        **metric,
                    }
                )
            rows_out.append({"role": role, "provider": provider, "harnesses": harnesses})
        blocks.append({"window": window, "levels": levels, "rows": rows_out})
    return blocks
