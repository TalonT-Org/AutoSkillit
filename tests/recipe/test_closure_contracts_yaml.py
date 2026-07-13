"""Contract tests: skill_contracts.yaml audit-impl entry must expose closure inputs/output."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core.io import load_yaml

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_CONTRACTS_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "src"
    / "autoskillit"
    / "recipe"
    / "skill_contracts.yaml"
)


def _load_audit_impl_entry() -> dict:
    data = load_yaml(_CONTRACTS_PATH)
    assert isinstance(data, dict), "skill_contracts.yaml must be a mapping"
    skills = data.get("skills")
    assert isinstance(skills, dict), "skill_contracts.yaml must have 'skills' mapping"
    entry = skills.get("audit-impl")
    assert isinstance(entry, dict), "skill_contracts.yaml must have 'audit-impl' entry"
    return entry


def test_audit_impl_contract_has_closure_inputs() -> None:
    """audit-impl entry has closure_authority_path and closure_authority_hash inputs."""
    entry = _load_audit_impl_entry()
    inputs = entry.get("inputs", [])
    input_names = {i.get("name") for i in inputs if isinstance(i, dict)}
    assert "closure_authority_path" in input_names, (
        "audit-impl contract must have 'closure_authority_path' input"
    )
    assert "closure_authority_hash" in input_names, (
        "audit-impl contract must have 'closure_authority_hash' input"
    )
    # Type check for closure_authority_path
    for inp in inputs:
        if inp.get("name") == "closure_authority_path":
            assert inp.get("type") == "file_path", "closure_authority_path must be type file_path"
            assert inp.get("required") is False, "closure_authority_path must be optional"
        if inp.get("name") == "closure_authority_hash":
            assert inp.get("type") == "string", "closure_authority_hash must be type string"
            assert inp.get("required") is False, "closure_authority_hash must be optional"


def test_audit_impl_contract_has_verified_verdict_output() -> None:
    """skill_contracts.yaml audit-impl entry has verified_verdict output field."""
    entry = _load_audit_impl_entry()
    outputs = entry.get("outputs", [])
    output_names = {o.get("name") for o in outputs if isinstance(o, dict)}
    assert "verified_verdict" in output_names, (
        "audit-impl contract must have 'verified_verdict' output"
    )
