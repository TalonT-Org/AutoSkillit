from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from autoskillit.core import (
    AGENT_BACKEND_CLAUDE_CODE,
    BackendConventions,
    CmdSpec,
    CodingAgentBackend,
    EnvPolicy,
    OutputFormat,
    ResultParser,
    SessionLocator,
    StreamParser,
)
from autoskillit.execution.backends import ClaudeCodeBackend, ClaudeStreamParser

pytestmark = [pytest.mark.layer("execution"), pytest.mark.small]


class TestClaudeCodeBackend:
    def test_isinstance_coding_agent_backend(self) -> None:
        assert isinstance(ClaudeCodeBackend(), CodingAgentBackend)

    def test_name_property(self) -> None:
        assert ClaudeCodeBackend().name == AGENT_BACKEND_CLAUDE_CODE

    def test_capabilities_mcp_config_capable_false(self) -> None:
        assert ClaudeCodeBackend().capabilities.mcp_config_capable is False

    def test_binary_name(self) -> None:
        assert ClaudeCodeBackend().binary_name() == "claude"

    def test_version_cmd(self) -> None:
        assert ClaudeCodeBackend().version_cmd() == ("claude", "--version")

    def test_build_cmd_returns_cmd_spec(self, tmp_path: Path) -> None:
        result = ClaudeCodeBackend().build_cmd("say hello", str(tmp_path))
        assert isinstance(result, CmdSpec)

    def test_build_cmd_cmd_is_tuple_not_list(self, tmp_path: Path) -> None:
        result = ClaudeCodeBackend().build_cmd("say hello", str(tmp_path))
        assert isinstance(result.cmd, tuple)

    def test_build_cmd_matches_build_headless_cmd(self, tmp_path: Path) -> None:
        backend = ClaudeCodeBackend()
        skill_cmd = "say hello"
        direct = backend.build_headless_cmd(skill_cmd)
        result = backend.build_cmd(skill_cmd, str(tmp_path))
        assert direct.cmd == result.cmd
        assert direct.env == result.env
        assert result.cwd == str(tmp_path)

    def test_stream_parser_returns_stream_parser(self) -> None:
        backend = ClaudeCodeBackend()
        result = backend.stream_parser()
        assert isinstance(result, StreamParser)

    @pytest.mark.parametrize(
        ("marker_kwarg", "expected"),
        [
            ({"completion_marker": "%%DONE%%"}, "%%DONE%%"),
            ({}, ""),
        ],
        ids=["explicit-marker", "default-empty"],
    )
    def test_stream_parser_factory_completion_marker(
        self, marker_kwarg: dict[str, str], expected: str
    ) -> None:
        parser = ClaudeCodeBackend().stream_parser(**marker_kwarg)
        assert isinstance(parser, ClaudeStreamParser)
        assert parser.completion_marker == expected

    def test_result_parser_returns_result_parser(self) -> None:
        backend = ClaudeCodeBackend()
        result = backend.result_parser()
        assert isinstance(result, ResultParser)

    def test_env_policy_returns_env_policy(self) -> None:
        backend = ClaudeCodeBackend()
        result = backend.env_policy()
        assert isinstance(result, EnvPolicy)

    def test_session_locator_returns_session_locator(self) -> None:
        backend = ClaudeCodeBackend()
        result = backend.session_locator()
        assert isinstance(result, SessionLocator)

    def test_write_tool_names_returns_write_edit(self) -> None:
        backend = ClaudeCodeBackend()
        assert backend.write_tool_names() == frozenset({"Write", "Edit"})

    def test_conventions_returns_backend_conventions(self) -> None:
        result = ClaudeCodeBackend().conventions
        assert isinstance(result, BackendConventions)
        assert str(result.skills_subdir) == ".claude/skills"
        assert result.project_local_skill_search_dirs == (".claude/skills", ".autoskillit/skills")

    def test_setup_session_dir_returns_none(self, tmp_path: Path) -> None:
        result = ClaudeCodeBackend().setup_session_dir(tmp_path)
        assert result is None

    def test_setup_session_dir_does_not_raise_or_write(self, tmp_path: Path) -> None:
        before = list(tmp_path.iterdir())
        ClaudeCodeBackend().setup_session_dir(tmp_path)
        after = list(tmp_path.iterdir())
        assert before == after


