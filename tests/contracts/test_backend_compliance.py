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

        from autoskillit.core import CmdSpec, ProjectedPluginRoot
        from autoskillit.execution.backends import BACKEND_REGISTRY
        from autoskillit.execution.backends.codex import CodexBackend  # noqa: F401

        for cls in BACKEND_REGISTRY.values():
            result = cls().build_food_truck_cmd(
                orchestrator_prompt="test",
                plugin_source=ProjectedPluginRoot(plugin_dir=Path("/tmp")),
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

    def test_locator_classes_nominally_subclass_session_locator(self):
        from autoskillit.core import SessionLocator
        from autoskillit.execution.backends import ClaudeSessionLocator, CodexSessionLocator

        assert SessionLocator in ClaudeSessionLocator.__mro__
        assert SessionLocator in CodexSessionLocator.__mro__

    def test_all_backends_project_log_dir_returns_path(self):
        from pathlib import Path

        from autoskillit.execution.backends import BACKEND_REGISTRY
        from autoskillit.execution.backends.codex import CodexBackend  # noqa: F401

        for cls in BACKEND_REGISTRY.values():
            locator = cls().session_locator()
            result = locator.project_log_dir("/tmp")
            assert isinstance(result, Path), (
                f"{type(locator).__name__}.project_log_dir must return Path"
            )

    def test_all_backends_session_locator_has_session_log_path(self):
        from pathlib import Path

        from autoskillit.execution.backends import BACKEND_REGISTRY
        from autoskillit.execution.backends.codex import CodexBackend  # noqa: F401

        for cls in BACKEND_REGISTRY.values():
            locator = cls().session_locator()
            sentinel_result = locator.session_log_path("/tmp", "")
            assert sentinel_result is None, (
                f"{type(locator).__name__}.session_log_path must return None for empty session_id"
            )
            result = locator.session_log_path("/tmp", "abc123")
            assert result is None or isinstance(result, Path), (
                f"{type(locator).__name__}.session_log_path must return Path or None"
            )

    def test_all_backends_session_locator_has_typed_listing(self):
        from autoskillit.execution.backends import BACKEND_REGISTRY
        from autoskillit.execution.backends.codex import CodexBackend  # noqa: F401

        for cls in BACKEND_REGISTRY.values():
            locator = cls().session_locator()
            assert callable(locator.list_sessions)

    def test_all_backends_have_cook_lifecycle_methods(self):
        from autoskillit.execution.backends import BACKEND_REGISTRY
        from autoskillit.execution.backends.codex import CodexBackend  # noqa: F401

        for cls in BACKEND_REGISTRY.values():
            backend = cls()
            for method_name in (
                "validate_interactive_invocation",
                "recover_cook_history",
                "cook_session_context",
            ):
                assert callable(getattr(backend, method_name))

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
            assert isinstance(
                cls().validate_session_layout(tmp_path, project_dir=tmp_path),
                list,
            )

    def test_all_backends_conventions_is_backend_conventions(self):
        from autoskillit.core import BackendConventions
        from autoskillit.execution.backends import BACKEND_REGISTRY
        from autoskillit.execution.backends.codex import CodexBackend  # noqa: F401

        for cls in BACKEND_REGISTRY.values():
            assert isinstance(cls().conventions, BackendConventions)

    def test_backend_conventions_skills_subdir_non_empty(self):
        from autoskillit.execution.backends import BACKEND_REGISTRY
        from autoskillit.execution.backends.codex import CodexBackend  # noqa: F401

        for cls in BACKEND_REGISTRY.values():
            assert len(str(cls().conventions.skills_subdir)) > 0

    def test_claude_backend_validate_session_layout_accepts_valid_dir(self, tmp_path):
        from autoskillit.execution.backends import ClaudeCodeBackend

        skill_dir = tmp_path / ".claude" / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: test-skill\n---\n")
        assert ClaudeCodeBackend().validate_session_layout(tmp_path) == []

    def test_codex_backend_validate_session_layout_accepts_valid_dir(self, tmp_path):
        from autoskillit.execution.backends import CodexBackend

        skill_dir = tmp_path / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: test-skill\n---\n")
        (tmp_path / "config.toml").write_text("[mcp_servers.autoskillit]\n")
        assert CodexBackend().validate_session_layout(tmp_path) == []

    def test_validate_session_layout_empty_dir_returns_errors(self, tmp_path):
        from autoskillit.execution.backends import BACKEND_REGISTRY
        from autoskillit.execution.backends.codex import CodexBackend  # noqa: F401

        for backend_cls in BACKEND_REGISTRY.values():
            errors = backend_cls().validate_session_layout(tmp_path)
            assert len(errors) > 0, f"{backend_cls.__name__} should report errors on empty dir"

    def test_backend_conventions_skills_subdir_matches_validate_session_layout(self, tmp_path):
        from autoskillit.execution.backends import BACKEND_REGISTRY, CodexBackend
        from autoskillit.execution.backends.codex import (
            CodexBackend as _CodexBackend,  # noqa: F401 — triggers CodexBackend registration in BACKEND_REGISTRY
        )

        for cls in BACKEND_REGISTRY.values():
            work_dir = tmp_path / cls.__name__
            work_dir.mkdir()
            skills_subdir = cls().conventions.skills_subdir
            skill_dir = work_dir / str(skills_subdir) / "test-skill"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("---\nname: test-skill\n---\n")
            if issubclass(cls, CodexBackend):
                (work_dir / "config.toml").write_text("[mcp_servers.autoskillit]\n")
            errors = cls().validate_session_layout(work_dir)
            assert errors == [], f"{cls.__name__}: {errors}"
