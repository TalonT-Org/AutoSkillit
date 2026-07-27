from __future__ import annotations

import pytest
import yaml

pytestmark = [pytest.mark.layer("config"), pytest.mark.small]


class TestAgentBackendConfigImports:
    def test_agent_backend_config_importable_from_settings(self) -> None:
        from autoskillit.config import AgentBackendConfig
        from autoskillit.config.settings import AgentBackendConfig as ABC

        assert ABC is AgentBackendConfig

    def test_agent_backend_config_in_settings_all(self) -> None:
        import autoskillit.config.settings as m

        assert "AgentBackendConfig" in m.__all__

    def test_agent_backend_config_in_config_all(self) -> None:
        import autoskillit.config as m

        assert "AgentBackendConfig" in m.__all__


class TestAgentBackendConfigField:
    def test_automation_config_has_agent_backend_field(self) -> None:
        from dataclasses import fields as _dc_fields

        from autoskillit.config import AgentBackendConfig, AutomationConfig

        field_names = [f.name for f in _dc_fields(AutomationConfig)]
        assert "agent_backend" in field_names
        cfg = AutomationConfig()
        assert isinstance(cfg.agent_backend, AgentBackendConfig)

    def test_agent_backend_field_ordering(self) -> None:
        from dataclasses import fields as _dc_fields

        from autoskillit.config import AutomationConfig

        field_names = [f.name for f in _dc_fields(AutomationConfig)]
        providers_idx = field_names.index("providers")
        agent_backend_idx = field_names.index("agent_backend")
        features_idx = field_names.index("features")
        assert providers_idx < agent_backend_idx < features_idx

    def test_agent_backend_config_defaults(self) -> None:
        from autoskillit.config.settings import AgentBackendConfig

        cfg = AgentBackendConfig()
        assert cfg.backend == "claude-code"

    def test_agent_backend_config_custom_value(self) -> None:
        from autoskillit.config.settings import AgentBackendConfig

        cfg = AgentBackendConfig(backend="codex")
        assert cfg.backend == "codex"

    def test_agent_backend_config_warns_on_unknown_backend(self) -> None:
        import structlog.testing

        from autoskillit.config.settings import AgentBackendConfig

        with structlog.testing.capture_logs() as cap_logs:
            cfg = AgentBackendConfig(backend="aider")
        assert cfg.backend == "aider"
        unknown_backend_events = [
            e
            for e in cap_logs
            if e.get("log_level") == "warning"
            and e.get("event") == "unknown_backend"
            and e.get("backend") == "aider"
        ]
        assert len(unknown_backend_events) == 1, (
            f"Expected exactly one unknown_backend warning for 'aider', got: {cap_logs}"
        )


class TestAgentBackendConfigLoading:
    def test_load_config_agent_backend_defaults(self, tmp_path) -> None:
        from autoskillit.config import load_config

        cfg = load_config(tmp_path)
        assert cfg.agent_backend.backend == "claude-code"

    def test_defaults_yaml_has_agent_backend_section(self) -> None:
        from autoskillit.core.io import load_yaml
        from autoskillit.core.paths import pkg_root

        defaults = load_yaml(pkg_root() / "config" / "defaults.yaml")
        assert "agent_backend" in defaults
        assert defaults["agent_backend"]["backend"] == "claude-code"

    def test_agent_backend_env_var_override(self, tmp_path, monkeypatch) -> None:
        from autoskillit.config import load_config

        monkeypatch.delenv("AUTOSKILLIT_AGENT_BACKEND", raising=False)
        monkeypatch.setenv("AUTOSKILLIT_AGENT_BACKEND__BACKEND", "codex")
        cfg = load_config(tmp_path)
        assert cfg.agent_backend.backend == "codex"

    def test_agent_backend_yaml_override(self, tmp_path, monkeypatch) -> None:
        from autoskillit.config import load_config

        monkeypatch.delenv("AUTOSKILLIT_AGENT_BACKEND", raising=False)
        monkeypatch.delenv("AUTOSKILLIT_AGENT_BACKEND__BACKEND", raising=False)
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text(yaml.dump({"agent_backend": {"backend": "aider"}}))
        cfg = load_config(tmp_path)
        assert cfg.agent_backend.backend == "aider"

    def test_agent_backend_key_accepted_by_schema_validator(self, tmp_path) -> None:
        from autoskillit.config import load_config

        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text(
            yaml.dump({"agent_backend": {"backend": "claude-code"}})
        )
        cfg = load_config(tmp_path)
        assert cfg.agent_backend.backend == "claude-code"

    def test_agent_backend_unknown_key_rejected(self, tmp_path) -> None:
        from autoskillit.config import load_config
        from autoskillit.config.settings import ConfigSchemaError

        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("agent_backend:\n  invented_key: whatever\n")
        with pytest.raises(ConfigSchemaError, match="unrecognized key"):
            load_config(tmp_path)


