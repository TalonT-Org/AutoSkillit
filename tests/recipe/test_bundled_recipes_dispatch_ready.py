"""Universal dispatch-readiness gate for all bundled recipes."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core.io import load_yaml
from autoskillit.core.types import Severity
from autoskillit.recipe._api import load_and_validate
from autoskillit.recipe.contracts import (
    check_contract_staleness,
    generate_recipe_card,
    validate_recipe_cards,
)
from autoskillit.recipe.io import all_validated_recipe_names, builtin_recipes_dir, load_recipe
from autoskillit.recipe.schema import RecipeKind
from autoskillit.recipe.validator import run_semantic_rules

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_RECIPES_DIR = builtin_recipes_dir()
_CONTRACTS_DIR = _RECIPES_DIR / "contracts"
# Unified discovery: covers builtin, campaigns, eval, and project-local recipes
_RECIPE_STEMS = all_validated_recipe_names(_PROJECT_ROOT)
_CONTRACT_STEMS = sorted(p.stem for p in _CONTRACTS_DIR.glob("*.yaml"))


_KNOWN_NON_CONFORMING_RULES: dict[str, set[str]] = {}

_RECIPES_WITH_OTHER_ERROR_RULES: frozenset[str] = frozenset(
    k for k, v in _KNOWN_NON_CONFORMING_RULES.items() if v
)

_DISPATCH_GATE_STEMS = [s for s in _RECIPE_STEMS if s not in _RECIPES_WITH_OTHER_ERROR_RULES]


@pytest.mark.parametrize("recipe_name", _DISPATCH_GATE_STEMS)
def test_bundled_recipe_dispatch_gate_no_exemptions(recipe_name: str) -> None:
    """Mirror the fleet/_api.py:365 hard gate — no per-rule allowlist.

    Recipes with pre-existing ERROR-severity findings from OTHER rules
    are excluded from this test. As those rules are fixed, recipes are
    automatically included here.

    If this test fails, fleet dispatch is broken for this recipe.
    """
    result = load_and_validate(recipe_name, project_dir=_PROJECT_ROOT)
    assert "error" not in result, f"Recipe '{recipe_name}' failed to load"
    assert result.get("valid") is True, (
        f"Recipe '{recipe_name}' would be REJECTED by fleet dispatch gate. "
        f"Error findings: "
        + "; ".join(
            f"[{s.get('rule')}] {s.get('message', '')[:80]}"
            for s in result.get("suggestions", [])
            if s.get("severity") == "error"
        )
    )


@pytest.mark.parametrize("recipe_name", _RECIPE_STEMS)
def test_bundled_recipe_dispatch_ready(recipe_name: str) -> None:
    result = load_and_validate(recipe_name, project_dir=_PROJECT_ROOT)
    assert "error" not in result, f"Recipe '{recipe_name}' failed to load: {result.get('error')}"
    excluded = _KNOWN_NON_CONFORMING_RULES.get(recipe_name, set())
    if not excluded:
        assert result.get("valid") is True, (
            f"Recipe '{recipe_name}' not dispatch-ready: "
            + "; ".join(
                f"[{s.get('rule')}] {s.get('message', '')[:80]}"
                for s in result.get("suggestions", [])
                if s.get("severity") == Severity.ERROR
            )
        )
    else:
        all_error_rules = {
            s.get("rule")
            for s in result.get("suggestions", [])
            if s.get("severity") == Severity.ERROR
        }
        for rule_name in excluded:
            assert rule_name in all_error_rules, (
                f"Recipe '{recipe_name}': exclusion for '{rule_name}' is stale — "
                f"rule no longer fires. Remove from _KNOWN_NON_CONFORMING_RULES."
            )
        error_suggestions = [
            s
            for s in result.get("suggestions", [])
            if s.get("severity") == Severity.ERROR and s.get("rule") not in excluded
        ]
        assert not error_suggestions, f"Recipe '{recipe_name}' not dispatch-ready: " + "; ".join(
            f"[{s.get('rule')}] {s.get('message', '')[:80]}" for s in error_suggestions
        )


# ---------------------------------------------------------------------------
# Per-backend dispatch readiness — must hold for codex (prunes guarded steps)
# and for the other backends (no pruning needed).
# ---------------------------------------------------------------------------


_BACKEND_OVERRIDES: dict[str, dict[str, str]] = {
    "claude-code": {},
    "codex": {"backend_supports_git_write": "false"},
}


@pytest.mark.parametrize("recipe_name", _DISPATCH_GATE_STEMS)
@pytest.mark.parametrize(
    "backend_name,ingredient_overrides",
    sorted(_BACKEND_OVERRIDES.items()),
    ids=lambda v: v if isinstance(v, str) else (sorted(v.keys())[0] if v else "no-overrides"),
)
def test_bundled_recipe_dispatch_ready_per_backend(
    recipe_name: str, backend_name: str, ingredient_overrides: dict[str, str]
) -> None:
    """All bundled dispatch-gate recipes must have no structural errors per backend.

    For codex, guarded steps with backend_supports_git_write=false are pruned.
    backend-incompatible-skill findings are expected on codex (merge-conflict steps
    require claude-code but are guarded by open_pr, not backend_supports_git_write)
    and are excluded from the dispatch-ready check — those are covered by
    test_admission_dispatch_agreement.
    """
    result = load_and_validate(
        recipe_name,
        project_dir=_PROJECT_ROOT,
        ingredient_overrides=ingredient_overrides,
        backend_name=backend_name,
    )
    assert "error" not in result, f"Recipe '{recipe_name}' failed to load: {result.get('error')}"
    non_compat_errors = [
        s
        for s in result.get("suggestions", [])
        if s.get("severity") == Severity.ERROR and s.get("rule") != "backend-incompatible-skill"
    ]
    assert not non_compat_errors, (
        f"Recipe '{recipe_name}' on backend '{backend_name}' not dispatch-ready: "
        + "; ".join(f"[{s.get('rule')}] {s.get('message', '')[:80]}" for s in non_compat_errors)
    )


@pytest.mark.parametrize("contract_name", _CONTRACT_STEMS)
def test_contract_card_fresh(contract_name: str) -> None:
    contract_path = _CONTRACTS_DIR / f"{contract_name}.yaml"
    contract = load_yaml(contract_path)
    assert isinstance(contract, dict), f"Malformed: expected dict, got {type(contract)}"
    stale = check_contract_staleness(contract)
    assert stale == [], f"Contract '{contract_name}' is stale: {stale}"


@pytest.mark.parametrize("contract_name", _CONTRACT_STEMS)
def test_contract_covers_all_recipe_steps(contract_name: str) -> None:
    recipe_path = _RECIPES_DIR / f"{contract_name}.yaml"
    if not recipe_path.exists():
        pytest.skip(f"No recipe YAML for contract '{contract_name}'")
    contract_path = _CONTRACTS_DIR / f"{contract_name}.yaml"
    contract = load_yaml(contract_path)
    recipe = load_recipe(recipe_path)

    contract_steps = {entry["step"] for entry in contract.get("dataflow", [])}
    recipe_skill_steps = {name for name, step in recipe.steps.items() if step.tool == "run_skill"}
    missing = recipe_skill_steps - contract_steps
    assert not missing, (
        f"Contract '{contract_name}' is missing dataflow entries for steps: {missing}. "
        f"Regenerate the contract card."
    )
    all_recipe_steps = set(recipe.steps.keys())
    orphaned = contract_steps - all_recipe_steps
    assert not orphaned, (
        f"Contract '{contract_name}' has orphaned dataflow entries for steps that no longer "
        f"exist in the recipe: {orphaned}. Regenerate the contract card."
    )


_RECIPES_WITH_CONTRACTS = sorted(
    p.stem for p in _CONTRACTS_DIR.glob("*.yaml") if (_RECIPES_DIR / f"{p.stem}.yaml").exists()
)


def test_all_campaign_dispatches_have_parseable_sentinels():
    """Every bundled campaign dispatch with non-empty capture must have
    at least one parseable sentinel stop step in its target recipe.

    Captures that reference fields not in any parseable sentinel example
    silently fail at runtime — the extractor looks for bare field names,
    but the recipe author may have written prose without a JSON example.

    Campaign recipes live in the campaigns/ subdirectory, not at the top level.
    """
    from autoskillit.recipe._rule_helpers import _extract_sentinel_fields
    from autoskillit.recipe.io import load_recipe

    # Collect campaign recipes with non-empty capture (campaigns are in campaigns/ subdir)
    campaigns_with_captures = []
    for campaign_path in sorted((_RECIPES_DIR / "campaigns").glob("*.yaml")):
        campaign = load_recipe(campaign_path)
        if campaign.kind == RecipeKind.CAMPAIGN and any(
            d.capture for d in campaign.dispatches if d.capture
        ):
            campaigns_with_captures.append((campaign_path.stem, campaign))

    failures: list[str] = []
    for campaign_name, campaign in campaigns_with_captures:
        for dispatch in campaign.dispatches:
            if not dispatch.capture:
                continue
            target_path = _RECIPES_DIR / f"{dispatch.recipe}.yaml"
            if not target_path.exists():
                continue
            target = load_recipe(target_path)
            sentinel_fields = _extract_sentinel_fields(target)
            if not sentinel_fields:
                failures.append(
                    f"Campaign '{campaign_name}' dispatch '{dispatch.name}' "
                    f"has captures but target recipe '{dispatch.recipe}' has no parseable sentinel"
                )
            else:
                # Also verify each captured field name appears in sentinel fields
                for cap_key in dispatch.capture.keys():
                    if cap_key not in sentinel_fields:
                        failures.append(
                            f"Campaign '{campaign_name}' dispatch '{dispatch.name}' "
                            f"captures field '{cap_key}' which is not in target recipe "
                            f"'{dispatch.recipe}' sentinel fields {sorted(sentinel_fields)}"
                        )
    assert not failures, "\n".join(failures)


@pytest.mark.parametrize("recipe_name", _RECIPES_WITH_CONTRACTS)
def test_card_and_semantic_rules_agree_on_errors(recipe_name: str, tmp_path: Path) -> None:
    recipe_path = _RECIPES_DIR / f"{recipe_name}.yaml"
    recipe = load_recipe(recipe_path)

    semantic_findings = run_semantic_rules(recipe)
    semantic_error_steps = {
        f.step_name
        for f in semantic_findings
        if f.rule == "missing-ingredient" and f.severity == Severity.ERROR
    }

    contract = generate_recipe_card(recipe_path, tmp_path)
    contract_findings = validate_recipe_cards(None, contract)
    contract_error_steps = {
        f["step"] for f in contract_findings if f.get("rule") == "contract-unsatisfied-input"
    }

    assert contract_error_steps == semantic_error_steps, (
        f"Recipe '{recipe_name}': card and semantic rules disagree on error steps. "
        f"Card-only: {contract_error_steps - semantic_error_steps}, "
        f"Semantic-only: {semantic_error_steps - contract_error_steps}"
    )


# ---------------------------------------------------------------------------
# Capability-admission-control feasibility signal
# ---------------------------------------------------------------------------


def test_codex_implementation_dispatch_infeasible() -> None:
    """Codex backend + implementation recipe must report dispatch_feasible=False
    when gate_backend_write is reachable post-prune (route-repair redirects
    upstream steps to the gate after implement is pruned).

    Note: valid may be False due to backend-incompatible-skill findings for
    merge-conflict steps (guarded by open_pr, not backend_supports_git_write).
    """
    result = load_and_validate(
        "implementation",
        project_dir=_PROJECT_ROOT,
        ingredient_overrides={"backend_supports_git_write": "false"},
        backend_name="codex",
    )
    assert result.get("dispatch_feasible") is False, (
        "When implement is pruned but create_impl_worktree.on_success is "
        "route-repaired to gate_backend_write, the gate is reachable and "
        "must block dispatch as infeasible."
    )
    assert "gate_backend_write" in result.get("infeasible_steps", [])


def test_claude_implementation_dispatch_feasible() -> None:
    """Claude Code backend + implementation recipe must report dispatch_feasible=True."""
    result = load_and_validate(
        "implementation",
        project_dir=_PROJECT_ROOT,
        ingredient_overrides={"backend_supports_git_write": "true"},
        backend_name="claude-code",
    )
    assert result["valid"] is True
    assert result.get("dispatch_feasible") is True


def test_dispatch_feasible_true_when_capability_overrides_are_truthy() -> None:
    """Capability-gated recipes report dispatch_feasible=True when all
    capability ingredient overrides evaluate as truthy."""
    result = load_and_validate(
        "implementation",
        project_dir=_PROJECT_ROOT,
        ingredient_overrides={"backend_supports_git_write": "true"},
    )
    assert result.get("dispatch_feasible") is True
    assert "infeasible_steps" not in result


def _discover_capability_gate_recipes() -> list[str]:
    """Return recipe names that contain a gate_backend_write step."""
    gate_recipes = []
    for name in _RECIPE_STEMS:
        recipe_path = _RECIPES_DIR / f"{name}.yaml"
        if not recipe_path.exists():
            continue
        recipe = load_recipe(recipe_path)
        for step in recipe.steps.values():
            if step.tool == "run_python" and step.with_args.get("callable", "").endswith(
                "gate_backend_write"
            ):
                gate_recipes.append(name)
                break
    return gate_recipes


_CAPABILITY_GATE_RECIPES = _discover_capability_gate_recipes()


@pytest.mark.parametrize("recipe_name", _CAPABILITY_GATE_RECIPES)
def test_codex_capability_gate_recipes(recipe_name: str) -> None:
    """Every recipe with a gate_backend_write step must report dispatch_feasible=False
    with gate_backend_write in infeasible_steps when the gate is reachable post-prune.

    Route-repair in _prune_skipped_steps redirects create_impl_worktree.on_success
    to gate_backend_write after pruning the guarded steps, making the gate reachable
    from the entry step. Admission control must identify this as an infeasible pipeline.

    Note: valid may be False due to backend-incompatible-skill findings for
    merge-conflict steps (guarded by open_pr, not backend_supports_git_write).
    """
    result = load_and_validate(
        recipe_name,
        project_dir=_PROJECT_ROOT,
        ingredient_overrides={"backend_supports_git_write": "false"},
        backend_name="codex",
    )
    assert result.get("dispatch_feasible") is False, (
        f"Recipe '{recipe_name}' must report dispatch_feasible=False when "
        f"gate_backend_write is reachable post-prune; got: "
        f"{result.get('dispatch_feasible')!r}"
    )
    assert "gate_backend_write" in result.get("infeasible_steps", []), (
        f"Recipe '{recipe_name}' must list gate_backend_write in infeasible_steps; "
        f"got: {result.get('infeasible_steps')}"
    )
