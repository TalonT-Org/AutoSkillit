"""Tests for the retired config key registry and remap helper (ticket #4303).

Covers T4-T10: retired keys healing at user/project layers, warning emission,
precedence/split semantics, unit tests for remap_retired_keys, and golden
fixture loading of real prior-version config.yaml shapes.
"""

from __future__ import annotations

import shutil
import types
from pathlib import Path

import pytest
import structlog.testing
import yaml

from autoskillit.config import load_config
from autoskillit.config.settings import ConfigSchemaError, RetiredConfigKeyDef, remap_retired_keys

pytestmark = [pytest.mark.layer("config"), pytest.mark.small]

_GOLDEN_DIR = Path(__file__).parent / "golden_configs"


class TestRetiredKeyHealsAtUserLayer:
    def test_user_global_retired_key_heals(self, tmp_path, monkeypatch):
        """T4: A retired key in ~/.autoskillit/config.yaml heals without raising."""
        user_home = tmp_path / "home"
        user_config_dir = user_home / ".autoskillit"
        user_config_dir.mkdir(parents=True)
        (user_config_dir / "config.yaml").write_text(
            yaml.dump({"diagnostics": {"post_run_analysis": True}})
        )
        monkeypatch.setattr("pathlib.Path.home", lambda: user_home)

        project_dir = tmp_path / "project"
        project_dir.mkdir()

        cfg = load_config(project_dir)
        assert cfg.diagnostics.pipeline_health is True


class TestRetiredKeyHealsAtProjectLayer:
    def test_project_retired_key_heals(self, tmp_path, monkeypatch):
        """T5: A retired key in project .autoskillit/config.yaml heals without raising.

        Proves fleet-spawned clones/worktrees (which load project config through the
        same loader) are covered too.
        """
        user_home = tmp_path / "home"
        user_home.mkdir()
        monkeypatch.setattr("pathlib.Path.home", lambda: user_home)

        project_dir = tmp_path / "project"
        project_config_dir = project_dir / ".autoskillit"
        project_config_dir.mkdir(parents=True)
        (project_config_dir / "config.yaml").write_text(
            yaml.dump({"diagnostics": {"post_run_analysis": True}})
        )

        cfg = load_config(project_dir)
        assert cfg.diagnostics.pipeline_health is True


class TestRetiredKeyWarningEmission:
    def test_warning_emitted_with_old_and_new_key(self, tmp_path, monkeypatch):
        """T6: retired_config_key warning is emitted with retired/replacement/log_level."""
        user_home = tmp_path / "home"
        user_home.mkdir()
        monkeypatch.setattr("pathlib.Path.home", lambda: user_home)

        project_dir = tmp_path / "project"
        project_config_dir = project_dir / ".autoskillit"
        project_config_dir.mkdir(parents=True)
        (project_config_dir / "config.yaml").write_text(
            yaml.dump({"diagnostics": {"post_run_analysis": True}})
        )

        with structlog.testing.capture_logs() as cap_logs:
            cfg = load_config(project_dir)

        assert cfg.diagnostics.pipeline_health is True
        matching = [e for e in cap_logs if e.get("event") == "retired_config_key"]
        assert len(matching) == 1, (
            f"Expected exactly one retired_config_key event, got: {cap_logs}"
        )
        record = matching[0]
        assert record.get("retired") == "diagnostics.post_run_analysis"
        assert record.get("replacement") == "diagnostics.pipeline_health"
        assert record.get("log_level") == "warning"

    def test_warning_guidance_mentions_both_split_keys(self, tmp_path, monkeypatch):
        """T6: quota_guard.threshold's warning guidance mentions both successor fields."""
        user_home = tmp_path / "home"
        user_home.mkdir()
        monkeypatch.setattr("pathlib.Path.home", lambda: user_home)

        project_dir = tmp_path / "project"
        project_config_dir = project_dir / ".autoskillit"
        project_config_dir.mkdir(parents=True)
        (project_config_dir / "config.yaml").write_text(
            yaml.dump({"quota_guard": {"threshold": 90.0}})
        )

        with structlog.testing.capture_logs() as cap_logs:
            load_config(project_dir)

        matching = [e for e in cap_logs if e.get("event") == "retired_config_key"]
        assert len(matching) == 1
        guidance = matching[0].get("guidance", "")
        assert "short_window_threshold" in guidance
        assert "long_window_threshold" in guidance


