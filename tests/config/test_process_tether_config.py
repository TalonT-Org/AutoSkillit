"""Validation for process-tether lifetime policy."""

import math

import pytest

from autoskillit.config._config_dataclasses import ProcessTetherConfig

pytestmark = [pytest.mark.layer("config"), pytest.mark.small]


@pytest.mark.parametrize("field", ["cook_ceiling_seconds", "orphan_ceiling_seconds"])
@pytest.mark.parametrize("value", [0.0, -1.0, math.nan, math.inf])
def test_validate_rejects_invalid_ceiling(field: str, value: float) -> None:
    config = (
        ProcessTetherConfig(cook_ceiling_seconds=value)
        if field == "cook_ceiling_seconds"
        else ProcessTetherConfig(orphan_ceiling_seconds=value)
    )
    with pytest.raises(ValueError, match=field):
        config.validate()


def test_cook_max_extension_defaults_to_one_day() -> None:
    assert ProcessTetherConfig().cook_max_extension_seconds == 86400.0


@pytest.mark.parametrize("value", [-1.0, math.nan, math.inf])
def test_validate_rejects_invalid_cook_max_extension(value: float) -> None:
    with pytest.raises(ValueError, match="cook_max_extension_seconds"):
        ProcessTetherConfig(cook_max_extension_seconds=value).validate()


def test_validate_accepts_zero_cook_max_extension() -> None:
    ProcessTetherConfig(cook_max_extension_seconds=0.0).validate()
