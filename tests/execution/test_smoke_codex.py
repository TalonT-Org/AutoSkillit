"""Codex CLI smoke test: gated E2E validation of codex exec NDJSON output.

Gated E2E tests run only when CODEX_SMOKE_TEST=1 and one of: CODEX_API_KEY env var,
OPENAI_API_KEY env var, or ~/.codex/auth.json (CLI-managed auth) are set
(via ``task test-smoke-codex``).
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import NamedTuple

import pytest

from autoskillit.core import BackendEventKind, DirectInstall, SessionEvent
from autoskillit.core.types import Severity
from autoskillit.execution.backends import CompositeSessionLocator
from autoskillit.execution.backends.codex import (
    CodexBackend,
    CodexResultParser,
    CodexStreamParser,
)
from autoskillit.recipe._api import load_and_validate

pytestmark = [pytest.mark.layer("execution"), pytest.mark.large]

_SKIP_REASON = (
    "Set CODEX_SMOKE_TEST=1 and one of: CODEX_API_KEY, OPENAI_API_KEY,"
    " or ~/.codex/auth.json to run Codex smoke tests"
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class _CodexSessionData(NamedTuple):
    result: subprocess.CompletedProcess
    events: list[SessionEvent]
    thread_id: str


_skip_unless_codex_smoke = pytest.mark.skipif(
    not os.environ.get("CODEX_SMOKE_TEST")
    or (
        not os.environ.get("CODEX_API_KEY")
        and not os.environ.get("OPENAI_API_KEY")
        and not Path("~/.codex/auth.json").expanduser().exists()
    ),
    reason=_SKIP_REASON,
)


@_skip_unless_codex_smoke
@pytest.mark.smoke
class TestCodexSmokeExecution:
    """Full end-to-end Codex CLI smoke execution.

    Run via ``task test-smoke-codex`` which sets CODEX_SMOKE_TEST=1 and
    requires one of: CODEX_API_KEY, OPENAI_API_KEY, or ~/.codex/auth.json.
    Executes ``codex exec --json`` with a trivial prompt and validates
    NDJSON output parsing.
    """

    def test_codex_exec_ndjson_parseable(self) -> None:
        result = subprocess.run(
            [
                "codex",
                "exec",
                "--json",
                "--sandbox",
                "workspace-write",
                "Respond with exactly: hello",
            ],
            capture_output=True,
            text=True,
            timeout=int(os.environ.get("CODEX_SMOKE_TIMEOUT", "30")),
        )
        assert result.returncode == 0, (
            f"codex exec failed with rc={result.returncode}: {result.stderr}"
        )

        # Parse individual NDJSON lines with CodexStreamParser
        parser = CodexStreamParser()
        events: list[SessionEvent] = []
        for line in result.stdout.splitlines():
            evt = parser.parse_line(line)
            if evt is not None:
                events.append(evt)

        completion_events = [e for e in events if e.kind == BackendEventKind.COMPLETION]
        assert len(completion_events) >= 1, (
            f"Expected at least one COMPLETION event, got kinds: {[e.kind for e in events]}"
        )

        # Parse full stdout with CodexResultParser
        result_parser = CodexResultParser()
        agent_result = result_parser.parse_stdout(result.stdout, exit_code=result.returncode)
        token_usage = agent_result.raw.get("token_usage")
        assert token_usage is not None, (
            f"token_usage is None; raw keys: {list(agent_result.raw.keys())}"
        )
        assert token_usage.get("input_tokens", -1) >= 0
        assert token_usage.get("output_tokens", -1) >= 0
        assert agent_result.output, "Codex session produced no output — possible schema drift"


@_skip_unless_codex_smoke
@pytest.mark.smoke
class TestCodexSmokeInteractiveCmdBuild:
    """Verify CodexBackend.build_interactive_cmd produces a valid CmdSpec."""

    def test_interactive_cmd_builds_without_error(self) -> None:
        cmd = CodexBackend().build_interactive_cmd(initial_prompt="Hello")
        assert cmd.cmd[0] == "codex"
        assert "--dangerously-bypass-approvals-and-sandbox" in cmd.cmd


@_skip_unless_codex_smoke
@pytest.mark.smoke
class TestCodexSmokeFoodTruckCmdBuild:
    """Verify CodexBackend.build_food_truck_cmd produces a valid CmdSpec."""

    def test_food_truck_cmd_has_required_flags(self) -> None:
        plugin_source = DirectInstall(plugin_dir=Path("/tmp/fake-plugin"))
        cmd = CodexBackend().build_food_truck_cmd(
            orchestrator_prompt="test",
            plugin_source=plugin_source,
            cwd="/tmp",
            completion_marker="DONE",
        )
        assert "--json" in cmd.cmd
        assert "--sandbox" in cmd.cmd
        assert cmd.cmd[cmd.cmd.index("--sandbox") + 1] == "read-only"


@_skip_unless_codex_smoke
@pytest.mark.smoke
class TestCodexSmokeRecipeComposition:
    """Recipe composition validity and session diagnostics under codex backend.

    Tests 1-2 are pure recipe validation (no codex CLI needed, but gated
    with the class for organizational grouping).
    Tests 3-5 use a shared single codex exec invocation.
    """

    @pytest.fixture(scope="class")
    def codex_session(self, tmp_path_factory):
        """Single codex exec invocation shared across smoke tests."""
        proc = subprocess.run(
            [
                "codex",
                "exec",
                "--json",
                "--sandbox",
                "workspace-write",
                "Respond with exactly: hello",
            ],
            capture_output=True,
            text=True,
            timeout=int(os.environ.get("CODEX_SMOKE_TIMEOUT", "30")),
        )
        parser = CodexStreamParser()
        events: list[SessionEvent] = []
        for line in proc.stdout.splitlines():
            evt = parser.parse_line(line)
            if evt is not None:
                events.append(evt)

        thread_id = ""
        for line in proc.stdout.splitlines():
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if obj.get("type") == "thread.started":
                thread_id = obj.get("thread_id", "")
                break
            if obj.get("type") == "session_meta":
                thread_id = obj.get("payload", {}).get("id", "")
                break

        tmp_dir = tmp_path_factory.mktemp("codex_smoke")
        rollout = tmp_dir / "codex-sessions" / "rollout.jsonl"
        rollout.parent.mkdir(parents=True)
        rollout.write_text(proc.stdout)

        return _CodexSessionData(result=proc, events=events, thread_id=thread_id), rollout

    def test_open_kitchen_implementation_valid(self) -> None:
        result = load_and_validate(
            "implementation",
            project_dir=_PROJECT_ROOT,
            backend_name="codex",
            ingredient_overrides={"backend_supports_git_write": "false"},
        )
        assert result["valid"] is True, "implementation recipe invalid on codex: " + "; ".join(
            f"[{s.get('rule')}] {s.get('message', '')[:80]}"
            for s in result.get("suggestions", [])
            if s.get("severity") == Severity.ERROR
        )
        assert len(result.get("content", "")) > 0
        backend_compat_errors = [
            s
            for s in result.get("suggestions", [])
            if s.get("rule") == "backend-incompatible-skill"
            and s.get("severity") == Severity.ERROR
        ]
        assert not backend_compat_errors

    def test_open_kitchen_planner_valid(self) -> None:
        result = load_and_validate(
            "planner",
            project_dir=_PROJECT_ROOT,
            backend_name="codex",
            ingredient_overrides={"backend_supports_git_write": "false"},
        )
        assert result["valid"] is True
        assert len(result.get("content", "")) > 0

    def test_reduced_codex_smoke_pipeline_no_refusals(self, codex_session) -> None:
        session_data, _rollout = codex_session
        assert session_data.result.returncode == 0, (
            f"codex exec failed with rc={session_data.result.returncode}: "
            f"{session_data.result.stderr}"
        )
        completion_events = [
            e for e in session_data.events if e.kind == BackendEventKind.COMPLETION
        ]
        assert len(completion_events) >= 1, (
            f"Expected at least one COMPLETION event, got kinds: "
            f"{[e.kind for e in session_data.events]}"
        )
        for line in session_data.result.stdout.splitlines():
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if obj.get("type") == "turn.failed":
                error = obj.get("error", {})
                pytest.fail(
                    f"Capability-gated refusal detected: "
                    f"{error.get('code', 'unknown')}: {error.get('message', '')}"
                )

    def test_session_diagnostics_field_completeness(self, codex_session, tmp_path) -> None:
        session_data, rollout = codex_session
        assert session_data.thread_id, "thread_id not resolved from codex NDJSON"

        from autoskillit.core.types._type_results import ProviderOutcome
        from autoskillit.core.types._type_results_execution import (
            RecipeIdentity,
            SessionTelemetry,
        )
        from autoskillit.execution.session_log import flush_session_log

        class _FakeLocator:
            def __init__(self, path):
                self._path = path

            def locate_session(self, session_id):
                return self._path

            def project_log_dir(self, cwd):
                return Path(cwd)

            def session_log_path(self, cwd, session_id):
                return self._path

        flush_session_log(
            log_dir=str(tmp_path),
            backend="codex",
            channel_b_capable=False,
            session_locator=_FakeLocator(rollout),
            cwd="/tmp/smoke",
            session_id=session_data.thread_id,
            pid=os.getpid(),
            skill_command="/autoskillit:implement",
            success=True,
            subtype="completed",
            exit_code=0,
            start_ts="2026-06-28T00:00:00",
            proc_snapshots=None,
            telemetry=SessionTelemetry.empty(),
            provider_outcome=ProviderOutcome.none_used(),
            recipe_identity=RecipeIdentity.empty(),
        )
        index_text = (tmp_path / "sessions.jsonl").read_text().strip()
        entry = json.loads(index_text)
        assert entry["session_id"] == session_data.thread_id
        assert entry["codex_log"] is not None
        assert entry["backend"] == "codex"
        assert entry["success"] is True

    def test_composite_locator_resolves_codex_session(self, codex_session, monkeypatch) -> None:
        session_data, rollout = codex_session
        assert session_data.thread_id, "thread_id not resolved"

        class _StubLocator:
            def __init__(self, path):
                self._path = path

            def locate_session(self, session_id):
                return self._path

            def project_log_dir(self, cwd):
                return Path("/stub")

            def session_log_path(self, cwd, session_id):
                return self._path

        def _fake_backend(locator):
            from unittest.mock import Mock

            backend = Mock()
            backend.session_locator.return_value = locator
            cls = Mock(return_value=backend)
            return cls

        import autoskillit.execution.backends as backends_mod

        monkeypatch.setattr(
            backends_mod,
            "BACKEND_REGISTRY",
            {"codex": _fake_backend(_StubLocator(rollout))},
        )
        result = CompositeSessionLocator().locate_session(session_data.thread_id)
        assert result is not None
        assert result.exists()