class TestPrecedenceAndSplitSemantics:
    def test_explicit_new_key_wins_over_retired_value(self, tmp_path, monkeypatch):
        """T7a: When both old and new keys are set, the explicit new key wins and
        value_carried is False in the emitted warning."""
        user_home = tmp_path / "home"
        user_home.mkdir()
        monkeypatch.setattr("pathlib.Path.home", lambda: user_home)

        project_dir = tmp_path / "project"
        project_config_dir = project_dir / ".autoskillit"
        project_config_dir.mkdir(parents=True)
        (project_config_dir / "config.yaml").write_text(
            yaml.dump(
                {
                    "quota_guard": {
                        "threshold": 90.0,
                        "short_window_threshold": 70.0,
                    }
                }
            )
        )

        with structlog.testing.capture_logs() as cap_logs:
            cfg = load_config(project_dir)

        assert cfg.quota_guard.short_window_threshold == 70.0
        matching = [e for e in cap_logs if e.get("event") == "retired_config_key"]
        assert len(matching) == 1
        assert matching[0].get("value_carried") is False

    def test_split_scoping_only_touches_short_window(self, tmp_path, monkeypatch):
        """T7b: quota_guard.threshold (with no explicit short_window_threshold) carries
        onto short_window_threshold only; long_window_threshold keeps its default."""
        user_home = tmp_path / "home"
        user_home.mkdir()
        monkeypatch.setattr("pathlib.Path.home", lambda: user_home)

        project_dir = tmp_path / "project"
        project_config_dir = project_dir / ".autoskillit"
        project_config_dir.mkdir(parents=True)
        (project_config_dir / "config.yaml").write_text(
            yaml.dump({"quota_guard": {"threshold": 90.0}})
        )

        cfg = load_config(project_dir)
        assert cfg.quota_guard.short_window_threshold == 90.0
        assert cfg.quota_guard.long_window_threshold == 95.0

    def test_cross_layer_precedence_unchanged(self, tmp_path, monkeypatch):
        """T7c: Per-layer remap does not disturb normal dynaconf merge order —
        project layer's explicit successor value still wins over user layer's
        retired value."""
        user_home = tmp_path / "home"
        user_config_dir = user_home / ".autoskillit"
        user_config_dir.mkdir(parents=True)
        (user_config_dir / "config.yaml").write_text(
            yaml.dump({"diagnostics": {"post_run_analysis": True}})
        )
        monkeypatch.setattr("pathlib.Path.home", lambda: user_home)

        project_dir = tmp_path / "project"
        project_config_dir = project_dir / ".autoskillit"
        project_config_dir.mkdir(parents=True)
        (project_config_dir / "config.yaml").write_text(
            yaml.dump({"diagnostics": {"pipeline_health": False}})
        )

        cfg = load_config(project_dir)
        assert cfg.diagnostics.pipeline_health is False

    def test_features_franchise_heals_through_both_validators(self, tmp_path, monkeypatch):
        """T7d: features.franchise heals into features.fleet, proving the remap runs
        upstream of both validate_layer_keys's features special-case AND
        AutomationConfig._build_features_dict."""
        user_home = tmp_path / "home"
        user_home.mkdir()
        monkeypatch.setattr("pathlib.Path.home", lambda: user_home)

        project_dir = tmp_path / "project"
        project_config_dir = project_dir / ".autoskillit"
        project_config_dir.mkdir(parents=True)
        (project_config_dir / "config.yaml").write_text("features:\n  franchise: true\n")

        cfg = load_config(project_dir)
        assert cfg.features["fleet"] is True


class TestRetiredKeyInSecretsLayerStillRaises:
    def test_retired_key_in_secrets_yaml_still_raises(self, tmp_path, monkeypatch):
        """T8b: secrets layers are never remapped — a retired key placed in
        .secrets.yaml still raises ConfigSchemaError (unrecognized key), proving
        the remap's is_secrets_layer gate is honoured end-to-end via load_config,
        not just by the remap_retired_keys unit in isolation."""
        user_home = tmp_path / "home"
        user_home.mkdir()
        monkeypatch.setattr("pathlib.Path.home", lambda: user_home)

        project_dir = tmp_path / "project"
        project_config_dir = project_dir / ".autoskillit"
        project_config_dir.mkdir(parents=True)
        (project_config_dir / ".secrets.yaml").write_text(
            yaml.dump({"diagnostics": {"post_run_analysis": True}})
        )

        with pytest.raises(ConfigSchemaError, match="unrecognized key"):
            load_config(project_dir)


