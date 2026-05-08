"""Tests for GitHubConfig.queued_label field and label-state mapping methods."""

from __future__ import annotations

import pytest

from autoskillit.config import AutomationConfig
from autoskillit.config._config_loader import _make_dynaconf
from autoskillit.config.settings import GitHubConfig
from autoskillit.core.types import IssueLabelState

pytestmark = [pytest.mark.layer("config"), pytest.mark.small]


class TestQueuedLabelField:
    def test_default_is_queued(self):
        """GitHubConfig has queued_label field with default 'queued'."""
        cfg = GitHubConfig()
        assert cfg.queued_label == "queued"

    def test_loads_from_env_var(self, monkeypatch):
        """queued_label is loaded from AUTOSKILLIT_GITHUB__QUEUED_LABEL env var."""
        monkeypatch.setenv("AUTOSKILLIT_GITHUB__QUEUED_LABEL", "waiting")
        d = _make_dynaconf()
        cfg = AutomationConfig.from_dynaconf(d)
        assert cfg.github.queued_label == "waiting"


class TestLabelStateMapping:
    def test_label_for_state_returns_configured_string(self):
        """label_for_state returns the configured label string for each IssueLabelState."""
        cfg = GitHubConfig()
        assert cfg.label_for_state(IssueLabelState.QUEUED) == "queued"
        assert cfg.label_for_state(IssueLabelState.IN_PROGRESS) == "in-progress"
        assert cfg.label_for_state(IssueLabelState.STAGED) == "staged"
        assert cfg.label_for_state(IssueLabelState.FAIL) == "fail"

    def test_label_for_state_respects_custom_config(self):
        """label_for_state uses per-field overrides when set."""
        cfg = GitHubConfig(queued_label="waiting")
        assert cfg.label_for_state(IssueLabelState.QUEUED) == "waiting"

    def test_state_for_label_roundtrip(self):
        """state_for_label(label_for_state(state)) == state for all states."""
        cfg = GitHubConfig()
        for state in IssueLabelState:
            label = cfg.label_for_state(state)
            assert cfg.state_for_label(label) == state

    def test_state_for_label_returns_none_for_classification_labels(self):
        """state_for_label returns None for non-lifecycle labels."""
        cfg = GitHubConfig()
        assert cfg.state_for_label("bug") is None
        assert cfg.state_for_label("recipe:implementation") is None

    def test_label_for_state_covers_all_members(self):
        """label_for_state returns a non-empty string for every IssueLabelState."""
        cfg = GitHubConfig()
        for state in IssueLabelState:
            label = cfg.label_for_state(state)
            assert label