class TestAgentBackendConfigOverrides:
    def test_recipe_overrides_field_exists(self) -> None:
        from autoskillit.config.settings import AgentBackendConfig

        cfg = AgentBackendConfig()
        assert hasattr(cfg, "recipe_overrides")
        assert cfg.recipe_overrides == {}

    def test_step_overrides_field_exists(self) -> None:
        from autoskillit.config.settings import AgentBackendConfig

        cfg = AgentBackendConfig()
        assert hasattr(cfg, "step_overrides")
        assert cfg.step_overrides == {}

    def test_step_overrides_type_validation(self) -> None:
        from autoskillit.config.settings import AgentBackendConfig

        with pytest.raises(ValueError, match="step_overrides"):
            AgentBackendConfig(step_overrides={"foo": 123})  # type: ignore[dict-item]

    def test_recipe_overrides_type_validation(self) -> None:
        from autoskillit.config.settings import AgentBackendConfig

        with pytest.raises(ValueError, match="recipe_overrides"):
            AgentBackendConfig(recipe_overrides={"remediation": "codex"})  # type: ignore[dict-item]

    def test_recipe_overrides_inner_value_validation(self) -> None:
        from autoskillit.config.settings import AgentBackendConfig

        with pytest.raises(ValueError, match="recipe_overrides"):
            AgentBackendConfig(recipe_overrides={"remediation": {"foo": 123}})  # type: ignore[dict-item]

    def test_recipe_overrides_none_entry_coerced_to_empty_dict(self) -> None:
        from autoskillit.config.settings import AgentBackendConfig

        cfg = AgentBackendConfig(recipe_overrides={"remediation": None})  # type: ignore[dict-item]
        assert cfg.recipe_overrides == {"remediation": {}}

    def test_unknown_backend_in_recipe_overrides_warns(self) -> None:
        import structlog.testing

        from autoskillit.config.settings import AgentBackendConfig

        with structlog.testing.capture_logs() as cap_logs:
            AgentBackendConfig(recipe_overrides={"remediation": {"dry_walkthrough": "aider"}})
        events = [
            e
            for e in cap_logs
            if e.get("log_level") == "warning"
            and e.get("event") == "recipe_override_references_unknown_backend"
            and e.get("recipe") == "remediation"
            and e.get("step") == "dry_walkthrough"
            and e.get("backend") == "aider"
        ]
        assert len(events) == 1, f"Expected one unknown-backend warning, got: {cap_logs}"

    def test_unknown_backend_in_step_overrides_warns(self) -> None:
        import structlog.testing

        from autoskillit.config.settings import AgentBackendConfig

        with structlog.testing.capture_logs() as cap_logs:
            AgentBackendConfig(step_overrides={"dry_walkthrough": "aider"})
        events = [
            e
            for e in cap_logs
            if e.get("log_level") == "warning"
            and e.get("event") == "step_override_references_unknown_backend"
            and e.get("step") == "dry_walkthrough"
            and e.get("backend") == "aider"
        ]
        assert len(events) == 1, f"Expected one unknown-backend warning, got: {cap_logs}"

    def test_yaml_loading_with_recipe_overrides(self, tmp_path, monkeypatch) -> None:
        from autoskillit.config import load_config

        monkeypatch.delenv("AUTOSKILLIT_AGENT_BACKEND", raising=False)
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text(
            yaml.dump(
                {
                    "agent_backend": {
                        "backend": "codex",
                        "recipe_overrides": {"remediation": {"dry_walkthrough": "codex"}},
                        "step_overrides": {"implement": "claude-code"},
                    }
                }
            )
        )
        cfg = load_config(tmp_path)
        assert cfg.agent_backend.backend == "codex"
        assert cfg.agent_backend.recipe_overrides == {"remediation": {"dry_walkthrough": "codex"}}
        assert cfg.agent_backend.step_overrides == {"implement": "claude-code"}

    def test_string_shorthand_still_works(self) -> None:
        from autoskillit.config.settings import AgentBackendConfig

        cfg = AgentBackendConfig(backend="codex")
        assert cfg.backend == "codex"
        assert cfg.recipe_overrides == {}
        assert cfg.step_overrides == {}
