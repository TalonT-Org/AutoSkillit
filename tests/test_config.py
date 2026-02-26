"""Tests for configuration loading and resolution."""

import yaml

from autoskillit.config import AutomationConfig, load_config


class TestDefaultConfig:
    def test_default_config_matches_current_constants(self):
        """C1: AutomationConfig() defaults reproduce current hardcoded values."""
        cfg = AutomationConfig()
        assert cfg.test_check.command == ["task", "test-all"]
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
        """MOD_C1: ModelConfig defaults to None for both fields."""
        cfg = AutomationConfig()
        assert cfg.model.default is None
        assert cfg.model.override is None

    def test_default_worktree_setup_config(self):
        """WS_C1: WorktreeSetupConfig defaults to command=None."""
        cfg = AutomationConfig()
        assert cfg.worktree_setup.command is None


class TestLoadConfig:
    def test_load_config_no_files_returns_defaults(self, tmp_path):
        """C2: No YAML files on disk -> defaults returned."""
        cfg = load_config(tmp_path)
        assert cfg.test_check.command == ["task", "test-all"]
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
        assert cfg.test_check.command == ["task", "test-all"]

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
        """MOD_C3: YAML with only model.override preserves model.default as None."""
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        config_data = {"model": {"override": "haiku"}}
        (config_dir / "config.yaml").write_text(yaml.dump(config_data))
        cfg = load_config(tmp_path)
        assert cfg.model.override == "haiku"
        assert cfg.model.default is None

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

    def test_yaml_loads_verbosity_summary(self, tmp_path):
        """TU_C3: token_usage.verbosity loads 'summary' from project YAML."""
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        config_data = {"token_usage": {"verbosity": "summary"}}
        (config_dir / "config.yaml").write_text(yaml.dump(config_data))
        cfg = load_config(tmp_path)
        assert cfg.token_usage.verbosity == "summary"

    def test_partial_config_preserves_token_usage_default(self, tmp_path):
        """TU_C4: Unrelated YAML section leaves token_usage at default."""
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        config_data = {"test_check": {"timeout": 120}}
        (config_dir / "config.yaml").write_text(yaml.dump(config_data))
        cfg = load_config(tmp_path)
        assert cfg.token_usage.verbosity == "summary"

    def test_automation_config_has_token_usage_field(self):
        """TU_C5: AutomationConfig has token_usage sub-config."""
        cfg = AutomationConfig()
        assert hasattr(cfg, "token_usage")
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

    def test_automation_config_has_migration_field_with_defaults(self):
        """MC3: AutomationConfig.migration exists with correct defaults."""
        cfg = AutomationConfig()
        assert hasattr(cfg, "migration")
        assert cfg.migration.suppressed == []


class TestSyncConfig:
    """SyncConfig dataclass and YAML loading."""

    def test_sync_config_defaults_to_empty_excluded_list(self):
        """CFG1: AutomationConfig().sync.excluded_recipes == []"""
        cfg = AutomationConfig()
        assert cfg.sync.excluded_recipes == []
        assert isinstance(cfg.sync.excluded_recipes, list)

    def test_sync_config_loads_excluded_recipes_from_yaml(self, tmp_path):
        """CFG2: _merge_into populates excluded_recipes from sync.excluded_recipes in YAML"""
        config_dir = tmp_path / ".autoskillit"
        config_dir.mkdir()
        config_data = {"sync": {"excluded_recipes": ["implementation", "bugfix-loop"]}}
        (config_dir / "config.yaml").write_text(yaml.dump(config_data))
        cfg = load_config(tmp_path)
        assert cfg.sync.excluded_recipes == ["implementation", "bugfix-loop"]


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
