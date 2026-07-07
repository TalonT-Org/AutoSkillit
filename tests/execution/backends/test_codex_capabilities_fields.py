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

    def test_git_metadata_writable(self):
        from autoskillit.execution.backends.codex import CodexBackend

        assert CodexBackend().capabilities.git_metadata_writable is False

    def test_github_api_callable_false(self):
        from autoskillit.execution.backends.codex import CodexBackend

        assert CodexBackend().capabilities.github_api_callable is False


class TestSandboxOverridesAggregate:
    """T-A7: github_api_write registry entry produces correct sandbox override aggregate."""

    def test_capability_sandbox_overrides_aggregate_to_network_access(self):
        from autoskillit.core.types._type_constants_registries import SKILL_CAPABILITY_REGISTRY

        uses_caps = frozenset({"github_api_write"})
        sandbox_overrides: frozenset[str] = frozenset().union(
            *(
                SKILL_CAPABILITY_REGISTRY[cap].required_sandbox_overrides
                for cap in uses_caps
                if cap in SKILL_CAPABILITY_REGISTRY
            )
        )
        network_access = "sandbox_workspace_write.network_access=true" in sandbox_overrides
        assert network_access, (
            "github_api_write capability must aggregate to network_access=True via "
            "sandbox_workspace_write.network_access=true override"
        )
