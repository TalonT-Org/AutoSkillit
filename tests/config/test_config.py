"""Tests for configuration loading and resolution."""

from dataclasses import fields as dc_fields

import pytest
import yaml

from autoskillit.config import AutomationConfig, ConfigSchemaError, RunSkillConfig, load_config


class TestDefaultConfig:
    def test_default_config_matches_current_constants(self):
        """C1: AutomationConfig() defaults reproduce current hardcoded values."""
        cfg = AutomationConfig()
        assert cfg.test_check.command == ["task", "test-check"]
        assert cfg.test_check.timeout == 600
        assert cfg.classify_fix.path_prefixes == []
        assert cfg.reset_workspace.command is None
        assert cfg.reset_workspace.preserve_dirs == set()
        assert cfg.implement_gate.marker == "Dry-walkthrough verified = TRUE"
        assert cfg.implement_gate.skill_names == {
            "/autoskillit:implement-worktree",
            "/autoskillit:implement-worktree-no-merge",
        }
        assert cfg.safety.reset_guard_marker == ".autoskillit-workspace"
        assert cfg.safety.require_dry_walkthrough is True
        assert cfg.safety.test_gate_on_merge is True
        assert isinstance(cfg.safety.protected_branches, list)
        assert "main" in cfg.safety.protected_branches
        assert "integration" in cfg.safety.protected_branches
        assert "stable" in cfg.safety.protected_branches
        assert cfg.worktree_setup.command is None

    def test_default_model_config(self):
        """MOD_C1: ModelConfig.default is 'sonnet'; override defaults to None."""
        cfg = AutomationConfig()
        assert cfg.model.default == "sonnet"
        assert cfg.model.override is None


