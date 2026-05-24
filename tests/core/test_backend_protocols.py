"""Tests for StreamParser, ResultParser, EnvPolicy, SessionLocator, CodingAgentBackend."""

from __future__ import annotations

from collections.abc import Mapping

import pytest

pytestmark = [pytest.mark.layer("core"), pytest.mark.small]


def test_all_backend_protocols_are_runtime_checkable():
    from autoskillit.core import (
        CodingAgentBackend,
        EnvPolicy,
        ResultParser,
        SessionLocator,
        StreamParser,
    )

    for proto in (StreamParser, ResultParser, EnvPolicy, SessionLocator, CodingAgentBackend):
        assert getattr(proto, "_is_runtime_protocol", False), (
            f"{proto.__name__} must be @runtime_checkable"
        )


def test_coding_agent_backend_has_name_property():
    from autoskillit.core import CodingAgentBackend

    assert isinstance(CodingAgentBackend.__dict__["name"], property)


def test_coding_agent_backend_has_capabilities_property():
    from autoskillit.core import CodingAgentBackend

    assert isinstance(CodingAgentBackend.__dict__["capabilities"], property)


def test_no_autoskillit_imports_in_protocols_backend():
    from autoskillit.core import paths

    proto_path = paths.pkg_root() / "core" / "types" / "_type_protocols_backend.py"
    source = proto_path.read_text()
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("from autoskillit") or stripped.startswith("import autoskillit"):
            pytest.fail(f"IL-0 violation: {stripped}")


def test_stub_class_satisfies_stream_parser():
    from autoskillit.core import SessionEvent, StreamParser

    class _Parser:
        def parse_line(self, line: str) -> SessionEvent | None:
            return None

    assert isinstance(_Parser(), StreamParser)


def test_stub_class_satisfies_coding_agent_backend():
    from collections.abc import Sequence
    from pathlib import Path

    from autoskillit.core import (
        BackendCapabilities,
        CmdSpec,
        CodingAgentBackend,
        EnvPolicy,
        NoResume,
        OutputFormat,
        PluginSource,
        ResultParser,
        ResumeSpec,
        SessionLocator,
        SkillSessionConfig,
        StreamParser,
        ValidatedAddDir,
    )

    class _Backend:
        @property
        def name(self) -> str:
            return "test"

        @property
        def capabilities(self) -> BackendCapabilities: ...

        def build_cmd(self, skill_command: str, cwd: str) -> CmdSpec: ...

        def stream_parser(self, completion_marker: str = "") -> StreamParser: ...

        def result_parser(self) -> ResultParser: ...

        def env_policy(self) -> EnvPolicy: ...

        def session_locator(self) -> SessionLocator: ...

        def write_tool_names(self) -> frozenset[str]: ...

        def build_resume_cmd(
            self,
            *,
            resume_session_id: str,
            prompt: str,
            output_format: OutputFormat = OutputFormat.JSON,
            plugin_source: PluginSource | None = None,
            env_extras: Mapping[str, str] | None = None,
        ) -> CmdSpec: ...

        def build_skill_session_cmd(
            self,
            skill_command: str,
            cwd: str,
            config: SkillSessionConfig,
        ) -> CmdSpec: ...

        def build_food_truck_cmd(
            self,
            *,
            orchestrator_prompt: str,
            plugin_source: PluginSource,
            cwd: str,
            completion_marker: str,
        ) -> CmdSpec: ...

        def build_interactive_cmd(
            self,
            *,
            initial_prompt: str | None = None,
            model: str | None = None,
            plugin_source: PluginSource | None = None,
            add_dirs: Sequence[Path | str | ValidatedAddDir] = (),
            resume_spec: ResumeSpec = NoResume(),
            system_prompt: str | None = None,
            env_extras: Mapping[str, str] | None = None,
            required_env: frozenset[str] | None = None,
        ) -> CmdSpec: ...

    assert isinstance(_Backend(), CodingAgentBackend)


def test_skill_session_config_importable_from_protocols_backend() -> None:
    from autoskillit.core import SkillSessionConfig as SkillSessionConfigDirect
    from autoskillit.core.types._type_protocols_backend import SkillSessionConfig

    assert SkillSessionConfig is SkillSessionConfigDirect
