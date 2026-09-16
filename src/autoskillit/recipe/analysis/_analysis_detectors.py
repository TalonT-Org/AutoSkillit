"""Dataflow warning detectors: dead outputs, ref invalidations, implicit handoffs."""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from autoskillit.core import SKILL_TOOLS
from autoskillit.recipe.analysis._analysis_bfs import (
    _INVALIDATING_TOOLS,
    _bfs_capped,
    _build_capture_origin_map,
    bfs_reachable,
)
from autoskillit.recipe.contracts.contracts import _CONTEXT_REF_RE
from autoskillit.recipe.schema import DataFlowWarning, Recipe, RecipeStep

# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------


def _context_refs_in_value(value: object) -> set[str]:
    if isinstance(value, str):
        return set(_CONTEXT_REF_RE.findall(value))
    if isinstance(value, dict):
        refs: set[str] = set()
        for key, nested in value.items():
            refs.update(_context_refs_in_value(key))
            refs.update(_context_refs_in_value(nested))
        return refs
    if isinstance(value, (list, tuple)):
        refs = set()
        for nested in value:
            refs.update(_context_refs_in_value(nested))
        return refs
    return set()


def _capture_step_index(recipe: Recipe) -> dict[str, set[str]]:
    """Index every step that captures each context variable."""
    capture_steps: dict[str, set[str]] = {}
    for step_name, step in recipe.steps.items():
        for variable in step.capture or {}:
            capture_steps.setdefault(variable, set()).add(step_name)
        for variable in step.capture_list or {}:
            capture_steps.setdefault(variable, set()).add(step_name)
    return capture_steps


def _iter_stale_context_refs(
    recipe: Recipe,
    graph: dict[str, set[str]],
    invalidator_name: str,
    variables: Iterable[str],
    capture_steps: dict[str, set[str]],
) -> Iterator[tuple[str, str]]:
    """Yield each stale top-level context reference after one invalidator succeeds."""
    invalidator = recipe.steps[invalidator_name]
    success_target = invalidator.on_success
    if not success_target or success_target not in recipe.steps:
        return
    for variable in variables:
        barriers = capture_steps.get(variable, set())
        stale_reachable = _bfs_capped(graph, {success_target}, barriers)
        stale_reachable.discard(invalidator_name)
        for downstream_name in stale_reachable:
            downstream = recipe.steps.get(downstream_name)
            if downstream is None:
                continue
            for argument in (downstream.with_args or {}).values():
                if not isinstance(argument, str):
                    continue
                for referenced_variable in _CONTEXT_REF_RE.findall(argument):
                    if referenced_variable == variable:
                        yield downstream_name, variable


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

    capture_steps = _capture_step_index(recipe)
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

        for downstream_name, variable in _iter_stale_context_refs(
            recipe, graph, step_name, invalidated_vars, capture_steps
        ):
            warnings.append(
                DataFlowWarning(
                    code="REF_INVALIDATED",
                    step_name=downstream_name,
                    field=variable,
                    message=(
                        f"Step '{downstream_name}' references "
                        f"context.{variable} after step '{step_name}' "
                        f"({step.tool}) has invalidated the underlying "
                        f"resource. Replace with a stable alternative "
                        f"(e.g., a commit SHA captured before any merge "
                        f"begins)."
                    ),
                )
            )

    return warnings


