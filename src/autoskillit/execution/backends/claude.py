from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autoskillit.core import (
    AGENT_BACKEND_CLAUDE_CODE,
    CLAUDE_CODE_CAPABILITIES,
    CONTEXT_EXHAUSTION_MARKER,
    AgentSessionResult,
    BackendCapabilities,
    BackendEventKind,
    ClaudeEventData,
    CmdSpec,
    NoResume,
    OutputFormat,
    PluginSource,
    ResumeSpec,
    SessionEvent,
    ValidatedAddDir,
    build_agent_env,
    fast_loads,
)
from autoskillit.execution.session import parse_session_result

__all__ = [
    "ClaudeCodeBackend",
    "ClaudeEnvPolicy",
    "ClaudeResultParser",
    "ClaudeSessionLocator",
    "ClaudeStreamParser",
]


def _marker_is_standalone(text: str, marker: str) -> bool:
    for text_line in text.splitlines():
        if text_line.strip() == marker:
            return True
    return False


def _extract_write_artifacts(tool_uses: list[dict[str, Any]]) -> list[str]:
    return [
        t.get("file_path", "")
        for t in tool_uses
        if t.get("name") in {"Write", "Edit"} and t.get("file_path")
    ]


@dataclass(frozen=True, slots=True)
class ClaudeEnvPolicy:
    def build_env(self, base_env: Mapping[str, str]) -> dict[str, str]:
        return dict(build_agent_env(base=base_env))


@dataclass(frozen=True, slots=True)
class ClaudeSessionLocator:
    def locate_session(self, session_id: str) -> Path | None:
        if not session_id or session_id.startswith(("no_session_", "crashed_")):
            return None
        base = Path.home() / ".claude" / "projects"
        if not base.exists():
            return None
        for project_dir in base.iterdir():
            if not project_dir.is_dir():
                continue
            candidate = project_dir / f"{session_id}.jsonl"
            if candidate.exists():
                return candidate
        return None


@dataclass(frozen=True, slots=True)
class ClaudeStreamParser:
    completion_marker: str = ""

    def parse_line(self, line: str) -> SessionEvent | None:
        line = line.strip()
        if not line:
            return None
        try:
            obj = fast_loads(line)
        except (ValueError, TypeError):
            return None
        if not isinstance(obj, dict):
            return None

        record_type = obj.get("type", "")

        if record_type == "system":
            session_id = obj.get("session_id", "")
            return SessionEvent(
                kind=BackendEventKind.SESSION_META,
                is_terminal=False,
                has_marker=False,
                session_id=session_id or None,
            )

        if record_type == "result":
            result_field = obj.get("result", "")
            if not (isinstance(result_field, str) and result_field.strip()):
                return SessionEvent(
                    kind=BackendEventKind.IGNORED,
                    is_terminal=False,
                    has_marker=False,
                )
            has_marker = bool(
                self.completion_marker
                and _marker_is_standalone(result_field, self.completion_marker)
            )
            return SessionEvent(
                kind=BackendEventKind.COMPLETION,
                is_terminal=True,
                has_marker=has_marker,
                backend_data=ClaudeEventData(
                    record_type="result",
                    subtype=obj.get("subtype", ""),
                    session_id=obj.get("session_id", ""),
                    raw=obj,
                ),
            )

        if record_type == "assistant":
            if "message" not in obj and obj.get("output_tokens", -1) == 0:
                flat_content = obj.get("content", [])
                if isinstance(flat_content, list) and any(
                    isinstance(block, dict)
                    and block.get("type") == "text"
                    and CONTEXT_EXHAUSTION_MARKER in block.get("text", "").lower()
                    for block in flat_content
                ):
                    return SessionEvent(
                        kind=BackendEventKind.TOOL_OUTPUT,
                        is_terminal=False,
                        has_marker=False,
                        backend_data=ClaudeEventData(
                            record_type="assistant",
                            subtype="context_exhaustion",
                            session_id="",
                            raw=obj,
                        ),
                    )
            return SessionEvent(
                kind=BackendEventKind.IGNORED,
                is_terminal=False,
                has_marker=False,
            )

        return SessionEvent(
            kind=BackendEventKind.IGNORED,
            is_terminal=False,
            has_marker=False,
        )


