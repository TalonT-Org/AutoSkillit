import pytest

pytestmark = [pytest.mark.layer("config"), pytest.mark.small]


def test_leaf_dataclasses_importable_from_submodule():
    from autoskillit.config._config_dataclasses import (
        RunSkillConfig,
    )

    assert RunSkillConfig is not None


def test_loader_importable_from_submodule():
    from autoskillit.config._config_loader import load_config

    assert callable(load_config)


def test_public_api_unchanged():
    # config/__init__.py surface is unbroken after split
    from autoskillit.config import (
        AutomationConfig,
    )

    assert AutomationConfig is not None
