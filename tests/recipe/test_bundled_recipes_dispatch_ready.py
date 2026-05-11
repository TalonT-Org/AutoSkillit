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
from autoskillit.recipe.validator import run_semantic_rules

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]

_RECIPES_DIR = builtin_recipes_dir()
_CONTRACTS_DIR = _RECIPES_DIR / "contracts"
_RECIPE_STEMS = sorted(p.stem for p in _RECIPES_DIR.glob("*.yaml"))
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
def test_no_epoch_sentinel_in_contract(contract_name: str) -> None:
    contract_path = _CONTRACTS_DIR / f"{contract_name}.yaml"
    contract = yaml.safe_load(contract_path.read_text())
    generated_at = contract.get("generated_at", "")
    assert "1970-01-01" not in str(generated_at), (
        f"Contract '{contract_name}' has epoch sentinel generated_at={generated_at}. "
        f'Regenerate with: python -c "from autoskillit.recipe.contracts import '
        f"generate_recipe_card; from autoskillit.recipe.io import builtin_recipes_dir; "
        f"generate_recipe_card(builtin_recipes_dir()/'{contract_name}.yaml', "
        f'builtin_recipes_dir())"'
    )


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
