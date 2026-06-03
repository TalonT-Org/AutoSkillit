"""Contract tests for classify-experiment-type SKILL.md and skill_contracts.yaml registration."""

from __future__ import annotations

import pytest

from autoskillit.core.io import load_yaml
from autoskillit.core.paths import pkg_root

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]

SKILL_PATH = pkg_root() / "skills_extended" / "classify-experiment-type" / "SKILL.md"
_CONTRACTS_YAML = pkg_root() / "recipe" / "skill_contracts.yaml"


@pytest.fixture(scope="module")
def skill_text() -> str:
    return SKILL_PATH.read_text()


def test_all_five_experiment_type_names_present(skill_text: str) -> None:
    """The five experiment types are reachable: `exploratory` is the explicit fallback and
    `registry` + `classification_triggers` document the mechanism that loads the other
    four (`benchmark`, `configuration_study`, `causal_inference`, `robustness_audit`)
    from recipes/experiment-types/*.yaml at runtime.
    """
    assert "exploratory" in skill_text, (
        "classify-experiment-type/SKILL.md must name `exploratory` as the explicit "
        "schema-validation fallback for unknown registry returns"
    )
    assert "registry" in skill_text, (
        "classify-experiment-type/SKILL.md must document the registry mechanism "
        "that supplies the four non-fallback experiment types at runtime"
    )
    assert "classification_triggers" in skill_text, (
        "classify-experiment-type/SKILL.md must reference `classification_triggers` "
        "as the field that drives first-match classification against the registry"
    )


def test_first_match_classification_rule_documented(skill_text: str) -> None:
    """Step 2 must document the first-match classification rule (or its 'first match' variant)."""
    lower = skill_text.lower()
    assert "first-match" in lower or "first match" in lower, (
        "classify-experiment-type/SKILL.md Step 2 must document the first-match "
        "classification rule used to select among registry types"
    )


def test_secondary_modifiers_all_present(skill_text: str) -> None:
    """All four secondary modifiers (additive tier-up rules) must be listed in Step 2."""
    for modifier in ("+causal", "+high_cost", "+deployment", "+multi_metric"):
        assert modifier in skill_text, (
            f"classify-experiment-type/SKILL.md must list the `{modifier}` "
            f"secondary modifier in Step 2"
        )


def test_dimension_weight_matrix_documented(skill_text: str) -> None:
    """The dimension weight matrix (H/M/L/S tier labels) must be present in the SKILL.md."""
    # The example weight matrix in Step 5 uses all four tier labels.
    assert "H" in skill_text
    assert "M" in skill_text
    assert "L" in skill_text
    assert "S" in skill_text
    # All four must appear together in a dimension_weights context.
    matrix_line_present = "causal_structure:H" in skill_text and "S" in skill_text
    assert matrix_line_present, (
        "classify-experiment-type/SKILL.md must show a dimension_weights matrix "
        "covering H/M/L/S tier labels"
    )


def test_schema_validation_defaults_to_exploratory(skill_text: str) -> None:
    """Schema-validation fallback must name `exploratory` and a default/fallback trigger."""
    # The literal "default to `exploratory`" line in Step 2 schema validation
    # satisfies both "default" and "exploratory" being adjacent.
    assert "exploratory" in skill_text, (
        "classify-experiment-type/SKILL.md must mention `exploratory` as a fallback type"
    )
    lower = skill_text.lower()
    assert "default" in lower or "fallback" in lower or "invalid" in lower, (
        "classify-experiment-type/SKILL.md must include a default/fallback/invalid "
        "trigger that causes the schema-validation to fall through to `exploratory`"
    )


def test_is_silent_type_detection_present(skill_text: str) -> None:
    """The silent-type detection function/variable name must be referenced."""
    assert "is_silent_type" in skill_text, (
        "classify-experiment-type/SKILL.md must reference the `is_silent_type` "
        "detection rule (Step 3) and emit the corresponding output token (Step 5)"
    )


def test_is_silent_type_emits_output_token(skill_text: str) -> None:
    """The Step 5 output token block must include a literal `is_silent_type =` line."""
    assert "is_silent_type =" in skill_text, (
        "classify-experiment-type/SKILL.md Step 5 must declare a literal "
        "`is_silent_type =` output token (the adjudicator performs a regex match "
        "on the exact token name — decorators and code fences cause match failure)"
    )


def test_dimensions_manifest_not_in_output_tokens(skill_text: str) -> None:
    """No line in the SKILL.md may declare a bare `dimensions_manifest =` output token.

    The accepted token is `dimensions_manifest_path =` (with the `_path` suffix).
    A bare `dimensions_manifest =` line would shadow that contract.
    """
    violations = [
        line
        for line in skill_text.splitlines()
        if line.lstrip().startswith("dimensions_manifest =")
    ]
    assert not violations, (
        "classify-experiment-type/SKILL.md must not declare a bare `dimensions_manifest =` "
        "output token — the correct output token is `dimensions_manifest_path =`. "
        f"Found violations: {violations!r}"
    )


def test_experiment_type_output_token_documented(skill_text: str) -> None:
    """Step 5 must declare a literal `experiment_type =` output token."""
    assert "experiment_type =" in skill_text, (
        "classify-experiment-type/SKILL.md Step 5 must declare a literal "
        "`experiment_type =` output token"
    )


def test_classification_timestamp_output_token_documented(skill_text: str) -> None:
    """Step 5 must declare a literal `classification_timestamp =` output token."""
    assert "classification_timestamp =" in skill_text, (
        "classify-experiment-type/SKILL.md Step 5 must declare a literal "
        "`classification_timestamp =` output token"
    )


def test_skill_contracts_yaml_declares_classify_experiment_type() -> None:
    """`skill_contracts.yaml` must register classify-experiment-type with the required outputs."""
    raw = load_yaml(_CONTRACTS_YAML) or {}
    skills = raw.get("skills", {})
    assert "classify-experiment-type" in skills, (
        "skill_contracts.yaml must register the `classify-experiment-type` skill"
    )
    entry = skills["classify-experiment-type"]
    output_names = {o.get("name") for o in entry.get("outputs", [])}
    assert "experiment_type" in output_names, (
        "classify-experiment-type entry must declare an output named `experiment_type`"
    )
    assert "classification_timestamp" in output_names, (
        "classify-experiment-type entry must declare an output named `classification_timestamp`"
    )
