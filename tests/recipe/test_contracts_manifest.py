"""Tests for the contracts manifest loaders (get_skill_contract, get_callable_contract).

Verifies that `allowed_values` declared on output entries in skill_contracts.yaml
is promoted into the resulting SkillOutput — both for run_skill contracts and
run_python callable contracts (recipe-routing-deadlock immunity, #3889).
"""

from __future__ import annotations

import pytest

from autoskillit.recipe._contracts_manifest import (
    get_callable_contract,
    get_skill_contract,
    load_bundled_manifest,
    select_audit_output_contract,
)
from autoskillit.recipe._contracts_types import AuditOutputMode

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]


def test_get_callable_contract_promotes_allowed_values_for_commit_guard() -> None:
    """get_callable_contract must parse `allowed_values` from YAML into SkillOutput."""
    contract = get_callable_contract("autoskillit.recipe._cmd_rpc.commit_guard")
    assert contract is not None, (
        "commit_guard must be declared under callable_contracts in skill_contracts.yaml"
    )
    committed = next((o for o in contract.outputs if o.name == "committed"), None)
    assert committed is not None, "commit_guard contract must declare a 'committed' output"
    assert committed.allowed_values == ["false", "true", "regression_detected"]


def test_get_callable_contract_promotes_allowed_values_for_main_repo_guard() -> None:
    """get_callable_contract must parse `allowed_values` for main_repo_guard (new contract)."""
    contract = get_callable_contract("autoskillit.recipe._cmd_rpc.main_repo_guard")
    assert contract is not None, (
        "main_repo_guard must be declared under callable_contracts in skill_contracts.yaml"
    )
    cleaned = next((o for o in contract.outputs if o.name == "cleaned"), None)
    assert cleaned is not None, "main_repo_guard contract must declare a 'cleaned' output"
    assert cleaned.allowed_values == ["false", "true", "force", "failed"]


def test_get_callable_contract_promotes_allowed_values_for_verify_plan_artifacts() -> None:
    """get_callable_contract must parse `allowed_values` for verify_plan_artifacts."""
    contract = get_callable_contract("autoskillit.recipe._cmd_rpc.verify_plan_artifacts")
    assert contract is not None, (
        "verify_plan_artifacts must be declared under callable_contracts in skill_contracts.yaml"
    )
    verdict = next((o for o in contract.outputs if o.name == "verdict"), None)
    assert verdict is not None, "verify_plan_artifacts contract must declare a 'verdict' output"
    assert verdict.allowed_values == ["salvaged", "unsalvageable"]


def test_get_callable_contract_defaults_allowed_values_to_empty_list() -> None:
    """When a callable contract output has no `allowed_values` in YAML, defaults to []."""
    contract = get_callable_contract("autoskillit.recipe._cmd_rpc.review_path_rebase")
    assert contract is not None
    status = next((o for o in contract.outputs if o.name == "status"), None)
    assert status is not None
    assert status.allowed_values == []


def test_get_skill_contract_promotes_allowed_values_when_declared() -> None:
    """get_skill_contract must promote `allowed_values` from YAML into SkillOutput.

    Verifies the path is wired up symmetrically to get_callable_contract — adding
    allowed_values to a skill's output entry must surface on the resulting SkillOutput.
    """
    manifest = load_bundled_manifest()
    # Find any skill with allowed_values declared on an output
    skills = manifest.get("skills", {})
    target_skill: str | None = None
    target_output: dict | None = None
    for skill_name, skill_data in skills.items():
        for out in skill_data.get("outputs", []):
            if "allowed_values" in out:
                target_skill = skill_name
                target_output = out
                break
        if target_skill is not None:
            break
    if target_skill is None or target_output is None:
        pytest.skip(
            "No bundled skill currently declares allowed_values on an output — "
            "this test guards the loader path so future additions are safe."
        )
    contract = get_skill_contract(target_skill, manifest)
    assert contract is not None
    out = next((o for o in contract.outputs if o.name == target_output["name"]), None)
    assert out is not None
    assert out.allowed_values == target_output["allowed_values"]


def test_get_skill_contract_defaults_allowed_values_to_empty_list() -> None:
    """get_skill_contract must default `allowed_values` to [] when not declared in YAML."""
    manifest = load_bundled_manifest()
    # Pick a skill that has outputs but none with allowed_values
    skills = manifest.get("skills", {})
    for skill_name, skill_data in skills.items():
        outputs = skill_data.get("outputs", [])
        if outputs and not any("allowed_values" in o for o in outputs):
            contract = get_skill_contract(skill_name, manifest)
            assert contract is not None
            for o in contract.outputs:
                assert o.allowed_values == []
            return
    pytest.skip("No suitable skill found for default-empty assertion")


def test_audit_impl_selects_disjoint_attested_and_standalone_outputs() -> None:
    contract = get_skill_contract("audit-impl", load_bundled_manifest())
    assert contract is not None

    attested = select_audit_output_contract(contract, AuditOutputMode.ATTESTED)
    standalone = select_audit_output_contract(contract, AuditOutputMode.STANDALONE)

    assert {output.name for output in attested.outputs} == {
        "audit_semantic_result_path",
        "semantic_digest",
    }
    assert attested.audit_authority_publication is not None
    assert attested.audit_output_mode is AuditOutputMode.ATTESTED
    assert {output.name for output in standalone.outputs} == {
        "audit_status",
        "standalone_evidence_path",
        "content_digest",
    }
    assert standalone.audit_authority_publication is None
    assert standalone.audit_output_mode is AuditOutputMode.STANDALONE


@pytest.mark.parametrize("authority_data", [[], "invalid", 1])
def test_get_skill_contract_rejects_non_mapping_authority_publication(
    authority_data: object,
) -> None:
    manifest = {
        "skills": {
            "demo-skill": {
                "inputs": [{"name": "prior_authority", "type": "file_path"}],
                "outputs": [{"name": "authority", "type": "file_path"}],
                "audit_authority_publication": authority_data,
            }
        }
    }

    with pytest.raises(
        ValueError,
        match="audit_authority_publication for skill 'demo-skill' must be a mapping",
    ):
        get_skill_contract("demo-skill", manifest)
