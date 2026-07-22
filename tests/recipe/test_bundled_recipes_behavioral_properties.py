"""Behavioral property registry tests for bundled recipes.

These tests assert that bundled recipes satisfy structural behavioral
properties (on_context_limit coverage, dispatch mode consistency,
model adequacy for context-intensive steps) beyond simple schema
presence. They serve as a second line of defense alongside the
semantic rules in recipe/rules/ — if a rule's severity is reduced
or a finding is suppressed, these tests still catch the gap.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.recipe.io import all_validated_recipe_paths, builtin_recipes_dir, load_recipe

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_ALL_PATHS = all_validated_recipe_paths(_PROJECT_ROOT)
_BUNDLED_ONLY = [p for p in _ALL_PATHS if "src/autoskillit/recipes" in str(p)]
assert _BUNDLED_ONLY, "no bundled recipes found"
_RECIPE_NAMES = [p.name for p in _BUNDLED_ONLY]


CONTEXT_LIMIT_EXEMPT_STEPS: dict[str, set[str]] = {
    "planner": set(),
    "remediation": set(),
    "research": {
        "scope",
        "select_directions",
        "plan_experiment",
        "vis_dial",
        "vis_apply",
        "vis_synthesize",
        "stage_data",
        "download_data",
        "setup_environment",
        "decompose_phases",
        "troubleshoot_implement_failure",
        "run_experiment",
        "troubleshoot_run_failure",
        "prepare_research_pr",
        "run_experiment_lenses",
        "compose_research_pr",
        "finalize_bundle_render",
    },
    "implementation": set(),
    "implementation-groups": {"group"},
    "merge-prs": {
        "analyze_prs",
        "diagnose_queue_ci",
        "open_integration_pr",
        "review_pr_integration",
        "diagnose_ci",
    },
    "full-audit": {"run_audits", "validate_audits", "create_issues"},
    "bem-wrapper": {"run_bem"},
    "research-design": {
        "scope",
        "select_directions",
        "plan_experiment",
        "vis_dial",
        "vis_apply",
        "vis_synthesize",
    },
    "research-implement": {
        "stage_data",
        "download_data",
        "decompose_phases",
        "troubleshoot_implement_failure",
        "troubleshoot_run_failure",
    },
    "research-review": {
        "prepare_research_pr",
        "run_experiment_lenses",
        "compose_research_pr",
        "finalize_bundle_render",
    },
}

# Every recipe key above with a non-empty exemption set must be cited here.
# tracking: #4305 — pre-existing gaps surfaced by dismantling the recipe-level
# allowlist this dict replaced; not yet given salvage routes.
CONTEXT_LIMIT_EXEMPT_STEPS_TRACKING: dict[str, str] = {
    "research": "#4305",
    "implementation-groups": "#4305",
    "merge-prs": "#4305",
    "full-audit": "#4305",
    "bem-wrapper": "#4305",
    "research-design": "#4305",
    "research-implement": "#4305",
    "research-review": "#4305",
}

_CONTEXT_LIMIT_EXEMPT_STEPS_CAP = 42

PARALLEL_ELIGIBLE_DISPATCH_STEPS: dict[str, set[str]] = {
    "planner": {
        "elaborate_phases",
        "elaborate_assignments",
        "elaborate_wps",
        "refine_assignments",
        "refine_wps",
    },
}

CONTEXT_INTENSIVE_STEPS: dict[str, set[str]] = {
    "planner": {"elaborate_wps", "elaborate_assignments", "elaborate_phases"},
}


def _recipe_base_name(filename: str) -> str:
    return filename.removesuffix(".yaml")


RATE_LIMIT_EXEMPT_STEPS: dict[str, set[str]] = {
    "planner": set(),
    "remediation": set(),
    "research": {
        "scope",
        "select_directions",
        "plan_experiment",
        "dial",
        "apply",
        "vis_dial",
        "vis_apply",
        "vis_synthesize",
        "resolve_design_review",
        "stage_data",
        "download_data",
        "setup_environment",
        "decompose_phases",
        "plan_phase",
        "implement_phase",
        "troubleshoot_implement_failure",
        "audit_impl",
        "run_experiment",
        "troubleshoot_run_failure",
        "adjust_experiment",
        "generate_report",
        "generate_report_inconclusive",
        "fix_tests",
        "prepare_research_pr",
        "run_experiment_lenses",
        "compose_research_pr",
        "review_research_pr",
        "audit_claims",
        "resolve_research_review",
        "resolve_claims_review",
        "re_run_experiment",
        "re_generate_report",
        "finalize_bundle_render",
    },
    "implementation": set(),
    "implementation-groups": set(),
    "merge-prs": set(),
    "full-audit": {"run_audits", "validate_audits", "create_issues"},
    "bem-wrapper": {"run_bem"},
    "implement-findings": {"run_bem_internally"},
    "research-design": {
        "scope",
        "select_directions",
        "plan_experiment",
        "dial",
        "apply",
        "vis_dial",
        "vis_apply",
        "vis_synthesize",
        "resolve_design_review",
    },
    "research-implement": {
        "stage_data",
        "download_data",
        "decompose_phases",
        "plan_phase",
        "implement_phase",
        "troubleshoot_implement_failure",
        "audit_impl",
        "run_experiment",
        "troubleshoot_run_failure",
        "adjust_experiment",
        "generate_report",
        "generate_report_inconclusive",
        "fix_tests",
    },
    "research-review": {
        "prepare_research_pr",
        "run_experiment_lenses",
        "compose_research_pr",
        "review_research_pr",
        "audit_claims",
        "resolve_research_review",
        "resolve_claims_review",
        "re_run_experiment",
        "re_generate_report",
        "finalize_bundle_render",
    },
}

# Every recipe key above with a non-empty exemption set must be cited here.
# tracking: #4305 — pre-existing gaps surfaced by dismantling the recipe-level
# allowlist this dict replaced; not yet given salvage routes.
RATE_LIMIT_EXEMPT_STEPS_TRACKING: dict[str, str] = {
    "research": "#4305",
    "full-audit": "#4305",
    "bem-wrapper": "#4305",
    "implement-findings": "#4305",
    "research-design": "#4305",
    "research-implement": "#4305",
    "research-review": "#4305",
}

_RATE_LIMIT_EXEMPT_STEPS_CAP = 70


def test_context_limit_exempt_steps_size_cap() -> None:
    """CONTEXT_LIMIT_EXEMPT_STEPS must not grow beyond current size.

    If this test fails, a new exemption was added. Fix the recipe (add a real
    on_context_limit salvage route) instead of silently growing this registry.
    """
    total = sum(len(v) for v in CONTEXT_LIMIT_EXEMPT_STEPS.values())
    assert total <= _CONTEXT_LIMIT_EXEMPT_STEPS_CAP, (
        f"CONTEXT_LIMIT_EXEMPT_STEPS has {total} entries (cap: "
        f"{_CONTEXT_LIMIT_EXEMPT_STEPS_CAP}). Fix the recipe instead of adding exemptions."
    )


def test_context_limit_exempt_steps_have_tracking_comments() -> None:
    """Every non-empty CONTEXT_LIMIT_EXEMPT_STEPS entry must cite a tracking issue."""
    missing = [
        recipe
        for recipe, steps in CONTEXT_LIMIT_EXEMPT_STEPS.items()
        if steps and recipe not in CONTEXT_LIMIT_EXEMPT_STEPS_TRACKING
    ]
    assert not missing, (
        f"CONTEXT_LIMIT_EXEMPT_STEPS entries missing a tracking citation: {missing}. "
        "Add an entry to CONTEXT_LIMIT_EXEMPT_STEPS_TRACKING with the relevant issue number."
    )
    stale = [
        recipe
        for recipe in CONTEXT_LIMIT_EXEMPT_STEPS_TRACKING
        if not CONTEXT_LIMIT_EXEMPT_STEPS.get(recipe)
    ]
    assert not stale, (
        f"CONTEXT_LIMIT_EXEMPT_STEPS_TRACKING has stale entries with no matching "
        f"non-empty exemption: {stale}. Remove them."
    )


def test_rate_limit_exempt_steps_size_cap() -> None:
    """RATE_LIMIT_EXEMPT_STEPS must not grow beyond current size.

    If this test fails, a new exemption was added. Fix the recipe (add a real
    on_rate_limit route) instead of silently growing this registry.
    """
    total = sum(len(v) for v in RATE_LIMIT_EXEMPT_STEPS.values())
    assert total <= _RATE_LIMIT_EXEMPT_STEPS_CAP, (
        f"RATE_LIMIT_EXEMPT_STEPS has {total} entries (cap: "
        f"{_RATE_LIMIT_EXEMPT_STEPS_CAP}). Fix the recipe instead of adding exemptions."
    )


def test_rate_limit_exempt_steps_have_tracking_comments() -> None:
    """Every non-empty RATE_LIMIT_EXEMPT_STEPS entry must cite a tracking issue."""
    missing = [
        recipe
        for recipe, steps in RATE_LIMIT_EXEMPT_STEPS.items()
        if steps and recipe not in RATE_LIMIT_EXEMPT_STEPS_TRACKING
    ]
    assert not missing, (
        f"RATE_LIMIT_EXEMPT_STEPS entries missing a tracking citation: {missing}. "
        "Add an entry to RATE_LIMIT_EXEMPT_STEPS_TRACKING with the relevant issue number."
    )
    stale = [
        recipe
        for recipe in RATE_LIMIT_EXEMPT_STEPS_TRACKING
        if not RATE_LIMIT_EXEMPT_STEPS.get(recipe)
    ]
    assert not stale, (
        f"RATE_LIMIT_EXEMPT_STEPS_TRACKING has stale entries with no matching "
        f"non-empty exemption: {stale}. Remove them."
    )


@pytest.mark.parametrize("recipe_name", _RECIPE_NAMES)
def test_run_skill_steps_declare_on_context_limit(recipe_name: str) -> None:
    """Every run_skill step must declare on_context_limit (or be exempt)."""
    recipe_path = next(p for p in _BUNDLED_ONLY if p.name == recipe_name)
    recipe = load_recipe(recipe_path)
    exempt = CONTEXT_LIMIT_EXEMPT_STEPS.get(_recipe_base_name(recipe_name), set())

    context_limit_targets: set[str] = set()
    for step in recipe.steps.values():
        if step.on_context_limit and step.on_context_limit not in (
            "escalate",
            "release_issue_failure",
        ):
            context_limit_targets.add(step.on_context_limit)

    missing: list[str] = []
    for name, step in recipe.steps.items():
        if step.tool != "run_skill":
            continue
        if step.action == "stop":
            continue
        if step.on_context_limit is not None:
            continue
        if name in context_limit_targets:
            continue
        if name in exempt:
            continue
        missing.append(name)

    assert not missing, (
        f"{recipe_name}: run_skill steps missing on_context_limit: {missing}. "
        f"Add on_context_limit: <recovery_step> to each, or add to CONTEXT_LIMIT_EXEMPT_STEPS."
    )


@pytest.mark.parametrize("recipe_name", _RECIPE_NAMES)
def test_run_skill_steps_declare_on_rate_limit(recipe_name: str) -> None:
    """Every run_skill step must declare on_rate_limit (or be exempt)."""
    recipe_path = next(p for p in _BUNDLED_ONLY if p.name == recipe_name)
    recipe = load_recipe(recipe_path)
    exempt = RATE_LIMIT_EXEMPT_STEPS.get(_recipe_base_name(recipe_name), set())

    rate_limit_targets: set[str] = set()
    for step in recipe.steps.values():
        if step.on_rate_limit and step.on_rate_limit not in (
            "escalate",
            "release_issue_failure",
        ):
            rate_limit_targets.add(step.on_rate_limit)

    missing: list[str] = []
    for name, step in recipe.steps.items():
        if step.tool != "run_skill":
            continue
        if step.action == "stop":
            continue
        if step.on_rate_limit is not None:
            continue
        if name in rate_limit_targets:
            continue
        if name in exempt:
            continue
        missing.append(name)

    assert not missing, (
        f"{recipe_name}: run_skill steps missing on_rate_limit: {missing}. "
        f"Add on_rate_limit: <recovery_step> to each, or add to RATE_LIMIT_EXEMPT_STEPS."
    )


@pytest.mark.parametrize(
    "recipe_name",
    [n for n in _RECIPE_NAMES if _recipe_base_name(n) in PARALLEL_ELIGIBLE_DISPATCH_STEPS],
)
def test_parallel_eligible_steps_use_parallel_dispatch(recipe_name: str) -> None:
    """Steps listed as parallel-eligible must use PARALLEL in step.note."""
    recipe_path = next(p for p in _BUNDLED_ONLY if p.name == recipe_name)
    recipe = load_recipe(recipe_path)
    base = _recipe_base_name(recipe_name)
    eligible = PARALLEL_ELIGIBLE_DISPATCH_STEPS.get(base, set())

    for step_name in eligible:
        step = recipe.steps[step_name]
        assert step.note, f"{recipe_name}.{step_name}: must have a note for dispatch instructions"
        assert "parallel" in step.note.lower(), (
            f"{recipe_name}.{step_name}: note must mention parallel dispatch. Got: {step.note!r}"
        )
        assert "sequential" not in step.note.lower(), (
            f"{recipe_name}.{step_name}: note must not mention sequential dispatch. "
            f"Got: {step.note!r}"
        )


@pytest.mark.parametrize(
    "recipe_name",
    [n for n in _RECIPE_NAMES if _recipe_base_name(n) in CONTEXT_INTENSIVE_STEPS],
)
def test_context_intensive_steps_declare_explicit_model(recipe_name: str) -> None:
    """Context-intensive steps must declare model != '' (not rely on default fallthrough)."""
    recipe_path = next(p for p in _BUNDLED_ONLY if p.name == recipe_name)
    recipe = load_recipe(recipe_path)
    base = _recipe_base_name(recipe_name)
    intensive = CONTEXT_INTENSIVE_STEPS.get(base, set())

    for step_name in intensive:
        step = recipe.steps[step_name]
        assert step.model is not None and step.model != "", (
            f"{recipe_name}.{step_name}: context-intensive step must declare an explicit "
            f"model (not empty string), got model={step.model!r}"
        )


# The nine (recipe, step) pairs whose skill contracts can trigger
# retry_reason=contract_recovery — audited risk table, issue #4305.
_SALVAGE_ROUTE_SITES: list[tuple[str, str]] = [
    ("remediation", "make_plan"),
    ("implementation", "plan"),
    ("implementation-groups", "plan"),
    ("research-implement", "plan_phase"),
    ("research", "plan_phase"),
    ("merge-prs", "plan"),
    ("remediation", "rectify"),
    ("remediation", "dry_walkthrough"),
    ("remediation", "audit_impl"),
]

# Destinations that abandon a salvageable artifact instead of attempting salvage.
_DESTRUCTIVE_SALVAGE_ROUTES: frozenset[str] = frozenset(
    {"release_issue_failure", "register_clone_failure", "escalate_stop"}
)


@pytest.mark.parametrize(
    "recipe_name,step_name",
    _SALVAGE_ROUTE_SITES,
    ids=[f"{r}:{s}" for r, s in _SALVAGE_ROUTE_SITES],
)
def test_contract_recovery_capable_steps_have_salvage_route(
    recipe_name: str, step_name: str
) -> None:
    """Steps whose skill contracts can trigger contract_recovery must declare a
    non-destructive, non-decorative on_context_limit salvage route (issue #4305)."""
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    step = recipe.steps[step_name]
    assert step.on_context_limit is not None, (
        f"{recipe_name}.{step_name}: on_context_limit must be set — for capture_list "
        f"steps (retries: 0 forced), on_context_limit is the only in-recipe salvage lever"
    )
    assert step.on_context_limit != step.on_failure, (
        f"{recipe_name}.{step_name}: on_context_limit={step.on_context_limit!r} is a "
        f"decorative alias of on_failure — it must attempt salvage before falling back"
    )
    assert step.on_context_limit not in _DESTRUCTIVE_SALVAGE_ROUTES, (
        f"{recipe_name}.{step_name}: on_context_limit={step.on_context_limit!r} routes "
        f"straight to a destructive terminal step, abandoning any salvageable artifact"
    )
    assert step.on_context_limit in recipe.steps, (
        f"{recipe_name}.{step_name}: on_context_limit target "
        f"{step.on_context_limit!r} does not exist as a step in this recipe"
    )


# Class-1 (plan-producing) sites and the salvage step that verifies their artifacts.
_CLASS1_SALVAGE_SITES: list[tuple[str, str, str]] = [
    ("remediation", "make_plan", "salvage_plan"),
    ("implementation", "plan", "salvage_plan"),
    ("implementation-groups", "plan", "salvage_plan"),
    ("research-implement", "plan_phase", "salvage_plan_phase"),
    ("research", "plan_phase", "salvage_plan_phase"),
    ("merge-prs", "plan", "salvage_plan"),
    ("remediation", "rectify", "salvage_rectify_plan"),
]


@pytest.mark.parametrize(
    "recipe_name,step_name,salvage_step_name",
    _CLASS1_SALVAGE_SITES,
    ids=[f"{r}:{s}" for r, s, _ in _CLASS1_SALVAGE_SITES],
)
def test_salvage_step_routes_match_plan_step_destinations(
    recipe_name: str, step_name: str, salvage_step_name: str
) -> None:
    """Class-1 salvage steps must route verdict==salvaged to the plan step's own
    success destination and verdict==unsalvageable to its own on_failure destination —
    read from the loaded recipe so the assertion survives future retargeting."""
    recipe = load_recipe(builtin_recipes_dir() / f"{recipe_name}.yaml")
    plan_step = recipe.steps[step_name]
    salvage_step = recipe.steps[salvage_step_name]

    assert plan_step.on_context_limit == salvage_step_name

    if plan_step.on_result is not None:
        plan_success_route = next(
            c.route
            for c in plan_step.on_result.conditions
            if c.when and "== plan" in c.when and "false_positive" not in c.when
        )
    else:
        assert plan_step.on_success is not None, (
            f"{recipe_name}.{step_name}: must declare either on_result (verdict "
            f"routing) or on_success — the salvage step's success destination is "
            f"read from whichever is present"
        )
        plan_success_route = plan_step.on_success

    assert salvage_step.on_result is not None
    salvaged_route = next(
        c.route for c in salvage_step.on_result.conditions if c.when and "salvaged" in c.when
    )
    assert salvaged_route == plan_success_route, (
        f"{recipe_name}.{salvage_step_name}: verdict==salvaged must route to "
        f"{plan_success_route!r} (the plan step's own success destination)"
    )

    unsalvageable_route = next(
        (c.route for c in salvage_step.on_result.conditions if c.when is None),
        salvage_step.on_failure,
    )
    assert unsalvageable_route == plan_step.on_failure, (
        f"{recipe_name}.{salvage_step_name}: verdict==unsalvageable must route to "
        f"{plan_step.on_failure!r} (the plan step's own on_failure destination)"
    )
