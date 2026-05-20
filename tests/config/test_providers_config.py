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

    def test_from_dynaconf_providers_defaults(self, tmp_path) -> None:
        from autoskillit.config import load_config

        cfg = load_config(tmp_path)
        assert cfg.providers.default_provider is None
        assert "anthropic" in cfg.providers.profiles
        assert cfg.providers.profiles["anthropic"] == {
            "base_url": None,
            "timeout_seconds": None,
            "api_key_env": None,
            "context_window": None,
        }
        assert cfg.providers.step_overrides == {}
        assert cfg.providers.recipe_overrides == {}
        assert cfg.providers.provider_retry_limit == 2

    def test_providers_config_defaults(self) -> None:
        from autoskillit.config.settings import ProvidersConfig

        cfg = ProvidersConfig()
        assert cfg.default_provider is None
        assert cfg.profiles == {}
        assert cfg.step_overrides == {}
        assert cfg.recipe_overrides == {}
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
            "recipe_overrides",
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

        with pytest.raises(ValueError, match=r"profiles\[.+\]\[.+\] must be a string or null"):
            ProvidersConfig(profiles={"my_profile": {"model": 42}})  # type: ignore[arg-type]

    def test_recipe_overrides_non_string_value_raises(self) -> None:
        from autoskillit.config.settings import ProvidersConfig

        with pytest.raises(ValueError, match=r"recipe_overrides\[.+\]\[.+\] must be a string"):
            ProvidersConfig(recipe_overrides={"remediation": {"implement": 42}})  # type: ignore[arg-type]

    def test_recipe_overrides_non_dict_inner_value_raises(self) -> None:
        from autoskillit.config.settings import ProvidersConfig

        with pytest.raises(ValueError, match=r"recipe_overrides\[.+\] must be a dict"):
            ProvidersConfig(recipe_overrides={"remediation": "not_a_dict"})  # type: ignore[arg-type]

    def test_providers_config_profiles_none_value_accepted(self) -> None:
        from autoskillit.config.settings import ProvidersConfig

        cfg = ProvidersConfig(profiles={"sentinel": {"base_url": None, "api_key_env": None}})  # type: ignore[arg-type]
        assert cfg.profiles["sentinel"]["base_url"] is None
        assert cfg.profiles["sentinel"]["api_key_env"] is None


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
        # Defaults (anthropic sentinel) are merged with user-provided profiles
        assert cfg.providers.profiles["fast"] == {
            "model": "gpt-4o-mini",
            "api_base": "https://api.openai.com",
        }
        assert "anthropic" in cfg.providers.profiles

    def test_load_config_provider_retry_limit_override(self, tmp_path) -> None:
        from autoskillit.config import load_config

        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("providers:\n  provider_retry_limit: 5\n")
        cfg = load_config(tmp_path)
        assert cfg.providers.provider_retry_limit == 5

    def test_load_config_recipe_overrides_parsing(self, tmp_path) -> None:
        from autoskillit.config import load_config

        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        config_data = {
            "providers": {"recipe_overrides": {"remediation": {"implement": "anthropic"}}}
        }
        (config_dir / "config.yaml").write_text(yaml.dump(config_data))
        cfg = load_config(tmp_path)
        assert cfg.providers.recipe_overrides == {"remediation": {"implement": "anthropic"}}

    def test_defaults_yaml_anthropic_sentinel_profile(self) -> None:
        from autoskillit.core.io import load_yaml
        from autoskillit.core.paths import pkg_root

        defaults = load_yaml(pkg_root() / "config" / "defaults.yaml")
        profile = defaults["providers"]["profiles"]["anthropic"]
        assert set(profile.keys()) == {
            "base_url",
            "timeout_seconds",
            "api_key_env",
            "context_window",
        }
        assert all(v is None for v in profile.values())

    def test_load_config_recipe_overrides_merges_across_layers(
        self, tmp_path, monkeypatch
    ) -> None:
        from autoskillit.config import load_config

        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
        user_config_dir = tmp_path / "home" / ".autoskillit"
        user_config_dir.mkdir(parents=True)
        project_config_dir = tmp_path / ".autoskillit"
        project_config_dir.mkdir()
        (user_config_dir / "config.yaml").write_text(
            yaml.dump(
                {"providers": {"recipe_overrides": {"remediation": {"implement": "anthropic"}}}}
            )
        )
        (project_config_dir / "config.yaml").write_text(
            yaml.dump(
                {"providers": {"recipe_overrides": {"implementation": {"implement": "minimax"}}}}
            )
        )
        cfg = load_config(tmp_path)
        assert cfg.providers.recipe_overrides == {
            "remediation": {"implement": "anthropic"},
            "implementation": {"implement": "minimax"},
        }


