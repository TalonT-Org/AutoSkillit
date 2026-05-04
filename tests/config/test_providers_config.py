"""Tests for ProvidersConfig loading and validation."""

from __future__ import annotations

import pytest
import yaml

pytestmark = [pytest.mark.layer("config"), pytest.mark.small]


class TestProvidersConfig:
    def test_providers_config_importable_from_settings(self) -> None:
        from autoskillit.config import ProvidersConfig
        from autoskillit.config.settings import ProvidersConfig as PC

        assert PC is ProvidersConfig

    def test_providers_config_in_settings_all(self) -> None:
        import autoskillit.config.settings as m

        assert "ProvidersConfig" in m.__all__

    def test_automation_config_has_providers_field(self) -> None:
        from dataclasses import fields as _dc_fields

        from autoskillit.config import AutomationConfig, ProvidersConfig

        field_names = [f.name for f in _dc_fields(AutomationConfig)]
        assert "providers" in field_names
        cfg = AutomationConfig()
        assert isinstance(cfg.providers, ProvidersConfig)

    def test_providers_field_ordering(self) -> None:
        from dataclasses import fields as _dc_fields

        from autoskillit.config import AutomationConfig

        field_names = [f.name for f in _dc_fields(AutomationConfig)]
        fleet_idx = field_names.index("fleet")
        providers_idx = field_names.index("providers")
        features_idx = field_names.index("features")
        assert fleet_idx < providers_idx < features_idx

    def test_providers_config_importable_from_package(self) -> None:
        from autoskillit.config import ProvidersConfig as PC
        from autoskillit.config.settings import ProvidersConfig

        assert PC is ProvidersConfig

    def test_from_dynaconf_providers_defaults(self, tmp_path) -> None:
        from autoskillit.config import load_config

        cfg = load_config(tmp_path)
        assert cfg.providers.default_provider is None
        assert cfg.providers.profiles == {}
        assert cfg.providers.step_overrides == {}
        assert cfg.providers.provider_retry_limit == 2

    def test_providers_config_defaults(self) -> None:
        from autoskillit.config.settings import ProvidersConfig

        cfg = ProvidersConfig()
        assert cfg.default_provider is None
        assert cfg.profiles == {}
        assert cfg.step_overrides == {}
        assert cfg.provider_retry_limit == 2

    def test_providers_config_is_mutable(self) -> None:
        from autoskillit.config.settings import ProvidersConfig

        cfg = ProvidersConfig()
        cfg.default_provider = "openai"
        assert cfg.default_provider == "openai"

    def test_providers_config_field_types(self) -> None:
        from dataclasses import fields as _dc_fields

        from autoskillit.config.settings import ProvidersConfig

        field_map = {f.name: f for f in _dc_fields(ProvidersConfig)}
        assert set(field_map.keys()) == {
            "default_provider",
            "profiles",
            "step_overrides",
            "provider_retry_limit",
        }

    def test_providers_config_retry_limit_zero_raises(self) -> None:
        from autoskillit.config.settings import ProvidersConfig

        with pytest.raises(ValueError, match="provider_retry_limit must be >= 1"):
            ProvidersConfig(provider_retry_limit=0)

    def test_providers_config_retry_limit_negative_raises(self) -> None:
        from autoskillit.config.settings import ProvidersConfig

        with pytest.raises(ValueError, match="provider_retry_limit must be >= 1"):
            ProvidersConfig(provider_retry_limit=-1)

    def test_providers_config_profiles_non_string_value_raises(self) -> None:
        from autoskillit.config.settings import ProvidersConfig

        with pytest.raises(ValueError, match=r"profiles\[.+\]\[.+\] must be a string"):
            ProvidersConfig(profiles={"my_profile": {"model": 42}})  # type: ignore[arg-type]


class TestProvidersConfigYaml:
    def test_defaults_yaml_has_providers_section(self) -> None:
        from autoskillit.core.io import load_yaml
        from autoskillit.core.paths import pkg_root

        defaults = load_yaml(pkg_root() / "config" / "defaults.yaml")
        assert "providers" in defaults, "defaults.yaml missing 'providers' section"
        assert defaults["providers"]["provider_retry_limit"] == 2

    def test_load_config_step_overrides_parsing(self, tmp_path) -> None:
        from autoskillit.config import load_config

        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        config_data = {"providers": {"step_overrides": {"fetch-data": "openai"}}}
        (config_dir / "config.yaml").write_text(yaml.dump(config_data))
        cfg = load_config(tmp_path)
        assert cfg.providers.step_overrides == {"fetch-data": "openai"}

    def test_load_config_profiles_dict_parsing(self, tmp_path) -> None:
        from autoskillit.config import load_config

        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        config_data = {
            "providers": {
                "profiles": {
                    "fast": {"model": "gpt-4o-mini", "api_base": "https://api.openai.com"},
                }
            }
        }
        (config_dir / "config.yaml").write_text(yaml.dump(config_data))
        cfg = load_config(tmp_path)
        assert cfg.providers.profiles == {
            "fast": {"model": "gpt-4o-mini", "api_base": "https://api.openai.com"},
        }

    def test_load_config_provider_retry_limit_override(self, tmp_path) -> None:
        from autoskillit.config import load_config

        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("providers:\n  provider_retry_limit: 5\n")
        cfg = load_config(tmp_path)
        assert cfg.providers.provider_retry_limit == 5
