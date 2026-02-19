"""Tests for configuration loading and resolution."""

import yaml

from autoskillit.config import AutomationConfig, load_config


class TestDefaultConfig:
    def test_default_config_matches_current_constants(self):
        """C1: AutomationConfig() defaults reproduce current hardcoded values."""
        cfg = AutomationConfig()
        assert cfg.test_check.command == ["pytest", "-v"]
        assert cfg.test_check.timeout == 600
        assert cfg.classify_fix.path_prefixes == []
        assert cfg.reset_workspace.command is None
        assert cfg.reset_workspace.preserve_dirs == set()
        assert cfg.implement_gate.marker == "Dry-walkthrough verified = TRUE"
        assert cfg.implement_gate.skill_names == {
            "/implement-worktree",
            "/implement-worktree-no-merge",
        }
        assert cfg.safety.reset_guard_marker == ".autoskillit-workspace"
        assert cfg.safety.require_dry_walkthrough is True
        assert cfg.safety.test_gate_on_merge is True


class TestLoadConfig:
    def test_load_config_no_files_returns_defaults(self, tmp_path):
        """C2: No YAML files on disk -> defaults returned."""
        cfg = load_config(tmp_path)
        assert cfg.test_check.command == ["pytest", "-v"]
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
        assert cfg.test_check.command == ["pytest", "-v"]

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