class TestLoadConfig:
    def test_load_config_no_files_returns_defaults(self, tmp_path):
        """C2: No YAML files on disk -> defaults returned."""
        cfg = load_config(tmp_path)
        assert cfg.test_check.command == ["task", "test-check"]
        assert cfg.reset_workspace.command is None

    def test_load_yaml_full_config(self, tmp_path):
        """C3: YAML with all fields -> all fields populated correctly."""
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        config_data = {
            "test_check": {"command": ["pytest", "-v"], "timeout": 300},
            "classify_fix": {"path_prefixes": ["src/core/", "tests/core/"]},
            "reset_workspace": {
                "command": ["make", "reset"],
                "preserve_dirs": [".data", "logs"],
            },
            "implement_gate": {
                "marker": "VERIFIED",
                "skill_names": ["/my-skill"],
            },
            "safety": {
                "reset_guard_marker": ".custom-marker",
                "require_dry_walkthrough": False,
                "test_gate_on_merge": False,
                "protected_branches": ["main", "production"],
            },
            "worktree_setup": {"command": ["task", "install-worktree"]},
        }
        (config_dir / "config.yaml").write_text(yaml.dump(config_data))
        cfg = load_config(tmp_path)

        assert cfg.test_check.command == ["pytest", "-v"]
        assert cfg.test_check.timeout == 300
        assert cfg.classify_fix.path_prefixes == ["src/core/", "tests/core/"]
        assert cfg.reset_workspace.command == ["make", "reset"]
        assert cfg.reset_workspace.preserve_dirs == {".data", "logs"}
        assert cfg.implement_gate.marker == "VERIFIED"
        assert cfg.implement_gate.skill_names == {"/my-skill"}
        assert cfg.safety.reset_guard_marker == ".custom-marker"
        assert cfg.safety.require_dry_walkthrough is False
        assert cfg.safety.test_gate_on_merge is False
        assert cfg.safety.protected_branches == ["main", "production"]
        assert cfg.worktree_setup.command == ["task", "install-worktree"]

    def test_partial_yaml_preserves_defaults(self, tmp_path):
        """C4: YAML with only test_check.command -> other fields keep defaults."""
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        config_data = {"test_check": {"command": ["pytest", "-v"]}}
        (config_dir / "config.yaml").write_text(yaml.dump(config_data))
        cfg = load_config(tmp_path)

        assert cfg.test_check.command == ["pytest", "-v"]
        assert cfg.test_check.timeout == 600  # default preserved
        assert cfg.reset_workspace.command is None  # default preserved
        assert cfg.implement_gate.marker == "Dry-walkthrough verified = TRUE"  # default preserved

    def test_project_overrides_user_config(self, tmp_path, monkeypatch):
        """C5: Both project and user YAML exist -> project values win."""
        # Set up user config
        user_home = tmp_path / "home"
        user_config_dir = user_home / ".autoskillit"
        user_config_dir.mkdir(parents=True)
        user_data = {"test_check": {"command": ["make", "test"], "timeout": 120}}
        (user_config_dir / "config.yaml").write_text(yaml.dump(user_data))
        monkeypatch.setattr("pathlib.Path.home", lambda: user_home)

        # Set up project config
        project_dir = tmp_path / "project"
        project_config_dir = project_dir / ".autoskillit"
        project_config_dir.mkdir(parents=True)
        project_data = {"test_check": {"command": ["pytest", "-v"]}}
        (project_config_dir / "config.yaml").write_text(yaml.dump(project_data))

        cfg = load_config(project_dir)
        assert cfg.test_check.command == ["pytest", "-v"]  # project wins
        assert cfg.test_check.timeout == 120  # user value preserved (not overridden by project)

    def test_user_overrides_defaults(self, tmp_path, monkeypatch):
        """C6: Only user YAML exists -> user values override defaults."""
        user_home = tmp_path / "home"
        user_config_dir = user_home / ".autoskillit"
        user_config_dir.mkdir(parents=True)
        user_data = {"test_check": {"command": ["make", "test"]}}
        (user_config_dir / "config.yaml").write_text(yaml.dump(user_data))
        monkeypatch.setattr("pathlib.Path.home", lambda: user_home)

        # Project dir has no config
        project_dir = tmp_path / "project"
        project_dir.mkdir()

        cfg = load_config(project_dir)
        assert cfg.test_check.command == ["make", "test"]  # user overrides default

    def test_empty_yaml_returns_defaults(self, tmp_path):
        """C7: Empty YAML file -> defaults returned (no crash)."""
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("")
        cfg = load_config(tmp_path)
        assert cfg.test_check.command == ["task", "test-check"]

    @pytest.mark.parametrize(
        "yaml_content,match",
        [
            ("completely_unknown_section:\n  foo: bar\n", "unrecognized key"),  # SCH-1
            ("github:\n  invented_field: whatever\n", "unrecognized key"),  # SCH-2
            ("github:\n  tokn: ghp_abc\n", "did you mean"),  # SCH-3
        ],
    )
    def test_config_yaml_rejects_invalid_keys(self, tmp_path, yaml_content, match):
        """SCH-1/2/3: Unrecognized or near-miss keys in config.yaml raise ConfigSchemaError."""
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text(yaml_content)
        with pytest.raises(ConfigSchemaError, match=match):
            load_config(tmp_path)

    def test_user_config_yaml_rejects_unrecognized_key(self, tmp_path, monkeypatch):
        """SCH-4: User-level config.yaml is also validated."""
        user_home = tmp_path / "home"
        user_config_dir = user_home / ".autoskillit"
        user_config_dir.mkdir(parents=True)
        (user_config_dir / "config.yaml").write_text("bogus_section:\n  k: v\n")
        monkeypatch.setattr("pathlib.Path.home", lambda: user_home)
        with pytest.raises(ConfigSchemaError, match="unrecognized key"):
            load_config(tmp_path)

    def test_set_fields_roundtrip(self, tmp_path):
        """C9: preserve_dirs loaded from YAML list -> becomes set[str]."""
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        config_data = {
            "reset_workspace": {"preserve_dirs": ["cache", "state", "cache"]},  # dupe
        }
        (config_dir / "config.yaml").write_text(yaml.dump(config_data))
        cfg = load_config(tmp_path)
        assert cfg.reset_workspace.preserve_dirs == {"cache", "state"}
        assert isinstance(cfg.reset_workspace.preserve_dirs, set)

    def test_yaml_loads_model_config(self, tmp_path):
        """MOD_C2: YAML with model section populates ModelConfig."""
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        config_data = {"model": {"default": "sonnet"}}
        (config_dir / "config.yaml").write_text(yaml.dump(config_data))
        cfg = load_config(tmp_path)
        assert cfg.model.default == "sonnet"
        assert cfg.model.override is None

    def test_partial_model_config(self, tmp_path):
        """MOD_C3: YAML with only model.override preserves model.default from package defaults."""
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        config_data = {"model": {"override": "haiku"}}
        (config_dir / "config.yaml").write_text(yaml.dump(config_data))
        cfg = load_config(tmp_path)
        assert cfg.model.override == "haiku"
        assert cfg.model.default == "sonnet"

    def test_loaded_config_has_sonnet_default(self, tmp_path):
        """MOD_C4: load_config produces model.default='sonnet'."""
        cfg = load_config(tmp_path)
        assert cfg.model.default == "sonnet"

    def test_yaml_loads_worktree_setup_config(self, tmp_path):
        """WS_C2: YAML with worktree_setup section populates WorktreeSetupConfig."""
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        config_data = {"worktree_setup": {"command": ["task", "install-worktree"]}}
        (config_dir / "config.yaml").write_text(yaml.dump(config_data))
        cfg = load_config(tmp_path)
        assert cfg.worktree_setup.command == ["task", "install-worktree"]

    def test_partial_config_preserves_worktree_setup_default(self, tmp_path):
        """WS_C3: YAML without worktree_setup -> command stays None."""
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        config_data = {"test_check": {"command": ["pytest", "-v"]}}
        (config_dir / "config.yaml").write_text(yaml.dump(config_data))
        cfg = load_config(tmp_path)
        assert cfg.worktree_setup.command is None


