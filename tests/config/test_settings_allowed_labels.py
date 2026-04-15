"""Tests for GitHubConfig.allowed_labels field and check_label_allowed validation."""

from autoskillit.config import AutomationConfig
from autoskillit.config.settings import GitHubConfig, _make_dynaconf


class TestGitHubConfigAllowedLabelsField:
    def test_automation_config_allowed_labels_default_is_empty(self):
        """AutomationConfig().github.allowed_labels defaults to empty list."""
        cfg = AutomationConfig()
        assert cfg.github.allowed_labels == []

    def test_allowed_labels_from_env_var(self, monkeypatch):
        """allowed_labels is loaded from AUTOSKILLIT_GITHUB__ALLOWED_LABELS env var."""
        monkeypatch.setenv("AUTOSKILLIT_GITHUB__ALLOWED_LABELS", '@json ["bug", "enhancement"]')
        d = _make_dynaconf()
        cfg = AutomationConfig.from_dynaconf(d)
        assert "bug" in cfg.github.allowed_labels
        assert "enhancement" in cfg.github.allowed_labels


class TestCheckLabelAllowed:
    def test_empty_allowed_labels_permits_any_label(self):
        """REQ-TEST-003: Empty whitelist permits all labels (no restriction)."""
        cfg = GitHubConfig(allowed_labels=[])
        assert cfg.check_label_allowed("arbitrary-label") is None

    def test_absent_allowed_labels_permits_any_label(self):
        """REQ-TEST-003: Default (absent) whitelist permits all labels."""
        cfg = GitHubConfig()
        assert cfg.check_label_allowed("any-label") is None
        assert cfg.check_label_allowed("recipe:implementation") is None

    def test_whitelisted_label_returns_none(self):
        """REQ-TEST-001: A label present in allowed_labels passes validation."""
        cfg = GitHubConfig(allowed_labels=["bug", "enhancement", "in-progress"])
        assert cfg.check_label_allowed("bug") is None
        assert cfg.check_label_allowed("enhancement") is None
        assert cfg.check_label_allowed("in-progress") is None

    def test_non_whitelisted_label_returns_error_string(self):
        """REQ-TEST-002: A label absent from allowed_labels returns an error message."""
        cfg = GitHubConfig(allowed_labels=["bug", "enhancement"])
        err = cfg.check_label_allowed("arbitrary-junk")
        assert err is not None
        assert isinstance(err, str)

    def test_error_message_names_disallowed_label(self):
        """REQ-TEST-002: Error message identifies the disallowed label."""
        cfg = GitHubConfig(allowed_labels=["bug"])
        err = cfg.check_label_allowed("bad-label")
        assert "bad-label" in err

    def test_error_message_lists_allowed_alternatives(self):
        """REQ-TEST-002: Error message lists the allowed alternatives."""
        cfg = GitHubConfig(allowed_labels=["bug", "enhancement"])
        err = cfg.check_label_allowed("bad-label")
        assert "bug" in err
        assert "enhancement" in err

    def test_error_message_is_actionable(self):
        """REQ-TEST-002: Error message includes guidance on how to fix."""
        cfg = GitHubConfig(allowed_labels=["bug"])
        err = cfg.check_label_allowed("bad-label")
        assert "allowed" in err.lower()


class TestDefaultsYamlAllowedLabels:
    def test_load_config_from_defaults_includes_standard_labels(self, tmp_path):
        """REQ-CFG-003: defaults.yaml whitelist includes project standard labels."""
        from autoskillit.config import load_config

        cfg = load_config(tmp_path)
        expected = {
            "bug",
            "enhancement",
            "in-progress",
            "staged",
            "autoreported",
            "recipe:implementation",
            "recipe:remediation",
        }
        assert expected.issubset(set(cfg.github.allowed_labels))
