"""Tests for configuration loading and resolution."""

from dataclasses import fields as dc_fields

import yaml

from autoskillit.config import AutomationConfig, RunSkillConfig, load_config


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
            "version": 1,
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

    def test_unknown_keys_ignored(self, tmp_path):
        """C8: Extra keys in YAML -> no crash, known keys loaded."""
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        config_data = {
            "test_check": {"command": ["pytest"], "unknown_field": "ignored"},
            "completely_unknown_section": {"foo": "bar"},
        }
        (config_dir / "config.yaml").write_text(yaml.dump(config_data))
        cfg = load_config(tmp_path)
        assert cfg.test_check.command == ["pytest"]

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

    def test_stale_sync_yaml_key_silently_ignored(self, tmp_path):
        """REQ-SYNC-003: A config.yaml with a 'sync:' key does not raise."""
        config_file = tmp_path / ".autoskillit" / "config.yaml"
        config_file.parent.mkdir(parents=True)
        config_file.write_text("sync:\n  excluded_recipes:\n    - some-recipe\n")
        cfg = load_config(tmp_path)
        assert isinstance(cfg, AutomationConfig)


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
        """SEC-2: .secrets.yaml wins over config.yaml for the same key."""
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        (config_dir / "config.yaml").write_text(yaml.dump({"github": {"token": "from_config"}}))
        (config_dir / ".secrets.yaml").write_text(yaml.dump({"github": {"token": "from_secrets"}}))
        cfg = load_config(tmp_path)
        assert cfg.github.token == "from_secrets"

    def test_partial_section_deep_merges_across_layers(self, tmp_path, monkeypatch):
        """MERGE-1: A project config with a partial nested section does not wipe sibling
        keys from the user config.

        Without merge_enabled=True, a project-level github.default_repo would wipe
        the user-level github.token.
        """
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
        user_dir = tmp_path / "home" / ".autoskillit"
        user_dir.mkdir(parents=True)
        (user_dir / "config.yaml").write_text(yaml.dump({"github": {"token": "ghp_user_token"}}))
        project_dir = tmp_path / ".autoskillit"
        project_dir.mkdir()
        (project_dir / "config.yaml").write_text(
            yaml.dump({"github": {"default_repo": "owner/repo"}})
        )
        cfg = load_config(tmp_path)
        assert cfg.github.token == "ghp_user_token"
        assert cfg.github.default_repo == "owner/repo"

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
    def test_branching_config_default_base_branch_is_integration(self) -> None:
        """BranchingConfig must default default_base_branch to 'integration'."""
        from autoskillit.config.settings import BranchingConfig

        assert BranchingConfig().default_base_branch == "integration"

    def test_automation_config_has_branching_field(self) -> None:
        """AutomationConfig must expose a BranchingConfig as .branching."""
        from autoskillit.config.settings import AutomationConfig

        cfg = AutomationConfig()
        assert cfg.branching.default_base_branch == "integration"

    def test_branching_config_is_overridable(self) -> None:
        """BranchingConfig.default_base_branch must accept override values."""
        from autoskillit.config.settings import BranchingConfig

        cfg = BranchingConfig(default_base_branch="main")
        assert cfg.default_base_branch == "main"
