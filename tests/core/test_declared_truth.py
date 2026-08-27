"""Unit coverage for the closed recipe step-guard truth dialect."""

from __future__ import annotations

import pytest

from autoskillit.core import (
    DeclaredTruthUnresolved,
    DeclaredTruthUnsupported,
    normalize_declared_truth,
)

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


@pytest.mark.parametrize("value", [True, "true", "TRUE", " true ", "1", "yes", "on"])
def test_normalize_declared_truth_accepts_truthy_values(value: object) -> None:
    assert normalize_declared_truth(value) is True


@pytest.mark.parametrize("value", [False, "false", "0", "no", "off", "", "   "])
def test_normalize_declared_truth_accepts_falsy_values(value: object) -> None:
    assert normalize_declared_truth(value) is False


def test_normalize_declared_truth_requires_a_value() -> None:
    with pytest.raises(DeclaredTruthUnresolved):
        normalize_declared_truth(None)


@pytest.mark.parametrize(
    "value",
    ["maybe", "t", "f", "y", "n", 0, 1, [], {}, 3.5, "${{ context.value }}"],
)
def test_normalize_declared_truth_rejects_unknown_values(value: object) -> None:
    with pytest.raises(DeclaredTruthUnsupported):
        normalize_declared_truth(value)
