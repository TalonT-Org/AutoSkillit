"""Tests for ProviderProfileDef frozen dataclass."""

from __future__ import annotations

import dataclasses

import pytest

pytestmark = [pytest.mark.layer("config"), pytest.mark.small]


class TestProviderProfileDefImports:
    def test_importable_from_config_dataclasses(self) -> None:
        from autoskillit.config._config_dataclasses import ProviderProfileDef

        assert ProviderProfileDef is not None

    def test_importable_from_settings(self) -> None:
        from autoskillit.config.settings import ProviderProfileDef

        assert ProviderProfileDef is not None

    def test_importable_from_config(self) -> None:
        from autoskillit.config import ProviderProfileDef

        assert ProviderProfileDef is not None

    def test_settings_identity(self) -> None:
        from autoskillit.config import ProviderProfileDef
        from autoskillit.config.settings import ProviderProfileDef as PC

        assert PC is ProviderProfileDef

    def test_in_settings_all(self) -> None:
        import autoskillit.config.settings as m

        assert "ProviderProfileDef" in m.__all__

    def test_in_config_all(self) -> None:
        import autoskillit.config as m

        assert "ProviderProfileDef" in m.__all__


class TestProviderProfileDefFields:
    def test_has_exactly_five_fields(self) -> None:
        from autoskillit.config._config_dataclasses import ProviderProfileDef

        fields = dataclasses.fields(ProviderProfileDef)
        assert len(fields) == 5
        assert [f.name for f in fields] == [
            "name",
            "base_url",
            "timeout_seconds",
            "api_key_env",
            "context_window",
        ]

    def test_defaults_with_name_only(self) -> None:
        from autoskillit.config._config_dataclasses import ProviderProfileDef

        p = ProviderProfileDef(name="openai")
        assert p.name == "openai"
        assert p.base_url is None
        assert p.timeout_seconds is None
        assert p.api_key_env is None
        assert p.context_window is None

    def test_all_fields_populated(self) -> None:
        from autoskillit.config._config_dataclasses import ProviderProfileDef

        p = ProviderProfileDef(
            name="openai",
            base_url="https://api.openai.com",
            timeout_seconds=30,
            api_key_env="OPENAI_API_KEY",
            context_window=128000,
        )
        assert p.name == "openai"
        assert p.base_url == "https://api.openai.com"
        assert p.timeout_seconds == 30
        assert p.api_key_env == "OPENAI_API_KEY"
        assert p.context_window == 128000


class TestProviderProfileDefFrozen:
    def test_frozen(self) -> None:
        from autoskillit.config._config_dataclasses import ProviderProfileDef

        p = ProviderProfileDef(name="test")
        with pytest.raises(dataclasses.FrozenInstanceError):
            p.name = "other"  # type: ignore[misc]

    def test_has_slots(self) -> None:
        from autoskillit.config._config_dataclasses import ProviderProfileDef

        assert ProviderProfileDef.__dataclass_params__.slots is True


class TestProviderProfileDefValidation:
    def test_timeout_negative_raises(self) -> None:
        from autoskillit.config._config_dataclasses import ProviderProfileDef

        with pytest.raises(ValueError, match="timeout_seconds must be non-negative"):
            ProviderProfileDef(name="x", timeout_seconds=-1)

    def test_timeout_zero_succeeds(self) -> None:
        from autoskillit.config._config_dataclasses import ProviderProfileDef

        p = ProviderProfileDef(name="x", timeout_seconds=0)
        assert p.timeout_seconds == 0

    def test_context_window_zero_raises(self) -> None:
        from autoskillit.config._config_dataclasses import ProviderProfileDef

        with pytest.raises(ValueError, match="context_window must be positive"):
            ProviderProfileDef(name="x", context_window=0)

    def test_context_window_negative_raises(self) -> None:
        from autoskillit.config._config_dataclasses import ProviderProfileDef

        with pytest.raises(ValueError, match="context_window must be positive"):
            ProviderProfileDef(name="x", context_window=-5)

    def test_context_window_one_succeeds(self) -> None:
        from autoskillit.config._config_dataclasses import ProviderProfileDef

        p = ProviderProfileDef(name="x", context_window=1)
        assert p.context_window == 1
