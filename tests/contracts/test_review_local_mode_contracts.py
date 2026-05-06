"""Tests for skill_contracts.yaml and SKILL.md contract validation for local review mode.

Verifies that:
- skill_contracts.yaml documents the mode parameter for review-pr and resolve-review
- review-pr documents local_findings output when mode=local
- resolve-review documents deferred_observations and reject_patterns outputs
- deferred_observations JSON schema is documented in SKILL.md
"""

from pathlib import Path

import yaml

CONTRACTS_YAML = (
    Path(__file__).parent.parent.parent / "src" / "autoskillit" / "recipe" / "skill_contracts.yaml"
)

REVIEW_PR_SKILL_PATH = (
    Path(__file__).parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "review-pr"
    / "SKILL.md"
)

RESOLVE_REVIEW_SKILL_PATH = (
    Path(__file__).parent.parent.parent
    / "src"
    / "autoskillit"
    / "skills_extended"
    / "resolve-review"
    / "SKILL.md"
)


def _contracts() -> dict:
    return yaml.safe_load(CONTRACTS_YAML.read_text())


def _review_pr_skill() -> str:
    return REVIEW_PR_SKILL_PATH.read_text()


def _resolve_review_skill() -> str:
    return RESOLVE_REVIEW_SKILL_PATH.read_text()


def test_review_pr_contract_has_mode_input():
    """Assert skill_contracts.yaml review-pr inputs includes mode parameter."""
    contracts = _contracts()
    inputs = contracts.get("skills", {}).get("review-pr", {}).get("inputs", [])
    names = [inp["name"] for inp in inputs]
    assert "mode" in names, "skill_contracts.yaml review-pr inputs must include 'mode' parameter"


def test_resolve_review_contract_has_mode_input():
    """Assert skill_contracts.yaml resolve-review inputs includes mode parameter."""
    contracts = _contracts()
    inputs = contracts.get("skills", {}).get("resolve-review", {}).get("inputs", [])
    names = [inp["name"] for inp in inputs]
    assert "mode" in names, (
        "skill_contracts.yaml resolve-review inputs must include 'mode' parameter"
    )


def test_review_pr_contract_local_findings_output():
    """Assert review-pr outputs includes local_findings_path when mode=local."""
    contracts = _contracts()
    outputs = contracts.get("skills", {}).get("review-pr", {}).get("outputs", [])
    output_names = [o["name"] for o in outputs]
    assert "local_findings_path" in output_names, (
        "skill_contracts.yaml review-pr outputs must include 'local_findings_path' "
        "for the local_findings_{pr_number}.json output file"
    )


def test_resolve_review_contract_deferred_observations_output():
    """Assert skill_contracts.yaml resolve-review outputs includes deferred_observations_path."""
    contracts = _contracts()
    outputs = contracts.get("skills", {}).get("resolve-review", {}).get("outputs", [])
    output_names = [o["name"] for o in outputs]
    assert "deferred_observations_path" in output_names, (
        "skill_contracts.yaml resolve-review outputs must include 'deferred_observations_path' "
        "for the deferred_observations_{pr_number}.json output file"
    )


def test_resolve_review_contract_reject_patterns_output():
    """Assert skill_contracts.yaml resolve-review outputs includes reject_patterns_path."""
    contracts = _contracts()
    outputs = contracts.get("skills", {}).get("resolve-review", {}).get("outputs", [])
    output_names = [o["name"] for o in outputs]
    assert "reject_patterns_path" in output_names, (
        "skill_contracts.yaml resolve-review outputs must include 'reject_patterns_path' "
        "for the reject_patterns_{pr_number}.json output file"
    )


def test_deferred_observations_schema_review_pr_documents_iteration():
    """Verify that review-pr SKILL.md documents the iteration field in local_findings JSON
    (which resolve-review uses as the round number)."""
    text = _review_pr_skill()
    local_findings_idx = text.find("local_findings")
    assert local_findings_idx >= 0
    after = text[local_findings_idx : local_findings_idx + 1500]
    assert "iteration" in after.lower(), (
        "review-pr SKILL.md must document the 'iteration' field in local_findings JSON "
        "so resolve-review can use it as the round number in deferred_observations"
    )