class TestTokenUsageConfig:
    """TokenUsageConfig dataclass and YAML loading."""

    def test_default_verbosity_is_summary(self):
        """TU_C1: TokenUsageConfig defaults to verbosity='summary'."""
        from autoskillit.config import TokenUsageConfig

        cfg = TokenUsageConfig()
        assert cfg.verbosity == "summary"

    def test_yaml_loads_verbosity_none(self, tmp_path):
        """TU_C2: token_usage.verbosity loads 'none' from project YAML."""
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        config_data = {"token_usage": {"verbosity": "none"}}
        (config_dir / "config.yaml").write_text(yaml.dump(config_data))
        cfg = load_config(tmp_path)
        assert cfg.token_usage.verbosity == "none"

    def test_partial_config_preserves_token_usage_default(self, tmp_path):
        """TU_C4: Unrelated YAML section leaves token_usage at default."""
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        config_data = {"test_check": {"timeout": 120}}
        (config_dir / "config.yaml").write_text(yaml.dump(config_data))
        cfg = load_config(tmp_path)
        assert cfg.token_usage.verbosity == "summary"


class TestMigrationConfig:
    """MigrationConfig dataclass and YAML loading."""

    def test_default_migration_config_has_empty_suppressed(self):
        """MC1: Default MigrationConfig has empty suppressed list."""
        from autoskillit.config import MigrationConfig

        mc = MigrationConfig()
        assert mc.suppressed == []
        assert isinstance(mc.suppressed, list)

    def test_migration_suppressed_loads_from_yaml(self, tmp_path):
        """MC2: migration.suppressed loads from YAML as list of strings."""
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        config_data = {"migration": {"suppressed": ["script-a", "script-b"]}}
        (config_dir / "config.yaml").write_text(yaml.dump(config_data))
        cfg = load_config(tmp_path)
        assert cfg.migration.suppressed == ["script-a", "script-b"]


class TestEnsureProjectTempAbsent:
    def test_ensure_project_temp_absent_from_config(self):
        import autoskillit.config as config_mod

        assert not hasattr(config_mod, "ensure_project_temp"), (
            "ensure_project_temp was moved to _io; config must not export it"
        )


class TestSyncConfigRemoval:
    def test_automation_config_has_no_sync_field(self):
        """REQ-SYNC-003: AutomationConfig has no sync attribute."""
        cfg = AutomationConfig()
        assert not hasattr(cfg, "sync")

    def test_sync_config_class_does_not_exist(self):
        """REQ-SYNC-003: SyncConfig does not exist in config module."""
        import autoskillit.config as cfg_mod

        assert not hasattr(cfg_mod, "SyncConfig")


class TestRunSkillRetryConfigFields:
    def test_run_skill_retry_config_removed(self):
        """run_skill_retry config section was merged into run_skill (timeout now 7200s)."""
        cfg = AutomationConfig()
        assert cfg.run_skill.timeout == 7200


class TestRunSkillConfigExitAfterStopDelay:
    def test_default_exit_after_stop_delay_is_120000(self):
        cfg = AutomationConfig()
        assert cfg.run_skill.exit_after_stop_delay_ms == 120000

    def test_yaml_loads_exit_after_stop_delay(self, tmp_path):
        (tmp_path / ".autoskillit").mkdir()
        (tmp_path / ".autoskillit" / "config.yaml").write_text(
            "run_skill:\n  exit_after_stop_delay_ms: 60000\n"
        )
        cfg = load_config(tmp_path)
        assert cfg.run_skill.exit_after_stop_delay_ms == 60000

    def test_zero_disables_injection(self, tmp_path):
        (tmp_path / ".autoskillit").mkdir()
        (tmp_path / ".autoskillit" / "config.yaml").write_text(
            "run_skill:\n  exit_after_stop_delay_ms: 0\n"
        )
        cfg = load_config(tmp_path)
        assert cfg.run_skill.exit_after_stop_delay_ms == 0

    def test_partial_run_skill_config_preserves_default(self, tmp_path):
        (tmp_path / ".autoskillit").mkdir()
        (tmp_path / ".autoskillit" / "config.yaml").write_text("run_skill:\n  timeout: 1800\n")
        cfg = load_config(tmp_path)
        assert cfg.run_skill.exit_after_stop_delay_ms == 120000

    def test_run_skill_config_fields_include_exit_delay(self):
        names = {f.name for f in dc_fields(RunSkillConfig)}
        assert "exit_after_stop_delay_ms" in names


