"""Tests for evaluate_skip_when — campaign/inputs ref resolution and expression evaluation."""

from __future__ import annotations

import pytest

from autoskillit.core import FleetErrorCode
from autoskillit.fleet import evaluate_skip_when

pytestmark = [pytest.mark.layer("fleet"), pytest.mark.small, pytest.mark.feature("fleet")]


# ---------------------------------------------------------------------------
# Campaign ref resolution
# ---------------------------------------------------------------------------


def test_skip_when_literal_equal_true() -> None:
    error_code, _, should_skip = evaluate_skip_when("'local' == 'local'", {})
    assert error_code is None
    assert should_skip is True


def test_skip_when_literal_equal_false() -> None:
    error_code, _, should_skip = evaluate_skip_when("'pr' == 'local'", {})
    assert error_code is None
    assert should_skip is False


def test_skip_when_literal_not_equal_true() -> None:
    error_code, _, should_skip = evaluate_skip_when("'pr' != 'local'", {})
    assert error_code is None
    assert should_skip is True


def test_skip_when_campaign_ref_resolved() -> None:
    error_code, _, should_skip = evaluate_skip_when(
        "${{ campaign.mode }} == 'local'",
        accumulated_captures={"mode": "local"},
    )
    assert error_code is None
    assert should_skip is True


def test_skip_when_campaign_ref_not_equal() -> None:
    error_code, _, should_skip = evaluate_skip_when(
        "${{ campaign.mode }} != 'local'",
        accumulated_captures={"mode": "github"},
    )
    assert error_code is None
    assert should_skip is True


def test_skip_when_missing_campaign_ref_returns_error() -> None:
    error_code, msg, _ = evaluate_skip_when(
        "${{ campaign.missing_key }} == 'local'",
        accumulated_captures={},
    )
    assert error_code == FleetErrorCode.FLEET_UNKNOWN_INGREDIENT
    assert "missing_key" in (msg or "")


# ---------------------------------------------------------------------------
# inputs.* ref resolution (regression: was silently not substituted before)
# ---------------------------------------------------------------------------


def test_skip_when_inputs_ref_resolved_true() -> None:
    error_code, _, should_skip = evaluate_skip_when(
        "${{ inputs.output_mode }} == 'local'",
        accumulated_captures={},
        ingredients={"output_mode": "local"},
    )
    assert error_code is None
    assert should_skip is True


def test_skip_when_inputs_ref_resolved_false() -> None:
    error_code, _, should_skip = evaluate_skip_when(
        "${{ inputs.output_mode }} == 'local'",
        accumulated_captures={},
        ingredients={"output_mode": "github"},
    )
    assert error_code is None
    assert should_skip is False


def test_skip_when_missing_inputs_ref_returns_error() -> None:
    error_code, msg, _ = evaluate_skip_when(
        "${{ inputs.pr_url }} == ''",
        accumulated_captures={},
        ingredients={},
    )
    assert error_code == FleetErrorCode.FLEET_RECIPE_INVALID
    assert "pr_url" in (msg or "")


def test_skip_when_inputs_and_campaign_refs_combined() -> None:
    error_code, _, should_skip = evaluate_skip_when(
        "${{ inputs.mode }} == ${{ campaign.expected_mode }}",
        accumulated_captures={"expected_mode": "local"},
        ingredients={"mode": "local"},
    )
    assert error_code is None
    assert should_skip is True


# ---------------------------------------------------------------------------
# Expression validation
# ---------------------------------------------------------------------------


def test_skip_when_empty_expression_error() -> None:
    error_code, msg, _ = evaluate_skip_when(
        "${{ campaign.x }}",
        accumulated_captures={"x": ""},
    )
    assert error_code == FleetErrorCode.FLEET_RECIPE_INVALID
    assert "empty" in (msg or "").lower()


def test_skip_when_malformed_operator_count_error() -> None:
    error_code, msg, _ = evaluate_skip_when(
        "${{ campaign.x }} == ${{ campaign.y }} == ${{ campaign.z }}",
        accumulated_captures={"x": "a", "y": "b", "z": "c"},
    )
    assert error_code == FleetErrorCode.FLEET_RECIPE_INVALID
    assert "malformed" in (msg or "").lower()


def test_skip_when_no_operator_error() -> None:
    error_code, msg, _ = evaluate_skip_when("'local'", accumulated_captures={})
    assert error_code == FleetErrorCode.FLEET_RECIPE_INVALID
    assert "malformed" in (msg or "").lower()


# ---------------------------------------------------------------------------
# Quote stripping
# ---------------------------------------------------------------------------


def test_skip_when_double_quoted_values() -> None:
    error_code, _, should_skip = evaluate_skip_when('"local" == "local"', {})
    assert error_code is None
    assert should_skip is True


def test_skip_when_unquoted_values() -> None:
    error_code, _, should_skip = evaluate_skip_when("local == local", {})
    assert error_code is None
    assert should_skip is True
