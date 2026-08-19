"""Mutation-resistant fleet provenance prompt contracts."""

from __future__ import annotations

import inspect

import pytest

from autoskillit.cli.prompts import _prompts_campaign, _prompts_kitchen
from tests.cli._fleet_provenance_prompt_contract import (
    REQUIRED_PROVENANCE_CLAUSES,
    assert_provenance_contract,
    infrastructure_recovery_section,
)

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


@pytest.mark.parametrize("module", [_prompts_campaign, _prompts_kitchen])
def test_infrastructure_recovery_is_provenance_driven(module: object) -> None:
    assert_provenance_contract(inspect.getsource(module))


@pytest.mark.parametrize("module", [_prompts_campaign, _prompts_kitchen])
@pytest.mark.parametrize(
    ("clause_id", "required_text"),
    [(clause.clause_id, clause.text) for clause in REQUIRED_PROVENANCE_CLAUSES],
)
def test_clause_deletion_fails_its_own_invariant(
    module: object,
    clause_id: str,
    required_text: str,
) -> None:
    source = inspect.getsource(module)
    section = infrastructure_recovery_section(source)
    mutant = source.replace(section, section.replace(required_text, "", 1), 1)

    with pytest.raises(AssertionError, match=clause_id):
        assert_provenance_contract(mutant)


@pytest.mark.parametrize("module", [_prompts_campaign, _prompts_kitchen])
def test_decoy_vocabulary_outside_owning_section_cannot_satisfy_clause(
    module: object,
) -> None:
    source = inspect.getsource(module)
    section = infrastructure_recovery_section(source)
    mutant = (
        "Decoy: [missing-provenance-fails-closed] Missing effect_provenance never "
        "authorizes a fresh dispatch.\n"
        + source.replace(
            section,
            section.replace(
                "[missing-provenance-fails-closed]",
                "",
                1,
            ),
            1,
        )
    )

    with pytest.raises(AssertionError, match="missing-provenance-fails-closed"):
        assert_provenance_contract(mutant)