class TestQuotaGuardConfig:
    def test_default_enabled(self):
        import pytest

        config = AutomationConfig()
        assert config.quota_guard.enabled is True
        assert config.quota_guard.threshold == pytest.approx(90.0)
        assert config.quota_guard.buffer_seconds == 60
        assert config.quota_guard.cache_max_age == 300

    def test_load_quota_guard_from_yaml(self, tmp_path):
        import pytest

        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text(
            yaml.dump({"quota_guard": {"enabled": True, "threshold": 90.0}})
        )
        config = load_config(tmp_path)
        assert config.quota_guard.enabled is True
        assert config.quota_guard.threshold == pytest.approx(90.0)
        # Unspecified fields keep defaults
        assert config.quota_guard.buffer_seconds == 60


class TestLoggingConfig:
    """LoggingConfig dataclass and YAML loading."""

    def test_logging_config_defaults(self):
        """LOG_C1: LoggingConfig has correct defaults from defaults.yaml."""
        cfg = load_config(None)  # package defaults only (nonexistent project)
        assert cfg.logging.level == "INFO"
        assert cfg.logging.json_output is None

    def test_logging_config_from_yaml(self, tmp_path):
        """LOG_C2: LoggingConfig reads level from project config."""
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("logging:\n  level: DEBUG\n")
        cfg = load_config(tmp_path)
        assert cfg.logging.level == "DEBUG"

    def test_logging_config_json_output_from_yaml(self, tmp_path):
        """LOG_C3: LoggingConfig reads json_output from project config."""
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("logging:\n  json_output: true\n")
        cfg = load_config(tmp_path)
        assert cfg.logging.json_output is True

    def test_logging_config_env_var(self, monkeypatch, tmp_path):
        """LOG_C4: AUTOSKILLIT_LOGGING__LEVEL env var overrides config file."""
        monkeypatch.setenv("AUTOSKILLIT_LOGGING__LEVEL", "WARNING")
        cfg = load_config(tmp_path)
        assert cfg.logging.level == "WARNING"

    def test_logging_config_level_uppercased(self, tmp_path):
        """LOG_C5: Level is uppercased regardless of input casing."""
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("logging:\n  level: debug\n")
        cfg = load_config(tmp_path)
        assert cfg.logging.level == "DEBUG"

    def test_partial_config_preserves_logging_default(self, tmp_path):
        """LOG_C6: Unrelated YAML section leaves logging at default."""
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        config_data = {"test_check": {"timeout": 120}}
        import yaml

        (config_dir / "config.yaml").write_text(yaml.dump(config_data))
        cfg = load_config(tmp_path)
        assert cfg.logging.level == "INFO"
        assert cfg.logging.json_output is None

    def test_automation_config_has_logging_field(self):
        """LOG_C7: AutomationConfig has logging sub-config."""
        cfg = AutomationConfig()
        assert hasattr(cfg, "logging")
        assert cfg.logging.level == "INFO"
        assert cfg.logging.json_output is None

    def test_logging_config_fields(self):
        """LOG_C8: LoggingConfig has exactly the expected fields."""
        from dataclasses import fields as dc_fields

        from autoskillit.config.settings import LoggingConfig

        names = {f.name for f in dc_fields(LoggingConfig)}
        assert names == {"level", "json_output"}