def _detect_stale_captured_paths(
    recipe: Recipe, graph: dict[str, set[str]]
) -> list[DataFlowWarning]:
    """Detect output path tokens captured from worktree-cwd steps that are
    consumed after ``merge_worktree`` deletes the worktree.

    Complements ``_detect_ref_invalidations`` by catching transitive path
    captures: where a variable is sourced from a result token emitted by a
    step running inside the worktree, and that variable is later consumed
    by a step after merge.
    """
    warnings: list[DataFlowWarning] = []

    # Steps that run inside a worktree (cwd references worktree_path)
    worktree_cwd_steps: dict[str, list[str]] = {}
    for step_name, step in recipe.steps.items():
        cwd = step.with_args.get("cwd", "") if step.with_args else ""
        if "worktree_path" in cwd:
            for cap_var in step.capture or {}:
                worktree_cwd_steps.setdefault(cap_var, []).append(step_name)
            for cap_var in step.capture_list or {}:
                worktree_cwd_steps.setdefault(cap_var, []).append(step_name)

    if not worktree_cwd_steps:
        return warnings

    capture_steps = _capture_step_index(recipe)

    for step_name, step in recipe.steps.items():
        if step.tool not in _INVALIDATING_TOOLS:
            continue

        for downstream_name, variable in _iter_stale_context_refs(
            recipe, graph, step_name, worktree_cwd_steps, capture_steps
        ):
            origin_step = worktree_cwd_steps[variable][0]
            warnings.append(
                DataFlowWarning(
                    code="CAPTURED_PATH_INVALIDATED",
                    step_name=downstream_name,
                    field=variable,
                    message=(
                        f"Step '{downstream_name}' references "
                        f"context.{variable} (captured from "
                        f"worktree-scoped step "
                        f"'{origin_step}') after step "
                        f"'{step_name}' ({step.tool}) has "
                        f"destroyed the worktree. Path tokens "
                        f"written to the worktree become "
                        f"unresolvable after merge."
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
        ("selected_lenses", "select-vis-lenses"),
        ("lens_context_paths", "select-vis-lenses"),
        ("pr_url", "compose-pr"),
        ("html_path", "bundle-local-report"),
        ("resource_report", "stage-data"),
        ("download_report", "download-data"),
        ("alignment_findings_path", "planner-validate-task-alignment"),
        ("review_approach_assessment_path", "planner-assess-review-approach"),
        # synthesize-vis-plan terminal handoff captures: emitted in food-truck sentinel,
        # not consumed by downstream recipe steps (route → stop action).
        ("visualization_plan_path", "synthesize-vis-plan"),
        ("report_plan_path", "synthesize-vis-plan"),
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
    _cap = (step.capture or {}).get(cap_key) or (step.capture_list or {}).get(cap_key)
    if (
        step.tool == "merge_worktree"
        and _cap is not None
        and "result.cleanup_succeeded" in _cap.from_
    ):
        return True

    # export_local_bundle local_bundle_path: matched by step name (terminal
    # step in the local-mode path that ends at a stop action).
    if cap_key == "local_bundle_path" and step_name == "export_local_bundle":
        return True

    # batch_create_issues issue_count: captured output used for telemetry/logging,
    # not threaded to downstream recipe steps (the done message uses issue_urls only).
    if cap_key == "issue_count" and step_name == "create_issues" and step.tool == "run_python":
        return True

    return False


def _consumed_context_for_step(step: RecipeStep) -> set[str]:
    """Collect context variables consumed by one reachable step."""
    consumed: set[str] = set()
    for argument in step.with_args.values():
        consumed.update(_context_refs_in_value(argument))
    if step.message and isinstance(step.message, str):
        consumed.update(_CONTEXT_REF_RE.findall(step.message))
    if step.on_result and step.on_result.conditions:
        for condition in step.on_result.conditions:
            if condition.when and isinstance(condition.when, str):
                consumed.update(_CONTEXT_REF_RE.findall(condition.when))
    if step.optional_context_refs:
        consumed.update(step.optional_context_refs)
    return consumed


def _detect_dead_outputs(recipe: Recipe, graph: dict[str, set[str]]) -> list[DataFlowWarning]:
    """Detect captured variables that are never consumed downstream."""
    warnings: list[DataFlowWarning] = []

    for step_name, step in recipe.steps.items():
        captured_names = list(step.capture or {}) + list(step.capture_list or {})
        if not captured_names:
            continue

        # BFS: collect all steps reachable from this step
        reachable = bfs_reachable(graph, step_name)

        # Collect all context.X references in reachable steps' with_args and
        # on_result condition when-expressions (route actions gate on context vars).
        consumed: set[str] = set()
        for reachable_name in reachable:
            reachable_step = recipe.steps[reachable_name]
            consumed.update(_consumed_context_for_step(reachable_step))

        # on_result routing — both legacy field and predicate conditions count
        # as structural consumption of captured variables.
        if step.on_result:
            # Legacy field routing: field name matches a captured key
            if step.on_result.field in (step.capture or {}) or step.on_result.field in (
                step.capture_list or {}
            ):
                consumed.add(step.on_result.field)
            # Predicate condition routing — conditions gate on step result;
            # treat all captured vars as structurally consumed.
            if step.on_result.conditions:
                consumed.update(captured_names)

        # Flag captured vars not consumed on any path
        for cap_key in captured_names:
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
        if step.tool in SKILL_TOOLS and not step.capture and not step.capture_list:
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
