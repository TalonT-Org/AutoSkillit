"""Manifest declarations that authorize a child plan edit."""

from __future__ import annotations

import pytest

from autoskillit.recipe.contracts import get_skill_contract, load_bundled_manifest

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def test_dry_walkthrough_declares_its_delegated_write_input() -> None:
    contract = get_skill_contract("dry-walkthrough", load_bundled_manifest())

    assert contract is not None
    assert contract.delegated_write_input == "plan_path"


def test_prepare_pr_declares_its_delegated_write_output() -> None:
    contract = get_skill_contract("prepare-pr", load_bundled_manifest())

    assert contract is not None
    assert contract.delegated_write_output == "prep_path"


def test_every_bundled_skill_contract_parses() -> None:
    manifest = load_bundled_manifest()

    for skill_name in manifest["skills"]:
        assert get_skill_contract(skill_name, manifest) is not None, skill_name


@pytest.mark.parametrize(
    ("inputs", "outputs", "delegated_input", "delegated_output", "write_behavior"),
    [
        ([{"name": "plan_path", "type": "file_path"}], [], "unknown_path", None, "always"),
        ([{"name": "plan_path", "type": "string"}], [], "plan_path", None, "always"),
        ([{"name": "plan_path", "type": "file_path"}], [], "plan_path", None, None),
        ([{"name": "plan_path", "type": "file_path"}], [], "plan_path", None, ["always"]),
        ([{"name": "plan_path", "type": "file_path"}], [], 1, None, "always"),
        ([], [{"name": "prep_path", "type": "file_path"}], None, 1, "always"),
        (
            [],
            [{"name": "prep_path", "type": "file_path"}],
            None,
            "unknown_path",
            "always",
        ),
        (
            [{"name": "plan_path", "type": "file_path"}],
            [],
            None,
            "plan_path",
            "always",
        ),
        ([], [{"name": "prep_path", "type": "string"}], None, "prep_path", "always"),
        ([], [{"name": "prep_path", "type": "file_path"}], None, "prep_path", None),
    ],
)
def test_invalid_delegated_write_declarations_name_the_skill(
    inputs: list[dict[str, object]],
    outputs: list[dict[str, object]],
    delegated_input: object,
    delegated_output: object,
    write_behavior: object,
) -> None:
    skill_data: dict[str, object] = {"inputs": inputs, "outputs": outputs}
    if delegated_input is not None:
        skill_data["delegated_write_input"] = delegated_input
    if delegated_output is not None:
        skill_data["delegated_write_output"] = delegated_output
    if write_behavior is not None:
        skill_data["write_behavior"] = write_behavior

    with pytest.raises(ValueError, match="example-skill"):
        get_skill_contract("example-skill", {"skills": {"example-skill": skill_data}})