class TestLinuxTracingConfig:
    """LinuxTracingConfig dataclass and YAML loading."""

    def test_linux_tracing_config_defaults(self):
        """LT_C1: LinuxTracingConfig defaults: enabled, 5s interval, empty log_dir."""
        cfg = load_config(None)
        assert cfg.linux_tracing.enabled is True
        assert cfg.linux_tracing.proc_interval == 5.0
        assert cfg.linux_tracing.log_dir == ""

    def test_linux_tracing_config_from_yaml(self, tmp_path):
        """LT_C2: LinuxTracingConfig reads from project config."""
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text(
            "linux_tracing:\n  enabled: true\n  proc_interval: 2.0\n  log_dir: /custom/logs\n"
        )
        cfg = load_config(tmp_path)
        assert cfg.linux_tracing.enabled is True
        assert cfg.linux_tracing.proc_interval == 2.0
        assert cfg.linux_tracing.log_dir == "/custom/logs"

    def test_automation_config_has_linux_tracing_field(self):
        """LT_C3: AutomationConfig has linux_tracing sub-config."""
        cfg = AutomationConfig()
        assert hasattr(cfg, "linux_tracing")
        assert cfg.linux_tracing.enabled is True
        assert cfg.linux_tracing.proc_interval == 5.0
        assert cfg.linux_tracing.log_dir == ""

    def test_linux_tracing_config_fields(self):
        """LT_C4: LinuxTracingConfig has exactly the expected fields."""
        from dataclasses import fields as dc_fields

        from autoskillit.config.settings import LinuxTracingConfig

        names = {f.name for f in dc_fields(LinuxTracingConfig)}
        assert names == {"enabled", "proc_interval", "log_dir", "tmpfs_path"}


class TestDynaconfIntegration:
    def test_env_var_overrides_nested_github_token(self, tmp_path, monkeypatch):
        """ENV-1: AUTOSKILLIT_GITHUB__TOKEN env var sets config.github.token."""
        monkeypatch.setenv("AUTOSKILLIT_GITHUB__TOKEN", "ghp_test_token_123")
        cfg = load_config(tmp_path)
        assert cfg.github.token == "ghp_test_token_123"

    def test_env_var_overrides_integer_field(self, tmp_path, monkeypatch):
        """ENV-2: AUTOSKILLIT_TEST_CHECK__TIMEOUT=999 sets config.test_check.timeout."""
        monkeypatch.setenv("AUTOSKILLIT_TEST_CHECK__TIMEOUT", "999")
        cfg = load_config(tmp_path)
        assert cfg.test_check.timeout == 999

    def test_env_var_takes_priority_over_project_yaml(self, tmp_path, monkeypatch):
        """ENV-3: Env var overrides value set in project config.yaml."""
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text(yaml.dump({"test_check": {"timeout": 300}}))
        monkeypatch.setenv("AUTOSKILLIT_TEST_CHECK__TIMEOUT", "42")
        cfg = load_config(tmp_path)
        assert cfg.test_check.timeout == 42

    def test_secrets_file_is_loaded(self, tmp_path, monkeypatch):
        """SEC-1: .autoskillit/.secrets.yaml provides config values."""
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / ".secrets.yaml").write_text(
            yaml.dump({"github": {"token": "secret_ghp_xxx"}})
        )
        cfg = load_config(tmp_path)
        assert cfg.github.token == "secret_ghp_xxx"

    def test_secrets_file_overrides_config_yaml(self, tmp_path, monkeypatch):
        """SEC-2: .secrets.yaml wins over config.yaml for a shared key."""
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text(
            yaml.dump({"github": {"in_progress_label": "test-label"}})
        )
        (config_dir / ".secrets.yaml").write_text(
            yaml.dump({"github": {"in_progress_label": "secrets-label"}})
        )
        cfg = load_config(tmp_path)
        assert cfg.github.in_progress_label == "secrets-label"

    def test_partial_section_deep_merges_across_layers(self, tmp_path, monkeypatch):
        """MERGE-1: A project config with a partial nested section does not wipe sibling
        keys from the user config.

        Without deep-merge semantics, a project-level github.default_repo would wipe
        the user-level github.in_progress_label.
        """
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
        user_dir = tmp_path / "home" / ".autoskillit"
        user_dir.mkdir(parents=True)
        (user_dir / "config.yaml").write_text(
            yaml.dump({"github": {"in_progress_label": "user-label"}})
        )
        project_dir = tmp_path / ".autoskillit"
        project_dir.mkdir()
        (project_dir / "config.yaml").write_text(
            yaml.dump({"github": {"default_repo": "owner/repo"}})
        )
        cfg = load_config(tmp_path)
        assert cfg.github.in_progress_label == "user-label"
        assert cfg.github.default_repo == "owner/repo"

    def test_config_yaml_rejects_secrets_only_key(self, tmp_path, monkeypatch):
        """SEC-3: github.token in config.yaml raises ConfigSchemaError with .secrets.yaml hint."""
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text(yaml.dump({"github": {"token": "ghp_leaked"}}))
        with pytest.raises(ConfigSchemaError, match=r"\.secrets\.yaml"):
            load_config(tmp_path)

    def test_secrets_yaml_accepts_token(self, tmp_path, monkeypatch):
        """SEC-4: github.token in .secrets.yaml loads without error."""
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / ".secrets.yaml").write_text(yaml.dump({"github": {"token": "ghp_ok"}}))
        cfg = load_config(tmp_path)
        assert cfg.github.token == "ghp_ok"

    def test_secrets_yaml_rejects_unrecognized_keys(self, tmp_path, monkeypatch):
        """SEC-5: Unrecognized key in .secrets.yaml raises ConfigSchemaError."""
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / ".secrets.yaml").write_text("bogus_secret_section:\n  key: val\n")
        with pytest.raises(ConfigSchemaError, match="unrecognized key"):
            load_config(tmp_path)

    def test_bundled_defaults_yaml_exists(self):
        """DFLT-1: The bundled defaults.yaml file is present in the package."""
        from autoskillit.core import pkg_root

        defaults_path = pkg_root() / "config" / "defaults.yaml"
        assert defaults_path.exists(), f"defaults.yaml missing at {defaults_path}"
        assert defaults_path.is_file()

    def test_github_config_has_in_progress_label(self):
        from autoskillit.config.settings import GitHubConfig

        cfg = GitHubConfig()
        assert cfg.in_progress_label == "in-progress"