class TestRemapRetiredKeysUnit:
    """Unit tests for remap_retired_keys itself."""

    def test_identity_fast_path_when_nothing_matches(self):
        layer_dict = {"github": {"default_repo": "owner/repo"}}
        result, records = remap_retired_keys(layer_dict, is_secrets_layer=False)
        assert result is layer_dict
        assert records == []

    def test_does_not_mutate_input_dict(self):
        layer_dict = {"diagnostics": {"post_run_analysis": True}}
        result, records = remap_retired_keys(layer_dict, is_secrets_layer=False)
        assert records != []
        # Original dict is untouched — no in-place mutation.
        assert layer_dict == {"diagnostics": {"post_run_analysis": True}}
        assert result is not layer_dict

    def test_secrets_layer_returns_identity_regardless_of_content(self):
        layer_dict = {"diagnostics": {"post_run_analysis": True}}
        result, records = remap_retired_keys(layer_dict, is_secrets_layer=True)
        assert result is layer_dict
        assert records == []

    def test_section_present_but_not_a_dict_is_ignored(self):
        layer_dict = {"diagnostics": None}
        result, records = remap_retired_keys(layer_dict, is_secrets_layer=False)
        assert result is layer_dict
        assert records == []

    def test_two_retired_keys_in_same_section_remap_independently(self, monkeypatch):
        import autoskillit.config.settings as settings_mod

        synthetic_registry = types.MappingProxyType(
            {
                ("diagnostics", "old_a"): RetiredConfigKeyDef(
                    new_key="new_a", retired_in="0.0.1", note="synthetic a"
                ),
                ("diagnostics", "old_b"): RetiredConfigKeyDef(
                    new_key="new_b", retired_in="0.0.1", note="synthetic b"
                ),
            }
        )
        monkeypatch.setattr(settings_mod, "RETIRED_CONFIG_KEYS", synthetic_registry)

        layer_dict = {"diagnostics": {"old_a": 1, "old_b": 2}}
        result, records = settings_mod.remap_retired_keys(layer_dict, is_secrets_layer=False)

        assert result["diagnostics"] == {"new_a": 1, "new_b": 2}
        assert len(records) == 2
        by_old_key = {r.old_key: r for r in records}
        assert by_old_key["old_a"].new_key == "new_a"
        assert by_old_key["old_b"].new_key == "new_b"

    def test_record_order_is_sorted_by_section_and_old_key(self, monkeypatch):
        import autoskillit.config.settings as settings_mod

        # Insertion order deliberately reversed from sorted (section, old_key) order.
        synthetic_registry = types.MappingProxyType(
            {
                ("diagnostics", "zzz_key"): RetiredConfigKeyDef(
                    new_key="new_zzz", retired_in="0.0.1", note="synthetic zzz"
                ),
                ("diagnostics", "aaa_key"): RetiredConfigKeyDef(
                    new_key="new_aaa", retired_in="0.0.1", note="synthetic aaa"
                ),
            }
        )
        monkeypatch.setattr(settings_mod, "RETIRED_CONFIG_KEYS", synthetic_registry)

        layer_dict = {"diagnostics": {"zzz_key": 1, "aaa_key": 2}}
        _, records = settings_mod.remap_retired_keys(layer_dict, is_secrets_layer=False)

        assert [r.old_key for r in records] == ["aaa_key", "zzz_key"]


class TestGoldenFixtureLoader:
    """T10 (G5): real prior-version config.yaml shapes load byte-for-byte."""

    @pytest.mark.parametrize(
        "fixture_name,assertions",
        [
            (
                "pre_0_10_885_diagnostics.yaml",
                lambda cfg: cfg.diagnostics.pipeline_health is True,
            ),
            (
                "pre_0_8_39_quota_guard.yaml",
                lambda cfg: (
                    cfg.quota_guard.short_window_threshold == 90.0
                    and cfg.quota_guard.long_window_threshold == 95.0
                    and cfg.quota_guard.enabled is True
                ),
            ),
            (
                "pre_0_9_135_features.yaml",
                lambda cfg: cfg.features["fleet"] is True,
            ),
        ],
    )
    def test_golden_fixture_heals_at_user_layer(
        self, tmp_path, monkeypatch, fixture_name, assertions
    ):
        fixture_path = _GOLDEN_DIR / fixture_name
        user_home = tmp_path / "home"
        user_config_dir = user_home / ".autoskillit"
        user_config_dir.mkdir(parents=True)
        shutil.copy(fixture_path, user_config_dir / "config.yaml")
        monkeypatch.setattr("pathlib.Path.home", lambda: user_home)

        project_dir = tmp_path / "project"
        project_dir.mkdir()

        cfg = load_config(project_dir)
        assert assertions(cfg)

    @pytest.mark.parametrize(
        "fixture_name,assertions",
        [
            (
                "pre_0_10_885_diagnostics.yaml",
                lambda cfg: cfg.diagnostics.pipeline_health is True,
            ),
            (
                "pre_0_8_39_quota_guard.yaml",
                lambda cfg: (
                    cfg.quota_guard.short_window_threshold == 90.0
                    and cfg.quota_guard.long_window_threshold == 95.0
                    and cfg.quota_guard.enabled is True
                ),
            ),
            (
                "pre_0_9_135_features.yaml",
                lambda cfg: cfg.features["fleet"] is True,
            ),
        ],
    )
    def test_golden_fixture_heals_at_project_layer(
        self, tmp_path, monkeypatch, fixture_name, assertions
    ):
        fixture_path = _GOLDEN_DIR / fixture_name
        user_home = tmp_path / "home"
        user_home.mkdir()
        monkeypatch.setattr("pathlib.Path.home", lambda: user_home)

        project_dir = tmp_path / "project"
        project_config_dir = project_dir / ".autoskillit"
        project_config_dir.mkdir(parents=True)
        shutil.copy(fixture_path, project_config_dir / "config.yaml")

        cfg = load_config(project_dir)
        assert assertions(cfg)
