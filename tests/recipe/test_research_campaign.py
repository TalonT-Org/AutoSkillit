"""Contract card assertions for the research-campaign recipe."""

import pytest
import yaml

from autoskillit.recipe.contracts import check_contract_staleness, load_bundled_manifest
from autoskillit.recipe.io import builtin_recipes_dir

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.medium]


def test_research_campaign_contract_exists():
    contracts_dir = builtin_recipes_dir() / "contracts"
    assert (contracts_dir / "research-campaign.yaml").exists(), (
        'Regenerate: python -c "from autoskillit.recipe.contracts import generate_recipe_card; '
        "from autoskillit.recipe.io import builtin_recipes_dir; "
        "generate_recipe_card("
        "builtin_recipes_dir()/'campaigns'/'research-campaign.yaml', builtin_recipes_dir())\""
    )


def test_research_campaign_contract_is_fresh():
    contract_path = builtin_recipes_dir() / "contracts" / "research-campaign.yaml"
    contract = yaml.safe_load(contract_path.read_text())
    stale = check_contract_staleness(contract)
    assert stale == [], f"Contract is stale: {stale}"


def test_research_campaign_contract_version_matches():
    contract_path = builtin_recipes_dir() / "contracts" / "research-campaign.yaml"
    contract = yaml.safe_load(contract_path.read_text())
    assert contract["bundled_manifest_version"] == load_bundled_manifest()["version"]