class TestReleaseReadinessConfig:
    def test_branching_default_base_branch_is_main(self):
        from autoskillit.core.io import load_yaml
        from autoskillit.core.paths import pkg_root

        defaults = load_yaml(pkg_root() / "config" / "defaults.yaml")
        assert defaults["branching"]["default_base_branch"] == "main"

    def test_model_default_consistent_with_yaml(self):
        """ModelConfig dataclass default must match defaults.yaml value."""
        from autoskillit.config.settings import ModelConfig

        assert ModelConfig().default == "sonnet"

    def test_report_bug_config_exported(self):
        from autoskillit.config import ReportBugConfig  # must not raise ImportError

        assert ReportBugConfig is not None


class TestBranchingConfig:
    def test_branching_config_default_base_branch_is_main(self) -> None:
        """BranchingConfig must default default_base_branch to 'main'."""
        from autoskillit.config.settings import BranchingConfig

        assert BranchingConfig().default_base_branch == "main"

    def test_automation_config_has_branching_field(self) -> None:
        """AutomationConfig must expose a BranchingConfig as .branching."""
        from autoskillit.config.settings import AutomationConfig

        cfg = AutomationConfig()
        assert cfg.branching.default_base_branch == "main"

    def test_branching_config_is_overridable(self) -> None:
        """BranchingConfig.default_base_branch must accept override values."""
        from autoskillit.config.settings import BranchingConfig

        cfg = BranchingConfig(default_base_branch="develop")
        assert cfg.default_base_branch == "develop"

    def test_branching_default_base_branch_matches_defaults_yaml(self) -> None:
        """BranchingConfig Python default must match defaults.yaml."""
        from autoskillit.config.settings import BranchingConfig
        from autoskillit.core.io import load_yaml
        from autoskillit.core.paths import pkg_root

        defaults = load_yaml(pkg_root() / "config" / "defaults.yaml")
        yaml_default = defaults["branching"]["default_base_branch"]
        python_default = BranchingConfig().default_base_branch

        assert python_default == yaml_default, (
            f"BranchingConfig.default_base_branch Python default ({python_default!r}) "
            f"disagrees with defaults.yaml ({yaml_default!r})"
        )

    def test_branching_config_promotion_target_defaults_to_main(self) -> None:
        """BranchingConfig.promotion_target defaults to main (package default)."""
        from autoskillit.config.settings import BranchingConfig

        assert BranchingConfig().promotion_target == "main"

    def test_automation_config_branching_promotion_target_default(self) -> None:
        """AutomationConfig propagates promotion_target default."""
        from autoskillit.config.settings import AutomationConfig

        assert AutomationConfig().branching.promotion_target == "main"

    def test_branching_config_promotion_target_overridable(self) -> None:
        """promotion_target can be set independently of default_base_branch."""
        from autoskillit.config.settings import BranchingConfig

        cfg = BranchingConfig(default_base_branch="integration", promotion_target="main")
        assert cfg.default_base_branch == "integration"
        assert cfg.promotion_target == "main"

    def test_branching_config_promotion_target_defaults_match_yaml(self) -> None:
        """Python default for promotion_target matches defaults.yaml."""
        from autoskillit.config import load_config
        from autoskillit.config.settings import BranchingConfig

        loaded = load_config()  # loads only defaults.yaml + user config
        assert loaded.branching.promotion_target == BranchingConfig().promotion_target

    def test_branching_config_promotion_target_env_var_override(self, monkeypatch) -> None:
        """AUTOSKILLIT_BRANCHING__PROMOTION_TARGET env var overrides promotion_target."""
        from autoskillit.config import load_config

        monkeypatch.setenv("AUTOSKILLIT_BRANCHING__PROMOTION_TARGET", "stable")
        cfg = load_config()
        assert cfg.branching.promotion_target == "stable"


