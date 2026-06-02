"""Return-type and protocol conformance for all BACKEND_REGISTRY entries.

for-loop conformance tests: each test iterates BACKEND_REGISTRY.values()
and asserts return types or isinstance for all registered backends.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.small]


class TestBackendCompliance:
    def test_all_backends_build_cmd_returns_cmdspec(self):
        from autoskillit.core import CmdSpec
        from autoskillit.execution.backends import BACKEND_REGISTRY
        from autoskillit.execution.backends.codex import CodexBackend  # noqa: F401

        for cls in BACKEND_REGISTRY.values():
            assert isinstance(cls().build_cmd("/test-skill", "/tmp"), CmdSpec)

    def test_all_backends_build_skill_session_cmd_returns_cmdspec(self):
        from autoskillit.core import CmdSpec, SkillSessionConfig
        from autoskillit.execution.backends import BACKEND_REGISTRY
        from autoskillit.execution.backends.codex import CodexBackend  # noqa: F401

        for cls in BACKEND_REGISTRY.values():
            assert isinstance(
                cls().build_skill_session_cmd("/test-skill", "/tmp", SkillSessionConfig()),
                CmdSpec,
            )

    def test_all_backends_build_food_truck_cmd_returns_cmdspec(self):
        from pathlib import Path

        from autoskillit.core import CmdSpec, DirectInstall
        from autoskillit.execution.backends import BACKEND_REGISTRY
        from autoskillit.execution.backends.codex import CodexBackend  # noqa: F401

        for cls in BACKEND_REGISTRY.values():
            result = cls().build_food_truck_cmd(
                orchestrator_prompt="test",
                plugin_source=DirectInstall(plugin_dir=Path("/tmp")),
                cwd="/tmp",
                completion_marker="%%TEST%%",
            )
            assert isinstance(result, CmdSpec)

    def test_all_backends_build_interactive_cmd_returns_cmdspec(self):
        from autoskillit.core import CmdSpec
        from autoskillit.execution.backends import BACKEND_REGISTRY
        from autoskillit.execution.backends.codex import CodexBackend  # noqa: F401

        for cls in BACKEND_REGISTRY.values():
            assert isinstance(cls().build_interactive_cmd(), CmdSpec)

    def test_all_backends_build_resume_cmd_returns_cmdspec(self):
        from autoskillit.core import CmdSpec
        from autoskillit.execution.backends import BACKEND_REGISTRY
        from autoskillit.execution.backends.codex import CodexBackend  # noqa: F401

        for cls in BACKEND_REGISTRY.values():
            result = cls().build_resume_cmd(resume_session_id="test-session-id", prompt="test")
            assert isinstance(result, CmdSpec)

    def test_all_backends_stream_parser_satisfies_protocol(self):
        from autoskillit.core import StreamParser
        from autoskillit.execution.backends import BACKEND_REGISTRY
        from autoskillit.execution.backends.codex import CodexBackend  # noqa: F401

        for cls in BACKEND_REGISTRY.values():
            assert isinstance(cls().stream_parser(), StreamParser)

    def test_all_backends_result_parser_satisfies_protocol(self):
        from autoskillit.core import ResultParser
        from autoskillit.execution.backends import BACKEND_REGISTRY
        from autoskillit.execution.backends.codex import CodexBackend  # noqa: F401

        for cls in BACKEND_REGISTRY.values():
            assert isinstance(cls().result_parser(), ResultParser)

    def test_all_backends_env_policy_satisfies_protocol(self):
        from autoskillit.core import EnvPolicy
        from autoskillit.execution.backends import BACKEND_REGISTRY
        from autoskillit.execution.backends.codex import CodexBackend  # noqa: F401

        for cls in BACKEND_REGISTRY.values():
            assert isinstance(cls().env_policy(), EnvPolicy)

    def test_all_backends_session_locator_satisfies_protocol(self):
        from autoskillit.core import SessionLocator
        from autoskillit.execution.backends import BACKEND_REGISTRY
        from autoskillit.execution.backends.codex import CodexBackend  # noqa: F401

        for cls in BACKEND_REGISTRY.values():
            assert isinstance(cls().session_locator(), SessionLocator)

    def test_all_backends_capabilities_is_backend_capabilities(self):
        from autoskillit.core import BackendCapabilities
        from autoskillit.execution.backends import BACKEND_REGISTRY
        from autoskillit.execution.backends.codex import CodexBackend  # noqa: F401

        for cls in BACKEND_REGISTRY.values():
            assert isinstance(cls().capabilities, BackendCapabilities)

    def test_all_backends_validate_session_layout_returns_list(self, tmp_path):
        from autoskillit.execution.backends import BACKEND_REGISTRY
        from autoskillit.execution.backends.codex import CodexBackend  # noqa: F401

        for cls in BACKEND_REGISTRY.values():
            assert isinstance(cls().validate_session_layout(tmp_path), list)
