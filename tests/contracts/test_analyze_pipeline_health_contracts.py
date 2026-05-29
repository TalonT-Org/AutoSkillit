"""Contract tests for the analyze-pipeline-health skill."""

from __future__ import annotations

import pytest
import yaml

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_OUTPUT_DELIMITER = "---pipeline-health-result---"


def _load_contract() -> dict:
    from autoskillit.core import pkg_root

    contracts_path = pkg_root() / "recipe" / "skill_contracts.yaml"
    raw = yaml.safe_load(contracts_path.read_text())
    return raw.get("skills", {}).get("analyze-pipeline-health", {})


def test_analyze_pipeline_health_has_expected_output_patterns():
    """analyze-pipeline-health contract must declare non-empty expected_output_patterns."""
    contract = _load_contract()
    patterns = contract.get("expected_output_patterns", [])
    assert patterns, (
        "analyze-pipeline-health skill_contracts.yaml entry must have "
        "a non-empty 'expected_output_patterns' list"
    )


def test_analyze_pipeline_health_output_patterns_include_delimiter():
    """analyze-pipeline-health expected_output_patterns must include the result delimiter."""
    contract = _load_contract()
    patterns = contract.get("expected_output_patterns", [])
    assert any(_OUTPUT_DELIMITER in p for p in patterns), (
        f"analyze-pipeline-health expected_output_patterns must include "
        f"'{_OUTPUT_DELIMITER}' — got: {patterns}"
    )


def test_analyze_pipeline_health_has_pattern_examples():
    """analyze-pipeline-health contract must declare pattern_examples."""
    contract = _load_contract()
    examples = contract.get("pattern_examples", [])
    assert examples, (
        "analyze-pipeline-health skill_contracts.yaml entry must have "
        "a non-empty 'pattern_examples' list"
    )


def test_analyze_pipeline_health_pattern_examples_match_delimiter():
    """Each pattern_example must contain the output delimiter."""
    contract = _load_contract()
    examples = contract.get("pattern_examples", [])
    for example in examples:
        assert _OUTPUT_DELIMITER in example, (
            f"pattern_example must contain '{_OUTPUT_DELIMITER}': {example!r}"
        )