@dataclass(frozen=True, slots=True)
class ClaudeResultParser:
    def parse_result(self, events: Sequence[SessionEvent]) -> AgentSessionResult:
        session_id: str | None = None
        has_completion = False
        has_marker = False
        last_backend_data: ClaudeEventData | None = None
        for event in events:
            if event.kind == BackendEventKind.SESSION_META and event.session_id:
                session_id = event.session_id
            if event.kind == BackendEventKind.COMPLETION:
                has_completion = True
                if event.has_marker:
                    has_marker = True
                if isinstance(event.backend_data, ClaudeEventData):
                    last_backend_data = event.backend_data
        output = ""
        if last_backend_data and last_backend_data.raw:
            output = last_backend_data.raw.get("result", "")
        success = has_completion and has_marker
        return AgentSessionResult(
            success=success,
            exit_code=0 if success else 1,
            backend_name=AGENT_BACKEND_CLAUDE_CODE,
            elapsed_seconds=0.0,
            session_id=session_id,
            output=output if isinstance(output, str) else "",
        )

    def parse_stdout(self, stdout: str) -> AgentSessionResult:
        result = parse_session_result(stdout)
        write_artifacts = _extract_write_artifacts(result.tool_uses)
        return AgentSessionResult(
            success=result.session_complete,
            exit_code=0 if result.session_complete else 1,
            backend_name=AGENT_BACKEND_CLAUDE_CODE,
            elapsed_seconds=0.0,
            session_id=result.session_id or None,
            output=result.result,
            error="\n".join(result.errors) if result.errors else "",
            raw={
                "subtype": result.subtype.value,
                "is_error": result.is_error,
                "token_usage": result.token_usage,
                "write_artifacts": write_artifacts,
                "tool_uses": result.tool_uses,
                "assistant_messages": result.assistant_messages,
                "jsonl_context_exhausted": result.jsonl_context_exhausted,
                "stop_reasons": result.stop_reasons,
                "has_thinking_only_turn": result.has_thinking_only_turn,
                "seen_block_types": list(result.seen_block_types),
            },
        )


@dataclass(frozen=True, slots=True)
class ClaudeCodeBackend:
    @property
    def name(self) -> str:
        return AGENT_BACKEND_CLAUDE_CODE

    @property
    def capabilities(self) -> BackendCapabilities:
        return CLAUDE_CODE_CAPABILITIES

    def build_cmd(self, skill_command: str, cwd: str) -> CmdSpec:
        from autoskillit.execution.commands import build_headless_cmd

        spec = build_headless_cmd(skill_command)
        return CmdSpec(cmd=tuple(spec.cmd), env=spec.env, cwd=cwd)

    def stream_parser(self) -> ClaudeStreamParser:
        return ClaudeStreamParser()

    def result_parser(self) -> ClaudeResultParser:
        return ClaudeResultParser()

    def env_policy(self) -> ClaudeEnvPolicy:
        return ClaudeEnvPolicy()

    def session_locator(self) -> ClaudeSessionLocator:
        return ClaudeSessionLocator()

    def binary_name(self) -> str:
        return "claude"

    def version_cmd(self) -> tuple[str, ...]:
        return ("claude", "--version")

    def build_headless_cmd(
        self,
        prompt: str,
        *,
        model: str | None = None,
        env_extras: Mapping[str, str] | None = None,
        base: Mapping[str, str] | None = None,
    ) -> CmdSpec:
        from autoskillit.execution.commands import build_headless_cmd as _build

        spec = _build(prompt, model=model, env_extras=env_extras, base=base)
        return CmdSpec(cmd=tuple(spec.cmd), env=spec.env)

    def build_interactive_cmd(
        self,
        *,
        initial_prompt: str | None = None,
        model: str | None = None,
        plugin_source: PluginSource | None = None,
        add_dirs: Sequence[Path | str | ValidatedAddDir] = (),
        resume_spec: ResumeSpec = NoResume(),
        env_extras: Mapping[str, str] | None = None,
        required_env: frozenset[str] | None = None,
    ) -> CmdSpec:
        from autoskillit.execution.commands import build_interactive_cmd as _build

        spec = _build(
            initial_prompt=initial_prompt,
            model=model,
            plugin_source=plugin_source,
            add_dirs=add_dirs,
            resume_spec=resume_spec,
            env_extras=env_extras,
            required_env=required_env,
        )
        return CmdSpec(cmd=tuple(spec.cmd), env=spec.env)

    def build_resume_cmd(
        self,
        *,
        resume_session_id: str,
        prompt: str,
        output_format: OutputFormat = OutputFormat.JSON,
        plugin_source: PluginSource | None = None,
        env_extras: Mapping[str, str] | None = None,
    ) -> CmdSpec:
        from autoskillit.execution.commands import build_headless_resume_cmd as _build

        spec = _build(
            resume_session_id=resume_session_id,
            prompt=prompt,
            output_format=output_format,
            plugin_source=plugin_source,
            env_extras=env_extras,
        )
        return CmdSpec(cmd=tuple(spec.cmd), env=spec.env)
