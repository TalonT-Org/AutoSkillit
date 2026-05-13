"""Universal dispatch-readiness gate for all bundled recipes."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

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

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]

_RECIPES_DIR = builtin_recipes_dir()
_CONTRACTS_DIR = _RECIPES_DIR / "contracts"
# Include top-level *.yaml and campaigns/*.yaml (campaign recipes live in subdir)
_RECIPE_STEMS = sorted(
    list(p.stem for p in _RECIPES_DIR.glob("*.yaml"))
    + list(p.stem for p in _RECIPES_DIR.glob("campaigns/*.yaml"))
)
_CONTRACT_STEMS = sorted(p.stem for p in _CONTRACTS_DIR.glob("*.yaml"))


@pytest.mark.parametrize("recipe_name", _RECIPE_STEMS)
def test_bundled_recipe_dispatch_ready(recipe_name: str) -> None:
    result = load_and_validate(recipe_name)
    assert "error" not in result, f"Recipe '{recipe_name}' failed to load: {result.get('error')}"
    assert result.get("valid") is True, f"Recipe '{recipe_name}' not dispatch-ready: " + "; ".join(
        f"[{s.get('rule')}] {s.get('message', '')[:80]}"
        for s in result.get("suggestions", [])
        if s.get("severity") == Severity.ERROR
    )


@pytest.mark.parametrize("contract_name", _CONTRACT_STEMS)
def test_contract_card_fresh(contract_name: str) -> None:
    contract_path = _CONTRACTS_DIR / f"{contract_name}.yaml"
    contract = yaml.safe_load(contract_path.read_text())
    assert isinstance(contract, dict), f"Malformed: expected dict, got {type(contract)}"
    stale = check_contract_staleness(contract)
    assert stale == [], f"Contract '{contract_name}' is stale: {stale}"


@pytest.mark.parametrize("contract_name", _CONTRACT_STEMS)
def test_contract_covers_all_recipe_steps(contract_name: str) -> None:
    recipe_path = _RECIPES_DIR / f"{contract_name}.yaml"
    if not recipe_path.exists():
        pytest.skip(f"No recipe YAML for contract '{contract_name}'")
    contract_path = _CONTRACTS_DIR / f"{contract_name}.yaml"
    contract = yaml.safe_load(contract_path.read_text())
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
    from autoskillit.recipe.io import load_recipe
    from autoskillit.recipe.rules.rules_campaign import _extract_sentinel_fields

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
