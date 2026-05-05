"""Dataflow warning detectors: dead outputs, ref invalidations, implicit handoffs."""

from __future__ import annotations

from autoskillit.core import SKILL_TOOLS
from autoskillit.recipe._analysis_bfs import (
    _INVALIDATING_TOOLS,
    _bfs_capped,
    _build_capture_origin_map,
    bfs_reachable,
)
from autoskillit.recipe.contracts import _CONTEXT_REF_RE
from autoskillit.recipe.schema import DataFlowWarning, Recipe, RecipeStep

# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------


def _detect_ref_invalidations(recipe: Recipe, graph: dict[str, set[str]]) -> list[DataFlowWarning]:
    """Detect context variables consumed after the step that invalidated the
    underlying resource.

    The resource lifecycle contract:
    - ``merge_worktree`` on SUCCESS destroys the worktree directory and branch ref.
    - ``remove_clone`` on SUCCESS destroys the clone directory.

    Only steps reachable via ``on_success`` from the invalidating step are checked.
    Steps that re-capture the same variable are treated as barriers — they refresh
    the variable to a new resource, so their successors are excluded from
    the stale-ref check.
    """
    origin = _build_capture_origin_map(recipe)

    # Map: result_key → set of context variable names sourced from that key
    key_to_vars: dict[str, set[str]] = {}
    for var, result_key in origin.items():
        key_to_vars.setdefault(result_key, set()).add(var)

    # Map: var_name → set of step names that re-capture (refresh) it
    var_recapture_steps: dict[str, set[str]] = {}
    for step_name, step in recipe.steps.items():
        for cap_var in step.capture or {}:
            var_recapture_steps.setdefault(cap_var, set()).add(step_name)

    warnings: list[DataFlowWarning] = []

    for step_name, step in recipe.steps.items():
        invalidated_result_keys = _INVALIDATING_TOOLS.get(step.tool or "", frozenset())
        if not invalidated_result_keys:
            continue

        # Variables whose underlying resource is destroyed when this step SUCCEEDS
        invalidated_vars: set[str] = set()
        for result_key in invalidated_result_keys:
            invalidated_vars.update(key_to_vars.get(result_key, set()))

        if not invalidated_vars:
            continue

        # Only check steps reachable via on_success (failure path = resource not destroyed)
        on_success_target = step.on_success
        if not on_success_target or on_success_target not in recipe.steps:
            continue

        for var in invalidated_vars:
            # Steps that re-capture this var are barriers: they refresh the variable
            # to a new resource, so their successors are NOT stale consumers.
            barrier = var_recapture_steps.get(var, set())
            stale_reachable = _bfs_capped(graph, {on_success_target}, barrier)
            # A loop may route the invalidating step back into the reachable set
            stale_reachable.discard(step_name)

            for downstream_name in stale_reachable:
                downstream = recipe.steps.get(downstream_name)
                if downstream is None:
                    continue

                for arg_val in (downstream.with_args or {}).values():
                    if not isinstance(arg_val, str):
                        continue
                    for ref_var in _CONTEXT_REF_RE.findall(arg_val):
                        if ref_var == var:
                            warnings.append(
                                DataFlowWarning(
                                    code="REF_INVALIDATED",
                                    step_name=downstream_name,
                                    field=var,
                                    message=(
                                        f"Step '{downstream_name}' references "
                                        f"context.{var} after step '{step_name}' "
                                        f"({step.tool}) has invalidated the underlying "
                                        f"resource. Replace with a stable alternative "
                                        f"(e.g., a commit SHA captured before any merge "
                                        f"begins)."
                                    ),
                                )
                            )

    return warnings


# Observability captures: variables captured for human-readable logs, hook
# consumption, or note-driven orchestration rather than downstream recipe
# threading.  Each entry is (cap_key, skill_command_fragment).  A capture is
# exempt when cap_key matches AND skill_command_fragment appears in the
# step's skill_command (or tool/step_name for non-skill steps).
_OBSERVABILITY_CAPTURES: frozenset[tuple[str, str]] = frozenset(
    {
        ("diagnosis_path", "diagnose-ci"),
        ("summary_path", "pipeline-summary"),
        ("report_path", "generate-report"),
        ("selected_lenses", "prepare-research-pr"),
        ("selected_lenses", "prepare-pr"),
        ("lens_context_paths", "prepare-research-pr"),
        ("lens_context_paths", "prepare-pr"),
        ("pr_url", "compose-pr"),
        ("html_path", "bundle-local-report"),
        ("resource_report", "stage-data"),
        ("alignment_findings_path", "planner-validate-task-alignment"),
        ("review_approach_assessment_path", "planner-assess-review-approach"),
        # plan-visualization terminal handoff captures: emitted in food-truck sentinel,
        # not consumed by downstream recipe steps (route → stop action).
        ("visualization_plan_path", "plan-visualization"),
        ("report_plan_path", "plan-visualization"),
    }
)


