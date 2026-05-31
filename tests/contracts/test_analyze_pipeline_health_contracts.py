"""Contract tests for the analyze-pipeline-health skill."""

from __future__ import annotations

import pytest

from autoskillit.core.io import load_yaml
from autoskillit.execution.session._session_content import _check_expected_patterns

pytestmark = [pytest.mark.layer("recipe"), pytest.mark.small]

_OUTPUT_DELIMITER = "---pipeline-health-result---"


def _load_contract() -> dict:
    from autoskillit.core import pkg_root

    contracts_path = pkg_root() / "recipe" / "skill_contracts.yaml"
    assert contracts_path.is_file(), f"skill_contracts.yaml not found at {contracts_path}"
    raw = load_yaml(contracts_path) or {}
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
    assert contract, "analyze-pipeline-health entry missing from skill_contracts.yaml"
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
    """Each pattern_example must match the output delimiter via the normalizer."""
    contract = _load_contract()
    assert contract, "analyze-pipeline-health entry missing from skill_contracts.yaml"
    examples = contract.get("pattern_examples", [])
    assert examples, "pattern_examples must be non-empty"
    for example in examples:
        assert _check_expected_patterns(example, [_OUTPUT_DELIMITER]), (
            f"pattern_example must match '{_OUTPUT_DELIMITER}' after normalization: {example!r}"
        )