class TestClaudeCodeBackendAgentBackendEnv:
    """Tests that AUTOSKILLIT_AGENT_BACKEND is injected into skill session env."""

    BASE: dict[str, object] = {
        "skill_command": "/test-skill",
        "cwd": "/work",
        "completion_marker": "%%DONE%%",
        "model": None,
        "plugin_source": None,
        "output_format": OutputFormat.JSON,
    }

    def test_agent_backend_env_set(self) -> None:
        spec = ClaudeCodeBackend().build_skill_session_cmd(**self.BASE)
        assert spec.env["AUTOSKILLIT_AGENT_BACKEND"] == "claude-code"

    def test_agent_backend_overrides_parent_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTOSKILLIT_AGENT_BACKEND", "wrong-value")
        spec = ClaudeCodeBackend().build_skill_session_cmd(**self.BASE)
        assert spec.env["AUTOSKILLIT_AGENT_BACKEND"] == "claude-code"

    def test_agent_backend_present_without_parent_env(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("AUTOSKILLIT_AGENT_BACKEND", raising=False)
        spec = ClaudeCodeBackend().build_skill_session_cmd(**self.BASE)
        assert spec.env["AUTOSKILLIT_AGENT_BACKEND"] == "claude-code"


def test_headless_env_hardening_constant_exists() -> None:
    from autoskillit.execution.backends._claude_prompt import _HEADLESS_ENV_HARDENING

    assert _HEADLESS_ENV_HARDENING["TERM"] == "dumb"
    assert _HEADLESS_ENV_HARDENING["NO_COLOR"] == "1"


def test_build_headless_cmd_injects_hardening() -> None:
    from autoskillit.core import build_agent_env
    from autoskillit.execution.backends._claude_prompt import _HEADLESS_ENV_HARDENING

    env = dict(build_agent_env(base={}, extras=_HEADLESS_ENV_HARDENING))
    assert env["TERM"] == "dumb"
    assert env["NO_COLOR"] == "1"


class TestClaudeCodeBackendVersion:
    def test_happy_path_returns_stripped_stdout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_run(cmd, *, capture_output, text, timeout):
            result = subprocess.CompletedProcess(cmd, 0)
            result.stdout = "  1.0.42\n"
            result.stderr = ""
            return result

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert ClaudeCodeBackend().version() == "1.0.42"

    def test_execpath_env_overrides_binary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured_cmd = None

        def fake_run(cmd, *, capture_output, text, timeout):
            nonlocal captured_cmd
            captured_cmd = cmd
            result = subprocess.CompletedProcess(cmd, 0)
            result.stdout = "v1"
            result.stderr = ""
            return result

        monkeypatch.setenv("CLAUDE_CODE_EXECPATH", "/custom/claude")
        monkeypatch.setattr(subprocess, "run", fake_run)
        ClaudeCodeBackend().version()
        assert captured_cmd[0] == "/custom/claude"

    def test_fallback_to_version_cmd_when_env_absent(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured_cmd = None

        def fake_run(cmd, *, capture_output, text, timeout):
            nonlocal captured_cmd
            captured_cmd = cmd
            result = subprocess.CompletedProcess(cmd, 0)
            result.stdout = "v1"
            result.stderr = ""
            return result

        monkeypatch.delenv("CLAUDE_CODE_EXECPATH", raising=False)
        monkeypatch.setattr(subprocess, "run", fake_run)
        ClaudeCodeBackend().version()
        assert captured_cmd[0] == "claude"

    def test_timeout_returns_empty_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_run(cmd, *, capture_output, text, timeout):
            raise subprocess.TimeoutExpired(cmd, timeout)

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert ClaudeCodeBackend().version() == ""

    def test_oserror_returns_empty_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_run(cmd, *, capture_output, text, timeout):
            raise OSError("not found")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert ClaudeCodeBackend().version() == ""

    def test_nonzero_exit_returns_stdout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_run(cmd, *, capture_output, text, timeout):
            result = subprocess.CompletedProcess(cmd, 1)
            result.stdout = "version info"
            result.stderr = "some error"
            return result

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert ClaudeCodeBackend().version() == "version info"


class TestClaudeCodeBackendListPlugins:
    def _write_plugins_json(self, home: Path, data: dict) -> None:
        plugins_dir = home / ".claude" / "plugins"
        plugins_dir.mkdir(parents=True, exist_ok=True)
        (plugins_dir / "installed_plugins.json").write_text(json.dumps(data), encoding="utf-8")

    def test_happy_path_returns_plugin_list(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = tmp_path / "home"
        home.mkdir()
        self._write_plugins_json(
            home,
            {
                "plugins": {
                    "@anthropic/tool-use": [{"version": "1.2.3", "other": "x"}],
                }
            },
        )
        monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
        result = ClaudeCodeBackend().list_plugins()
        assert result == [{"ref": "@anthropic/tool-use", "version": "1.2.3"}]

    def test_file_not_found_returns_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
        assert ClaudeCodeBackend().list_plugins() == []

    def test_invalid_json_returns_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = tmp_path / "home"
        home.mkdir()
        plugins_dir = home / ".claude" / "plugins"
        plugins_dir.mkdir(parents=True)
        (plugins_dir / "installed_plugins.json").write_text("{bad json", encoding="utf-8")
        monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
        assert ClaudeCodeBackend().list_plugins() == []

    def test_plugins_not_dict_returns_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = tmp_path / "home"
        home.mkdir()
        self._write_plugins_json(home, {"plugins": ["not", "a", "dict"]})
        monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
        assert ClaudeCodeBackend().list_plugins() == []

    def test_empty_installs_skipped(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        home = tmp_path / "home"
        home.mkdir()
        self._write_plugins_json(home, {"plugins": {"ref-a": []}})
        monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
        assert ClaudeCodeBackend().list_plugins() == []

    def test_non_list_installs_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = tmp_path / "home"
        home.mkdir()
        self._write_plugins_json(home, {"plugins": {"ref-a": "not-a-list"}})
        monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
        assert ClaudeCodeBackend().list_plugins() == []

    def test_missing_version_key_returns_ref_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        home = tmp_path / "home"
        home.mkdir()
        self._write_plugins_json(home, {"plugins": {"ref-a": [{"other": "x"}]}})
        monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
        result = ClaudeCodeBackend().list_plugins()
        assert result == [{"ref": "ref-a"}]


class TestClaudeCodeBackendValidateSkillContent:
    def _make_backend_with_fields(
        self, monkeypatch: pytest.MonkeyPatch, fields: frozenset[str]
    ) -> ClaudeCodeBackend:
        from dataclasses import replace

        import autoskillit.execution.backends.claude as _claude_mod
        from autoskillit.core import CLAUDE_CODE_CAPABILITIES

        custom = replace(CLAUDE_CODE_CAPABILITIES, required_skill_fields=fields)
        monkeypatch.setattr(_claude_mod, "CLAUDE_CODE_CAPABILITIES", custom)
        return ClaudeCodeBackend()

    def test_empty_required_fields_returns_empty_list(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = self._make_backend_with_fields(monkeypatch, frozenset())
        assert backend.validate_skill_content("---\nname: x\n---\n") == []

    def test_all_required_fields_present_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = self._make_backend_with_fields(monkeypatch, frozenset({"name", "description"}))
        content = "---\nname: test\ndescription: a thing\n---\nbody"
        assert backend.validate_skill_content(content) == []

    def test_one_missing_field_returns_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        backend = self._make_backend_with_fields(monkeypatch, frozenset({"name", "description"}))
        content = "---\nname: test\n---\nbody"
        result = backend.validate_skill_content(content)
        assert len(result) == 1
        assert "Missing required frontmatter field: 'description'" in result[0]

    def test_multiple_missing_fields_returns_per_field(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        backend = self._make_backend_with_fields(monkeypatch, frozenset({"name", "description"}))
        content = "---\nother: x\n---\nbody"
        result = backend.validate_skill_content(content)
        assert len(result) == 2
        assert any("name" in e for e in result)
        assert any("description" in e for e in result)

    def test_no_opening_delimiter_returns_sentinel(self) -> None:
        result = ClaudeCodeBackend().validate_skill_content("no frontmatter here")
        assert result == ["Invalid frontmatter: no opening --- delimiter found"]

    def test_no_closing_delimiter_returns_sentinel(self) -> None:
        result = ClaudeCodeBackend().validate_skill_content("---\nname: x\n")
        assert result == ["Invalid frontmatter: no closing --- delimiter found"]

    def test_malformed_yaml_returns_parse_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        backend = self._make_backend_with_fields(monkeypatch, frozenset({"name", "description"}))
        content = "---\n: [invalid yaml\n---\nbody"
        result = backend.validate_skill_content(content)
        assert len(result) == 1
        assert "YAML parse error" in result[0]