class TestBuildSubsetsConfigCustomTagsValidation:
    """CC-F2: _build_subsets_config must raise ValueError for non-dict custom_tags."""

    def test_build_subsets_config_raises_for_non_dict_custom_tags(self):
        """Non-dict custom_tags must raise ValueError, not silently coerce to {}."""
        import pytest

        from autoskillit.config.settings import _build_subsets_config

        with pytest.raises(ValueError, match="custom_tags"):
            _build_subsets_config({"custom_tags": ["list", "not", "dict"]})

    def test_build_subsets_config_raises_for_string_custom_tags(self):
        """String custom_tags must raise ValueError."""
        import pytest

        from autoskillit.config.settings import _build_subsets_config

        with pytest.raises(ValueError, match="custom_tags"):
            _build_subsets_config({"custom_tags": "oops"})

    def test_build_subsets_config_raises_for_int_custom_tags(self):
        """Integer custom_tags must raise ValueError."""
        import pytest

        from autoskillit.config.settings import _build_subsets_config

        with pytest.raises(ValueError, match="custom_tags"):
            _build_subsets_config({"custom_tags": 42})

    def test_build_subsets_config_dict_custom_tags_accepted(self):
        """Valid dict custom_tags must not raise."""
        from autoskillit.config.settings import _build_subsets_config

        result = _build_subsets_config({"custom_tags": {"my_tag": ["skill-a"]}})
        assert result.custom_tags == {"my_tag": ["skill-a"]}

    def test_build_subsets_config_empty_dict_custom_tags_accepted(self):
        """Empty dict custom_tags must not raise."""
        from autoskillit.config.settings import _build_subsets_config

        result = _build_subsets_config({"custom_tags": {}})
        assert result.custom_tags == {}


def test_resolve_ingredient_defaults_in_config():
    from autoskillit.config import resolve_ingredient_defaults

    assert callable(resolve_ingredient_defaults)


class TestSkillsConfig:
    """SkillsConfig dataclass, tier duplication validation, and AutomationConfig integration."""

    def test_skills_config_dataclass(self) -> None:
        """SkillsConfig has tier1, tier2, tier3 list[str] fields."""
        from autoskillit.config.settings import SkillsConfig

        sc = SkillsConfig(tier1=["a"], tier2=["b"], tier3=["c"])
        assert sc.tier1 == ["a"] and sc.tier2 == ["b"] and sc.tier3 == ["c"]

    def test_skills_config_tier_duplication_raises(self) -> None:
        """Skill in multiple tiers raises ValueError at construction (REQ-TIER-009)."""
        import pytest

        from autoskillit.config.settings import SkillsConfig

        with pytest.raises(ValueError, match="multiple tiers"):
            SkillsConfig(tier1=["open-kitchen"], tier2=["open-kitchen"], tier3=[])

    def test_automation_config_has_skills_field(self) -> None:
        """AutomationConfig.skills is a SkillsConfig with tier lists."""
        from autoskillit.config.settings import AutomationConfig, SkillsConfig

        cfg = AutomationConfig()
        assert isinstance(cfg.skills, SkillsConfig)

    def test_defaults_yaml_skills_section(self) -> None:
        """defaults.yaml has non-empty skills.tier1/tier2/tier3 lists."""
        from autoskillit.core import load_yaml, pkg_root

        defaults = load_yaml(pkg_root() / "config" / "defaults.yaml")
        assert isinstance(defaults, dict)
        skills = defaults.get("skills", {})
        assert "open-kitchen" in skills.get("tier1", [])
        assert len(skills.get("tier2", [])) >= 20
        assert len(skills.get("tier3", [])) >= 10

    def test_load_config_populates_skills_tiers(self) -> None:
        """load_config() produces an AutomationConfig with tier assignments from defaults."""
        from autoskillit.config import load_config

        cfg = load_config()
        assert "open-kitchen" in cfg.skills.tier1
        assert "make-plan" in cfg.skills.tier2
        assert "open-pr" in cfg.skills.tier3

    def test_skills_config_exported_from_config_package(self) -> None:
        """SkillsConfig is importable from autoskillit.config and has expected fields."""
        from autoskillit.config import SkillsConfig

        cfg = SkillsConfig()
        assert hasattr(cfg, "tier1")
        assert hasattr(cfg, "tier2")
        assert hasattr(cfg, "tier3")