def test_deferred_observations_schema_resolve_review_documents_round():
    """Verify resolve-review SKILL.md documents the JSON schema for deferred_observations
    with required fields: round, path, line, body, evidence, severity, dimension, verdict,
    category."""
    text = _resolve_review_skill()
    step36_idx = text.find("### Step 3.6")
    assert step36_idx >= 0
    step36_section = text[step36_idx : step36_idx + 2500]
    # Should document round field
    assert "round" in step36_section.lower(), (
        "resolve-review SKILL.md Step 3.6 must document the 'round' field in "
        "deferred_observations JSON schema"
    )
    # Should document path field
    assert '"path"' in step36_section or "path" in step36_section.lower(), (
        "resolve-review SKILL.md Step 3.6 must document the 'path' field in "
        "deferred_observations JSON schema"
    )
    # Should document line field
    assert '"line"' in step36_section or "line" in step36_section.lower(), (
        "resolve-review SKILL.md Step 3.6 must document the 'line' field in "
        "deferred_observations JSON schema"
    )
    # Should document body field
    assert '"body"' in step36_section or "body" in step36_section.lower(), (
        "resolve-review SKILL.md Step 3.6 must document the 'body' field in "
        "deferred_observations JSON schema"
    )
    # Should document evidence field
    assert '"evidence"' in step36_section or "evidence" in step36_section.lower(), (
        "resolve-review SKILL.md Step 3.6 must document the 'evidence' field in "
        "deferred_observations JSON schema"
    )
    # Should document severity field
    assert "severity" in step36_section.lower(), (
        "resolve-review SKILL.md Step 3.6 must document the 'severity' field in "
        "deferred_observations JSON schema"
    )
    # Should document dimension field
    assert "dimension" in step36_section.lower(), (
        "resolve-review SKILL.md Step 3.6 must document the 'dimension' field in "
        "deferred_observations JSON schema"
    )
    # Should document verdict field
    assert '"verdict"' in step36_section or "verdict" in step36_section.lower(), (
        "resolve-review SKILL.md Step 3.6 must document the 'verdict' field in "
        "deferred_observations JSON schema"
    )
    # Should document category field
    assert "category" in step36_section.lower(), (
        "resolve-review SKILL.md Step 3.6 must document the 'category' field in "
        "deferred_observations JSON schema"
    )


def test_review_flag_marker_format():
    """Verify the REVIEW-FLAG marker format matches the regex:
    <!--\\s*REVIEW-FLAG:\\s*severity=(\\w+)\\s+dimension=(\\w+)\\s*-->"""
    text = _resolve_review_skill()
    # The marker format should be documented
    assert "REVIEW-FLAG" in text, (
        "resolve-review SKILL.md must document the REVIEW-FLAG marker format"
    )
    step15_idx = text.find("### Step 1.5")
    assert step15_idx >= 0
    step15_section = text[step15_idx : step15_idx + 2500]
    # Should have severity= and dimension= in the marker
    assert "severity=" in step15_section.lower() and "dimension=" in step15_section.lower(), (
        "resolve-review SKILL.md Step 1.5 must document REVIEW-FLAG marker with "
        "severity={severity} dimension={dimension} format"
    )


def test_review_pr_mode_default_github():
    """Assert skill_contracts.yaml documents mode default as github."""
    contracts = _contracts()
    inputs = contracts.get("skills", {}).get("review-pr", {}).get("inputs", [])
    mode_input = next((i for i in inputs if i.get("name") == "mode"), None)
    assert mode_input is not None, "mode input must exist in review-pr contract"
    # mode should not be marked as required (it's optional with default)
    assert mode_input.get("required") is not True, (
        "mode parameter should not be required (has default: github)"
    )


def test_resolve_review_mode_default_github():
    """Assert skill_contracts.yaml documents mode default as github."""
    contracts = _contracts()
    inputs = contracts.get("skills", {}).get("resolve-review", {}).get("inputs", [])
    mode_input = next((i for i in inputs if i.get("name") == "mode"), None)
    assert mode_input is not None, "mode input must exist in resolve-review contract"
    assert mode_input.get("required") is not True, (
        "mode parameter should not be required (has default: github)"
    )
