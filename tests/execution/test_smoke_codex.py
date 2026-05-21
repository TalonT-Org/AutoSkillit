"""Codex CLI smoke test: gated E2E validation of codex exec NDJSON output.

Gated E2E tests run only when CODEX_SMOKE_TEST=1 and OPENAI_API_KEY are set
(via ``task test-smoke-codex``).
"""

from __future__ import annotations

import os
import subprocess

import pytest

from autoskillit.core import BackendEventKind, SessionEvent
from autoskillit.execution.backends.codex import CodexResultParser, CodexStreamParser

pytestmark = [pytest.mark.layer("execution"), pytest.mark.large]

_SKIP_REASON = "Set CODEX_SMOKE_TEST=1 and OPENAI_API_KEY to run Codex smoke tests"

_skip_unless_codex_smoke = pytest.mark.skipif(
    not os.environ.get("CODEX_SMOKE_TEST") or not os.environ.get("OPENAI_API_KEY"),
    reason=_SKIP_REASON,
)


@_skip_unless_codex_smoke
@pytest.mark.smoke
class TestCodexSmokeExecution:
    """Full end-to-end Codex CLI smoke execution.

    Run via ``task test-smoke-codex`` which sets CODEX_SMOKE_TEST=1 and
    requires OPENAI_API_KEY. Executes ``codex exec --json`` with a trivial
    prompt and validates NDJSON output parsing.
    """

    def test_codex_exec_ndjson_parseable(self) -> None:
        result = subprocess.run(
            [
                "codex",
                "exec",
                "--json",
                "--sandbox",
                "workspace-write",
                "-a",
                "never",
                "Respond with exactly: hello",
            ],
            capture_output=True,
            text=True,
            timeout=30,
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
