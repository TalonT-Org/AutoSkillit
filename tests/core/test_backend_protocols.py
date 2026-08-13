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


def test_coding_agent_backend_has_conventions_property():
    from autoskillit.core import CodingAgentBackend

    assert isinstance(CodingAgentBackend.__dict__["conventions"], property)


def test_coding_agent_backend_has_setup_session_dir_method():
    from autoskillit.core import CodingAgentBackend

    assert callable(getattr(CodingAgentBackend, "setup_session_dir", None))
    assert callable(getattr(CodingAgentBackend, "clear_explorer_binding_env", None))


def test_session_locator_has_project_log_dir_method():
    from autoskillit.core import SessionLocator

    assert callable(getattr(SessionLocator, "project_log_dir", None))


def test_session_locator_has_session_log_path_method():
    from autoskillit.core import SessionLocator

    assert callable(getattr(SessionLocator, "session_log_path", None))


def test_session_locator_list_sessions_exact_signature():
    import inspect
    import typing
    from collections.abc import Sequence

    from autoskillit.core import SessionLocator, SessionSummary

    signature = inspect.signature(SessionLocator.list_sessions)
    assert tuple(signature.parameters) == ("self", "cwd")
    assert signature.parameters["cwd"].annotation == "str"
    hints = typing.get_type_hints(SessionLocator.list_sessions)
    assert hints == {"cwd": str, "return": Sequence[SessionSummary]}