def _is_observability_capture(cap_key: str, step_name: str, step: RecipeStep) -> bool:
    """Return True if *cap_key* is a known observability-only capture."""
    skill_cmd = step.with_args.get("skill_command", "") if step.with_args else ""

    # Skill-command-based exemptions (the common case).
    for obs_key, fragment in _OBSERVABILITY_CAPTURES:
        if cap_key == obs_key and fragment in skill_cmd:
            return True

    # merge_worktree cleanup_succeeded: matched by tool name + capture value,
    # not skill_command (merge_worktree is a direct tool, not a skill).
    if step.tool == "merge_worktree" and "result.cleanup_succeeded" in str(
        step.capture.get(cap_key, "")
    ):
        return True

    # export_local_bundle local_bundle_path: matched by step name (terminal
    # step in the local-mode path that ends at a stop action).
    if cap_key == "local_bundle_path" and step_name == "export_local_bundle":
        return True

    return False


def _detect_dead_outputs(recipe: Recipe, graph: dict[str, set[str]]) -> list[DataFlowWarning]:
    """Detect captured variables that are never consumed downstream."""
    warnings: list[DataFlowWarning] = []

    for step_name, step in recipe.steps.items():
        if not step.capture:
            continue

        # BFS: collect all steps reachable from this step
        reachable = bfs_reachable(graph, step_name)

        # Collect all context.X references in reachable steps' with_args and
        # on_result condition when-expressions (route actions gate on context vars).
        consumed: set[str] = set()
        for reachable_name in reachable:
            reachable_step = recipe.steps[reachable_name]
            for arg_val in reachable_step.with_args.values():
                if not isinstance(arg_val, str):
                    continue
                consumed.update(_CONTEXT_REF_RE.findall(arg_val))
            if reachable_step.on_result and reachable_step.on_result.conditions:
                for cond in reachable_step.on_result.conditions:
                    if cond.when and isinstance(cond.when, str):
                        consumed.update(_CONTEXT_REF_RE.findall(cond.when))

        # on_result routing — both legacy field and predicate conditions count
        # as structural consumption of captured variables.
        if step.on_result:
            # Legacy field routing: field name matches a captured key
            if step.on_result.field in step.capture:
                consumed.add(step.on_result.field)
            # Predicate condition routing — conditions gate on step result;
            # treat all captured vars as structurally consumed.
            if step.on_result.conditions:
                consumed.update(step.capture.keys())

        # Flag captured vars not consumed on any path
        for cap_key in step.capture:
            if cap_key not in consumed:
                if _is_observability_capture(cap_key, step_name, step):
                    continue
                warnings.append(
                    DataFlowWarning(
                        code="DEAD_OUTPUT",
                        step_name=step_name,
                        field=cap_key,
                        message=(
                            f"Step '{step_name}' captures '{cap_key}' but no "
                            f"reachable downstream step references "
                            f"${{{{ context.{cap_key} }}}}."
                        ),
                    )
                )

    return warnings


def _detect_implicit_handoffs(recipe: Recipe) -> list[DataFlowWarning]:
    """Detect skill-invoking steps with no capture block."""
    warnings: list[DataFlowWarning] = []

    for step_name, step in recipe.steps.items():
        if step.tool in SKILL_TOOLS and not step.capture:
            warnings.append(
                DataFlowWarning(
                    code="IMPLICIT_HANDOFF",
                    step_name=step_name,
                    field=step.tool,
                    message=(
                        f"Step '{step_name}' calls '{step.tool}' but has no "
                        f"capture: block. Data flows to subsequent steps "
                        f"implicitly through agent context rather than "
                        f"explicit ${{{{ context.X }}}} wiring."
                    ),
                )
            )

    return warnings
