"""Tests for GitHubConfig.staged_label field and config layer resolution."""

from __future__ import annotations

from autoskillit.config import AutomationConfig
from autoskillit.config.settings import GitHubConfig, _make_dynaconf


class TestGitHubConfigStagedLabel:
    def test_github_config_staged_label_default(self):
        """GitHubConfig has staged_label field with default 'staged'."""
        cfg = GitHubConfig()
        assert cfg.staged_label == "staged"

    def test_automation_config_staged_label_default(self):
        """AutomationConfig().github.staged_label defaults to 'staged'."""
        cfg = AutomationConfig()
        assert cfg.github.staged_label == "staged"

    def test_github_config_staged_label_from_env(self, monkeypatch):
        """staged_label is loaded from AUTOSKILLIT_GITHUB__STAGED_LABEL env var."""
        monkeypatch.setenv("AUTOSKILLIT_GITHUB__STAGED_LABEL", "awaiting-promotion")
        d = _make_dynaconf()
        cfg = AutomationConfig.from_dynaconf(d)
        assert cfg.github.staged_label == "awaiting-promotion"

    def test_github_config_staged_label_independent_of_in_progress_label(self):
        """staged_label and in_progress_label are independent fields."""
        cfg = GitHubConfig(in_progress_label="wip", staged_label="pending-merge")
        assert cfg.in_progress_label == "wip"
        assert cfg.staged_label == "pending-merge"