def test_coding_agent_backend_new_lifecycle_signatures_are_exact():
    import inspect
    import typing
    from contextlib import AbstractContextManager
    from pathlib import Path

    from autoskillit.core import (
        CmdSpec,
        CodingAgentBackend,
        CookSessionHandle,
        ExecutableLaunchBinding,
        ExecutionIdentity,
        PreLaunchReadiness,
        ResumeSpec,
    )

    layout = inspect.signature(CodingAgentBackend.validate_session_layout)
    assert tuple(layout.parameters) == ("self", "session_dir", "project_dir")
    assert layout.parameters["project_dir"].kind is inspect.Parameter.KEYWORD_ONLY
    assert layout.parameters["project_dir"].default is None
    assert typing.get_type_hints(CodingAgentBackend.validate_session_layout) == {
        "session_dir": Path,
        "project_dir": Path | None,
        "return": list[str],
    }

    native = inspect.signature(CodingAgentBackend.validate_interactive_invocation)
    assert tuple(native.parameters) == ("self", "spec")
    assert typing.get_type_hints(CodingAgentBackend.validate_interactive_invocation) == {
        "spec": CmdSpec,
        "return": list[str],
    }

    pre_launch = inspect.signature(CodingAgentBackend.ensure_pre_launch)
    assert tuple(pre_launch.parameters) == ("self", "session_dir", "executable", "plugin_dir")
    assert pre_launch.parameters["session_dir"].kind is inspect.Parameter.KEYWORD_ONLY
    assert pre_launch.parameters["session_dir"].default is None
    assert pre_launch.parameters["executable"].kind is inspect.Parameter.KEYWORD_ONLY
    assert pre_launch.parameters["executable"].default is None
    assert pre_launch.parameters["plugin_dir"].kind is inspect.Parameter.KEYWORD_ONLY
    assert pre_launch.parameters["plugin_dir"].default is None
    assert typing.get_type_hints(CodingAgentBackend.ensure_pre_launch) == {
        "session_dir": Path | None,
        "executable": ExecutableLaunchBinding | None,
        "plugin_dir": Path | None,
        "return": PreLaunchReadiness,
    }

    recovery = inspect.signature(CodingAgentBackend.recover_cook_history)
    assert tuple(recovery.parameters) == ("self",)
    assert typing.get_type_hints(CodingAgentBackend.recover_cook_history) == {"return": type(None)}

    context = inspect.signature(CodingAgentBackend.cook_session_context)
    assert tuple(context.parameters) == (
        "self",
        "session_home",
        "project_dir",
        "launch_id",
        "attempt",
        "current_resume_spec",
    )
    for name in tuple(context.parameters)[1:]:
        assert context.parameters[name].kind is inspect.Parameter.KEYWORD_ONLY
    hints = typing.get_type_hints(CodingAgentBackend.cook_session_context)
    assert hints == {
        "session_home": Path,
        "project_dir": Path,
        "launch_id": str,
        "attempt": int,
        "current_resume_spec": ResumeSpec,
        "return": AbstractContextManager[CookSessionHandle],
    }

    identity = inspect.signature(CodingAgentBackend.resolve_effective_execution_identity)
    assert tuple(identity.parameters) == ("self", "requested", "session_id")
    assert identity.parameters["requested"].kind is inspect.Parameter.KEYWORD_ONLY
    assert identity.parameters["session_id"].kind is inspect.Parameter.KEYWORD_ONLY
    assert typing.get_type_hints(CodingAgentBackend.resolve_effective_execution_identity) == {
        "requested": ExecutionIdentity,
        "session_id": str,
        "return": ExecutionIdentity,
    }


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
    from typing import Any

    from autoskillit.core import (
        BackendCapabilities,
        BackendConventions,
        CmdSpec,
        CodingAgentBackend,
        EnvPolicy,
        ExecutionIdentity,
        ExplorationDispatchRenderer,
        NoResume,
        OutputFormat,
        PluginLaunchBinding,
        PreLaunchReadiness,
        ResultParser,
        ResumeSpec,
        SessionLocator,
        SkillSemanticAdaptationResult,
        SkillSemanticPlan,
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

        @property
        def conventions(self) -> BackendConventions:
            return BackendConventions(
                skills_subdir=Path("test/skills"),
                project_local_skill_search_dirs=(),
            )

        @property
        def exploration_dispatch_renderer(self) -> ExplorationDispatchRenderer: ...

        def build_cmd(self, skill_command: str, cwd: str) -> CmdSpec: ...

        def stream_parser(self, completion_marker: str = "") -> StreamParser: ...

        def result_parser(self) -> ResultParser: ...

        def env_policy(self) -> EnvPolicy: ...

        def session_locator(self) -> SessionLocator: ...

        def resolve_effective_execution_identity(
            self,
            *,
            requested: ExecutionIdentity,
            session_id: str,
        ) -> ExecutionIdentity:
            del session_id
            return requested

        def write_tool_names(self) -> frozenset[str]: ...

        def binary_name(self) -> str: ...

        def build_resume_cmd(
            self,
            *,
            resume_session_id: str,
            prompt: str,
            output_format: OutputFormat = OutputFormat.JSON,
            plugin_binding: PluginLaunchBinding | None = None,
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
            plugin_binding: PluginLaunchBinding | None,
            cwd: str,
            completion_marker: str,
        ) -> CmdSpec: ...

        def build_interactive_cmd(
            self,
            *,
            initial_prompt: str | None = None,
            model: str | None = None,
            plugin_binding: PluginLaunchBinding | None = None,
            add_dirs: Sequence[Path | str | ValidatedAddDir] = (),
            generated_home: Path | None = None,
            resume_spec: ResumeSpec = NoResume(),
            system_prompt: str | None = None,
            env_extras: Mapping[str, str] | None = None,
            required_env: frozenset[str] | None = None,
            tools: Sequence[str] = (),
        ) -> CmdSpec: ...

        def validate_session_layout(
            self,
            session_dir: Path,
            *,
            project_dir: Path | None = None,
        ) -> list[str]: ...

        def validate_interactive_invocation(self, spec: CmdSpec) -> list[str]:
            return []

        def validate_skill_content(self, content: str) -> list[str]: ...

        def version(self) -> str: ...

        def list_plugins(self) -> list[dict[str, Any]]: ...

        def ensure_pre_launch(self, *, session_dir: Path | None = None) -> PreLaunchReadiness:
            return PreLaunchReadiness((), {})

        def recover_cook_history(self) -> None:
            return None

        def cook_session_context(
            self,
            *,
            session_home: Path,
            project_dir: Path,
            launch_id: str,
            attempt: int,
            current_resume_spec: ResumeSpec,
        ):
            del project_dir
            from contextlib import nullcontext

            from autoskillit.core import CookSessionHandle

            return nullcontext(
                CookSessionHandle(
                    view_id=f"{launch_id}-{attempt}",
                    pass_fds=(),
                    _record_spawn=lambda _pid, _pgid: None,
                    _record_reaped=lambda _pid, _pgid: None,
                )
            )

        def translate_model(self, model: str) -> str: ...

        def adapt_skill_semantics(
            self, plan: SkillSemanticPlan
        ) -> SkillSemanticAdaptationResult: ...

        def model_config_overrides(self, model: str) -> tuple[str, ...]:
            return ()

        def build_inspector_cmd(self, prompt: str, *, model: str = "") -> CmdSpec:
            return CmdSpec(cmd=(), env={})

        def setup_session_dir(
            self,
            session_dir: Path,
            *,
            parent_sandbox_mode: str = "workspace-write",
            explorer_binding_env: Mapping[str, Mapping[str, str]] | None = None,
        ) -> None: ...

        def refresh_explorer_binding_env(
            self,
            session_dir: Path,
            explorer_binding_env: Mapping[str, Mapping[str, str]],
        ) -> None: ...

        def clear_explorer_binding_env(self, session_dir: Path, roles: frozenset[str]) -> None: ...

    assert isinstance(_Backend(), CodingAgentBackend)


def test_skill_session_config_importable_from_protocols_backend() -> None:
    from autoskillit.core import SkillSessionConfig as SkillSessionConfigDirect
    from autoskillit.core.types._type_protocols_backend import SkillSessionConfig

    assert SkillSessionConfig is SkillSessionConfigDirect
