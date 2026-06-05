"""Tests for CodexBackend capabilities — 4 new str field values."""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestCodexCapabilitiesNewFields:
    """CodexBackend.capabilities supplies the 4 new str fields with Codex-specific values."""

    def test_hook_config_format(self):
        from autoskillit.execution.backends.codex import CodexBackend

        assert CodexBackend().capabilities.hook_config_format == "toml_nested"

    def test_write_detection_strategy(self):
        from autoskillit.execution.backends.codex import CodexBackend

        assert CodexBackend().capabilities.write_detection_strategy == "file_changes"

    def test_patch_format(self):
        from autoskillit.execution.backends.codex import CodexBackend

        assert CodexBackend().capabilities.patch_format == "codex_star_update"

    def test_default_skill_sandbox_mode(self):
        from autoskillit.execution.backends.codex import CodexBackend

        assert CodexBackend().capabilities.default_skill_sandbox_mode == "workspace-write"