class TestSubsetsConfig:
    # T1 — SubsetsConfig defaults

    def test_subsets_config_default_disabled_is_empty_list(self) -> None:
        from autoskillit.config import SubsetsConfig

        cfg = SubsetsConfig()
        assert cfg.disabled == []

    def test_subsets_config_default_custom_tags_is_empty_dict(self) -> None:
        from autoskillit.config import SubsetsConfig

        cfg = SubsetsConfig()
        assert cfg.custom_tags == {}

    def test_automation_config_has_subsets_field(self) -> None:
        from autoskillit.config import AutomationConfig, SubsetsConfig

        cfg = AutomationConfig()
        assert isinstance(cfg.subsets, SubsetsConfig)
        assert cfg.subsets.disabled == []
        assert cfg.subsets.custom_tags == {}

    # T2 — load_config with subsets.disabled

    def test_load_config_subsets_disabled(self, tmp_path) -> None:
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("subsets:\n  disabled:\n    - github\n    - ci\n")
        cfg = load_config(tmp_path)
        assert cfg.subsets.disabled == ["github", "ci"]

    def test_load_config_subsets_disabled_absent_means_empty(self, tmp_path) -> None:
        cfg = load_config(tmp_path)
        assert cfg.subsets.disabled == []

    # T3 — load_config with subsets.custom_tags

    def test_load_config_subsets_custom_tags(self, tmp_path) -> None:
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        yaml_text = (
            "subsets:\n"
            "  custom_tags:\n"
            "    my-team-tools:\n"
            "      - investigate\n"
            "      - make-plan\n"
        )
        (config_dir / "config.yaml").write_text(yaml_text)
        cfg = load_config(tmp_path)
        assert cfg.subsets.custom_tags == {"my-team-tools": ["investigate", "make-plan"]}

    def test_load_config_subsets_custom_tags_absent_means_empty(self, tmp_path) -> None:
        cfg = load_config(tmp_path)
        assert cfg.subsets.custom_tags == {}

    # T4 — Unknown category warning, no crash

    def test_load_config_unknown_disabled_category_logs_warning_not_crash(self, tmp_path) -> None:
        import logging

        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text(
            "subsets:\n  disabled:\n    - totally-unknown-category\n"
        )
        # Attach a handler directly to the logger to capture warnings reliably.
        # caplog is unreliable here because the structlog capture_logs() autouse
        # fixture can intercept the handler chain under xdist worker ordering.
        captured: list[logging.LogRecord] = []
        handler = logging.Handler()
        handler.emit = captured.append  # type: ignore[assignment]
        logger = logging.getLogger("autoskillit.config.settings")  # noqa: TID251
        logger.addHandler(handler)
        try:
            cfg = load_config(tmp_path)
        finally:
            logger.removeHandler(handler)
        assert cfg.subsets.disabled == ["totally-unknown-category"]  # preserved as-is
        assert any("totally-unknown-category" in r.getMessage() for r in captured)

    def test_load_config_known_disabled_category_no_warning(self, tmp_path, caplog) -> None:
        import logging

        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text("subsets:\n  disabled:\n    - github\n")
        with caplog.at_level(logging.WARNING):
            load_config(tmp_path)
        assert not any("github" in r.message for r in caplog.records)

    def test_load_config_custom_tag_in_disabled_is_valid(self, tmp_path, caplog) -> None:
        """Custom tags defined in custom_tags can also appear in disabled."""
        import logging

        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        yaml_text = (
            "subsets:\n"
            "  disabled:\n"
            "    - experimental\n"
            "  custom_tags:\n"
            "    experimental:\n"
            "      - write-recipe\n"
        )
        (config_dir / "config.yaml").write_text(yaml_text)
        with caplog.at_level(logging.WARNING):
            load_config(tmp_path)
        assert not any("experimental" in r.message for r in caplog.records)

    # T5 — SubsetsConfig exported from config package

    def test_subsets_config_importable_from_config_package(self) -> None:
        from autoskillit.config import SubsetsConfig

        cfg = SubsetsConfig()
        assert hasattr(cfg, "disabled")
        assert hasattr(cfg, "custom_tags")
