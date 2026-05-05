"""Tests for QuotaGuardConfig loading and validation."""

import pytest
import yaml

from autoskillit.config import AutomationConfig, load_config

pytestmark = [pytest.mark.layer("config"), pytest.mark.small]


class TestQuotaGuardConfig:
    def test_default_enabled(self):
        config = AutomationConfig()
        assert config.quota_guard.enabled is True
        assert config.quota_guard.short_window_threshold == pytest.approx(85.0)
        assert config.quota_guard.long_window_threshold == pytest.approx(95.0)
        assert config.quota_guard.buffer_seconds == 60
        assert config.quota_guard.cache_max_age == 300

    def test_load_quota_guard_from_yaml(self, tmp_path):
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text(
            yaml.dump(
                {
                    "quota_guard": {
                        "enabled": True,
                        "short_window_threshold": 85.0,
                        "long_window_threshold": 95.0,
                    }
                }
            )
        )
        config = load_config(tmp_path)
        assert config.quota_guard.enabled is True
        assert config.quota_guard.short_window_threshold == pytest.approx(85.0)
        assert config.quota_guard.long_window_threshold == pytest.approx(95.0)
        # Unspecified fields keep defaults
        assert config.quota_guard.buffer_seconds == 60

    def test_short_window_threshold_defaults_to_85(self):
        from autoskillit.config.settings import QuotaGuardConfig

        assert QuotaGuardConfig().short_window_threshold == 85.0

    def test_long_window_threshold_defaults_to_95(self):
        from autoskillit.config.settings import QuotaGuardConfig

        assert QuotaGuardConfig().long_window_threshold == 95.0

    def test_long_window_patterns_default(self):
        from autoskillit.config.settings import QuotaGuardConfig

        defaults = QuotaGuardConfig().long_window_patterns
        assert "seven_day" in defaults, (
            f"Default long_window_patterns {defaults!r} must include 'seven_day' "
            "(the actual Anthropic API key for the 7-day budget)"
        )

    def test_threshold_field_removed(self):
        import dataclasses

        from autoskillit.config.settings import QuotaGuardConfig

        names = {f.name for f in dataclasses.fields(QuotaGuardConfig)}
        assert "threshold" not in names

    def test_quota_guard_yaml_round_trip_per_window(self, tmp_path):
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text(
            yaml.dump(
                {
                    "quota_guard": {
                        "short_window_threshold": 85.0,
                        "long_window_threshold": 95.0,
                        "long_window_patterns": ["weekly", "sonnet", "opus"],
                    }
                }
            )
        )
        config = load_config(tmp_path)
        assert config.quota_guard.short_window_threshold == pytest.approx(85.0)
        assert config.quota_guard.long_window_threshold == pytest.approx(95.0)
        assert config.quota_guard.long_window_patterns == ["weekly", "sonnet", "opus"]

    def test_quota_guard_config_has_cache_refresh_interval(self):
        """QG_C3: QuotaGuardConfig has cache_refresh_interval defaulting to 240."""
        from autoskillit.config.settings import QuotaGuardConfig

        config = QuotaGuardConfig()
        assert config.cache_refresh_interval == 240

    def test_defaults_yaml_has_cache_refresh_interval(self):
        """QG_C4: defaults.yaml defines quota_guard.cache_refresh_interval < cache_max_age."""
        from autoskillit.core.paths import pkg_root

        defaults = yaml.safe_load((pkg_root() / "config" / "defaults.yaml").read_text())
        assert "cache_refresh_interval" in defaults["quota_guard"]
        interval = defaults["quota_guard"]["cache_refresh_interval"]
        max_age = defaults["quota_guard"]["cache_max_age"]
        assert interval < max_age, (
            f"cache_refresh_interval ({interval}) must be < cache_max_age ({max_age}); "
            "otherwise the loop arrives after the cache has already expired"
        )

    def test_quota_guard_per_window_enabled_defaults_true(self):
        """Test 14: QuotaGuardConfig() defaults both per-window flags to True."""
        from autoskillit.config.settings import QuotaGuardConfig

        config = QuotaGuardConfig()
        assert config.short_window_enabled is True
        assert config.long_window_enabled is True

    def test_quota_guard_per_window_enabled_yaml_round_trip(self, tmp_path):
        """Test 15: per-window enabled flags survive a YAML round-trip."""
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text(
            yaml.dump({"quota_guard": {"short_window_enabled": False}})
        )
        config = load_config(tmp_path)
        assert config.quota_guard.short_window_enabled is False
        assert config.quota_guard.long_window_enabled is True

    def test_defaults_yaml_has_per_window_enabled_keys(self):
        """Test 16: defaults.yaml has both per-window enabled keys set to True."""
        from autoskillit.core.paths import pkg_root

        defaults = yaml.safe_load((pkg_root() / "config" / "defaults.yaml").read_text())
        assert defaults["quota_guard"]["short_window_enabled"] is True
        assert defaults["quota_guard"]["long_window_enabled"] is True

    def test_quota_guard_env_var_override_short_window_enabled(self, monkeypatch, tmp_path):
        """Test 17: AUTOSKILLIT_QUOTA_GUARD__SHORT_WINDOW_ENABLED=false is cast to bool False."""
        monkeypatch.setenv("AUTOSKILLIT_QUOTA_GUARD__SHORT_WINDOW_ENABLED", "false")
        config = load_config(tmp_path)
        assert config.quota_guard.short_window_enabled is False
