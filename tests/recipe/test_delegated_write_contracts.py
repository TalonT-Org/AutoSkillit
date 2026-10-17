"""Manifest declarations that authorize a child plan edit."""

from __future__ import annotations

import pytest

from autoskillit.recipe.contracts import get_skill_contract, load_bundled_manifest

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def test_dry_walkthrough_declares_its_delegated_write_input() -> None:
    contract = get_skill_contract("dry-walkthrough", load_bundled_manifest())

    assert contract is not None
    assert contract.delegated_write_input == "plan_path"


def test_every_bundled_skill_contract_parses() -> None:
    manifest = load_bundled_manifest()

    for skill_name in manifest["skills"]:
        assert get_skill_contract(skill_name, manifest) is not None, skill_name


@pytest.mark.parametrize(
    ("inputs", "delegated_input", "write_behavior"),
    [
        ([{"name": "plan_path", "type": "file_path"}], "unknown_path", "always"),
        ([{"name": "plan_path", "type": "string"}], "plan_path", "always"),
        ([{"name": "plan_path", "type": "file_path"}], "plan_path", None),
        ([{"name": "plan_path", "type": "file_path"}], "plan_path", ["always"]),
        ([{"name": "plan_path", "type": "file_path"}], 1, "always"),
    ],
)
def test_invalid_delegated_write_declarations_name_the_skill(
    inputs: list[dict[str, object]],
    delegated_input: object,
    write_behavior: object,
) -> None:
    skill_data: dict[str, object] = {"inputs": inputs, "outputs": []}
    skill_data["delegated_write_input"] = delegated_input
    if write_behavior is not None:
        skill_data["write_behavior"] = write_behavior

    with pytest.raises(ValueError, match="example-skill"):
        get_skill_contract("example-skill", {"skills": {"example-skill": skill_data}})