class TestResolvedProfiles:
    def test_resolved_profiles_empty(self) -> None:
        from autoskillit.config.settings import ProvidersConfig

        cfg = ProvidersConfig()
        assert cfg.resolved_profiles == {}

    def test_resolved_profiles_typed_fields(self) -> None:
        from autoskillit.config._config_dataclasses import ProviderProfileDef
        from autoskillit.config.settings import ProvidersConfig

        cfg = ProvidersConfig(
            profiles={
                "openai": {
                    "base_url": "https://api.openai.com",
                    "api_key_env": "OPENAI_API_KEY",
                }
            }
        )
        result = cfg.resolved_profiles
        assert "openai" in result
        profile = result["openai"]
        assert isinstance(profile, ProviderProfileDef)
        assert profile.base_url == "https://api.openai.com"
        assert profile.api_key_env == "OPENAI_API_KEY"
        assert profile.timeout_seconds is None
        assert profile.context_window is None
        assert profile.raw_env == {}

    def test_resolved_profiles_int_coercion_timeout(self) -> None:
        from autoskillit.config.settings import ProvidersConfig

        cfg = ProvidersConfig(profiles={"fast": {"timeout_seconds": "30"}})
        assert cfg.resolved_profiles["fast"].timeout_seconds == 30

    def test_resolved_profiles_int_coercion_context_window(self) -> None:
        from autoskillit.config.settings import ProvidersConfig

        cfg = ProvidersConfig(profiles={"large": {"context_window": "128000"}})
        assert cfg.resolved_profiles["large"].context_window == 128000

    def test_resolved_profiles_empty_string_numeric_is_none(self) -> None:
        from autoskillit.config.settings import ProvidersConfig

        cfg = ProvidersConfig(profiles={"test": {"timeout_seconds": ""}})
        assert cfg.resolved_profiles["test"].timeout_seconds is None

    def test_resolved_profiles_extra_keys_in_raw_env(self) -> None:
        from autoskillit.config.settings import ProvidersConfig

        cfg = ProvidersConfig(
            profiles={
                "test": {
                    "base_url": "https://example.com",
                    "model": "gpt-4",
                    "extra_flag": "true",
                }
            }
        )
        raw = cfg.resolved_profiles["test"].raw_env
        assert raw == {"model": "gpt-4", "extra_flag": "true"}

    def test_resolved_profiles_no_mutation(self) -> None:
        from autoskillit.config.settings import ProvidersConfig

        cfg = ProvidersConfig(profiles={"test": {"model": "gpt-4"}})
        cfg.resolved_profiles
        assert cfg.profiles == {"test": {"model": "gpt-4"}}

    def test_resolved_profiles_multiple_profiles(self) -> None:
        from autoskillit.config.settings import ProvidersConfig

        cfg = ProvidersConfig(
            profiles={
                "fast": {"timeout_seconds": "10", "model": "gpt-4o-mini"},
                "large": {"context_window": "200000", "model": "gpt-4o"},
            }
        )
        result = cfg.resolved_profiles
        assert len(result) == 2
        assert result["fast"].timeout_seconds == 10
        assert result["fast"].raw_env == {"model": "gpt-4o-mini"}
        assert result["large"].context_window == 200000
        assert result["large"].raw_env == {"model": "gpt-4o"}

    def test_resolved_profiles_null_sentinel(self) -> None:
        from autoskillit.config._config_dataclasses import ProviderProfileDef
        from autoskillit.config.settings import ProvidersConfig

        cfg = ProvidersConfig(
            profiles={
                "anthropic": {
                    "base_url": None,
                    "timeout_seconds": None,
                    "api_key_env": None,
                    "context_window": None,
                }
            }  # type: ignore[arg-type]
        )
        result = cfg.resolved_profiles
        assert "anthropic" in result
        profile = result["anthropic"]
        assert isinstance(profile, ProviderProfileDef)
        assert profile.base_url is None
        assert profile.timeout_seconds is None
        assert profile.api_key_env is None
        assert profile.context_window is None
        assert profile.raw_env == {}
