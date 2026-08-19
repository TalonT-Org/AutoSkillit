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
from autoskillit.recipe.io import builtin_recipes_dir, load_recipe
from autoskillit.recipe.schema import RecipeKind
from autoskillit.recipe.validator import run_semantic_rules
from tests._tracked_recipes import tracked_recipe_names
from tests.recipe.test_bundled_recipes_behavioral_properties import _SALVAGE_ROUTE_SITES

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_RECIPES_DIR = builtin_recipes_dir()
_CONTRACTS_DIR = _RECIPES_DIR / "contracts"
# Unified discovery: covers builtin, campaigns, eval, and project-local recipes
_RECIPE_STEMS = tracked_recipe_names(_PROJECT_ROOT)
_CONTRACT_STEMS = sorted(p.stem for p in _CONTRACTS_DIR.glob("*.yaml"))


_KNOWN_NON_CONFORMING_RULES: dict[str, set[str]] = {}

_RECIPES_WITH_OTHER_ERROR_RULES: frozenset[str] = frozenset(
    k for k, v in _KNOWN_NON_CONFORMING_RULES.items() if v
)

_DISPATCH_GATE_STEMS = [s for s in _RECIPE_STEMS if s not in _RECIPES_WITH_OTHER_ERROR_RULES]
_STRUCTURED_INPUT_RECIPES = (
    "implementation",
    "implementation-groups",
    "merge-prs",
    "remediation",
    "research",
)


def _part_a_dispatch_gate_params() -> list:
    return [pytest.param(n) for n in _DISPATCH_GATE_STEMS]


def _part_a_recipe_params() -> list:
    return [pytest.param(n) for n in _RECIPE_STEMS]


@pytest.mark.parametrize("recipe_name", _STRUCTURED_INPUT_RECIPES)
def test_structured_skill_inputs_do_not_embed_cli_option_prefixes(recipe_name: str) -> None:
    recipe = load_recipe(_RECIPES_DIR / f"{recipe_name}.yaml")

    for step_name, step in recipe.steps.items():
        skill_inputs = step.with_args.get("skill_inputs", {})
        if not isinstance(skill_inputs, dict):
            continue
        for name, value in skill_inputs.items():
            if not isinstance(value, str):
                continue
            cli_prefix = f"--{name.replace('_', '-')}="
            assert not value.startswith(cli_prefix), (
                f"{recipe_name}:{step_name} structured input {name!r} "
                f"must not embed CLI prefix {cli_prefix!r}"
            )


@pytest.mark.parametrize(
    "recipe_name,step_name,selection_marker",
    [
        ("implementation-groups", "plan", "CURRENT group file path"),
        ("research-implement", "plan_phase", "FIRST REMAINING phase file path"),
    ],
)
def test_group_planning_binds_the_selected_file_in_structured_input(
    recipe_name: str,
    step_name: str,
    selection_marker: str,
) -> None:
    recipe = load_recipe(_RECIPES_DIR / f"{recipe_name}.yaml")
    task = recipe.steps[step_name].with_args["skill_inputs"]["task"]

    assert task != "${{ context.group_files }}"
    assert selection_marker in task
    assert "${{ context.group_files }}" in task


@pytest.mark.parametrize(
    "recipe_name,step_name",
    _SALVAGE_ROUTE_SITES,
    ids=[f"{r}:{s}" for r, s in _SALVAGE_ROUTE_SITES],
)
def test_audited_salvage_sites_have_zero_contract_recovery_findings(
    recipe_name: str, step_name: str
) -> None:
    """Incident-shaped structural regression test (issue #4305).

    Every one of the nine audited contract-recovery-capable call sites must have zero
    contract-recovery-requires-salvage-route findings. Reverting any of the salvage
    routes wired for these sites turns this red — and
    test_contract_recovery_capable_steps_have_salvage_route (behavioral-properties)
    turns red too, providing dual coverage.

    Scoped to the audited nine sites rather than every bundled recipe: the rule's
    contract-derived eligibility predicate also matches steps outside this audited set
    that have not yet been given salvage routes (tracked separately, not yet promoted
    to ERROR — see rules_contract_recovery.py's module docstring).
    """
    result = load_and_validate(recipe_name, project_dir=_PROJECT_ROOT)
    assert "error" not in result, f"Recipe '{recipe_name}' failed to load"
    findings = [
        s
        for s in result.get("suggestions", [])
        if s.get("rule") == "contract-recovery-requires-salvage-route"
        and s.get("step") == step_name
    ]
    assert not findings, (
        f"Recipe '{recipe_name}' step '{step_name}' has contract-recovery-requires-salvage-route "
        "findings: " + "; ".join(f"{s.get('message', '')[:150]}" for s in findings)
    )


@pytest.mark.parametrize("recipe_name", _part_a_dispatch_gate_params(), ids=lambda n: n)
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


@pytest.mark.parametrize("recipe_name", _part_a_recipe_params(), ids=lambda n: n)
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
# Per-backend dispatch readiness.
# ---------------------------------------------------------------------------


_BACKEND_NAMES = ("claude-code", "codex")


@pytest.mark.parametrize("recipe_name", _part_a_dispatch_gate_params(), ids=lambda n: n)
@pytest.mark.parametrize("backend_name", _BACKEND_NAMES)
def test_bundled_recipe_dispatch_ready_per_backend(recipe_name: str, backend_name: str) -> None:
    """All bundled dispatch-gate recipes must have no structural errors per backend."""
    result = load_and_validate(
        recipe_name,
        project_dir=_PROJECT_ROOT,
        backend_name=backend_name,
    )
    assert "error" not in result, f"Recipe '{recipe_name}' failed to load: {result.get('error')}"
    errors = [s for s in result.get("suggestions", []) if s.get("severity") == Severity.ERROR]
    assert not errors, (
        f"Recipe '{recipe_name}' on backend '{backend_name}' not dispatch-ready: "
        + "; ".join(f"[{s.get('rule')}] {s.get('message', '')[:80]}" for s in errors)
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
