"""Live Codex CLI conformance probes — CODEX_SMOKE_TEST-gated.

Wires real ``codex exec --json`` output through the shared
``_conformance_assertions.py`` assertion helpers. Each probe checks
``ProbeCache`` before invoking the CLI, delegates to assertion functions,
discriminates OSError/TimeoutExpired (network) from AssertionError (schema),
and records results via ``CanaryState`` + ``CanaryIssueUpdater``.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

import pytest

from autoskillit._probe_canary import (
    CanaryIssueUpdater,
    CanaryState,
    ErrorKind,
)
from autoskillit.execution.backends._probe_cache import (
    ProbeResult,
    read_probe_cache,
    write_probe_cache,
)
from tests.execution.backends._conformance_assertions import (
    assert_config_schema,
    assert_hook_event_format,
    assert_no_unknown_event_types,
    assert_session_start_present,
    assert_turn_completed_usage_nonzero,
    assert_vocabulary_coverage,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.large, pytest.mark.smoke]

_SKIP_REASON = (
    "Set CODEX_SMOKE_TEST=1 and one of: CODEX_API_KEY, OPENAI_API_KEY,"
    " or ~/.codex/auth.json to run Codex smoke tests"
)

_skip_unless_codex_smoke = pytest.mark.skipif(
    not os.environ.get("CODEX_SMOKE_TEST")
    or (
        not os.environ.get("CODEX_API_KEY")
        and not os.environ.get("OPENAI_API_KEY")
        and not Path("~/.codex/auth.json").expanduser().exists()
    ),
    reason=_SKIP_REASON,
)

_PROBE_BACKEND = "codex"
_CANARY_TITLE_PREFIX = "[Canary] codex conformance probe"


class _CodexProbeOutput(NamedTuple):
    events: list[dict]
    config_dict: dict
    cli_version: str


def _get_codex_version() -> str:
    try:
        result = subprocess.run(
            ["codex", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip() or "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


def _run_codex_probe() -> _CodexProbeOutput:
    timeout = int(os.environ.get("CODEX_SMOKE_TIMEOUT", "60"))
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
        timeout=timeout,
    )
    if result.returncode != 0:
        msg = f"codex exec failed with rc={result.returncode}: {result.stderr}"
        raise OSError(msg)

    events: list[dict] = []
    for line in result.stdout.splitlines():
        try:
            events.append(json.loads(line))
        except (json.JSONDecodeError, ValueError):
            continue

    config_dict: dict = {}
    for evt in events:
        if evt.get("type") == "session_configuration":
            config_dict = evt.get("configuration", evt)
            break

    return _CodexProbeOutput(
        events=events,
        config_dict=config_dict,
        cli_version=_get_codex_version(),
    )


def _make_canary_body(probe_name: str, kind: ErrorKind, cli_version: str, detail: str) -> str:
    return (
        f"**Probe:** {probe_name}\n"
        f"**Backend:** {_PROBE_BACKEND}\n"
        f"**CLI Version:** {cli_version}\n"
        f"**Failure Type:** {kind.value}\n"
        f"**Detail:** {detail}\n"
    )


@_skip_unless_codex_smoke
class TestCodexLiveProbes:
    """Live Codex CLI conformance probes.

    Each probe: cache check → subprocess → assertion delegation →
    error discrimination → canary recording.
    """

    @pytest.fixture(autouse=True)
    def _probe_state(self, tmp_path: Path) -> tuple[Path, Path]:
        self._cache_path = tmp_path / "probe_cache.json"
        self._state_path = tmp_path / "canary_state.json"
        return self._cache_path, self._state_path

    def _check_cache(self) -> None:
        cli_version = _get_codex_version()
        cached = read_probe_cache(self._cache_path, cli_version)
        if cached is not None and cached.passed:
            pytest.skip(f"Probe cached as passed for {cli_version}")

    def _record_success(self, cli_version: str) -> None:
        state = CanaryState.load(self._state_path)
        state.record_success()
        state.save(self._state_path)
        write_probe_cache(
            self._cache_path,
            ProbeResult(
                cli_version=cli_version,
                passed=True,
                failure_detail=None,
                probe_timestamp=datetime.now(UTC).isoformat(),
            ),
        )

    def _record_failure(
        self, kind: ErrorKind, probe_name: str, cli_version: str, detail: str
    ) -> None:
        state = CanaryState.load(self._state_path)
        state.record_failure(kind)
        if state.should_report():
            repo_slug = os.environ.get("GITHUB_REPOSITORY", "")
            if repo_slug and "/" in repo_slug:
                owner, repo = repo_slug.split("/", 1)
                updater = CanaryIssueUpdater(owner=owner, repo=repo)
                title = f"{_CANARY_TITLE_PREFIX}: {probe_name}"
                body = _make_canary_body(probe_name, kind, cli_version, detail)
                updater.ensure_issue(state, title, body)
        state.save(self._state_path)
        write_probe_cache(
            self._cache_path,
            ProbeResult(
                cli_version=cli_version,
                passed=False,
                failure_detail=detail,
                probe_timestamp=datetime.now(UTC).isoformat(),
            ),
        )

    def _run_probe_with_discrimination(
        self, probe_name: str, probe_output: _CodexProbeOutput, assertion_fn
    ) -> None:
        try:
            assertion_fn(probe_output)
            self._record_success(probe_output.cli_version)
        except AssertionError as exc:
            self._record_failure(ErrorKind.SCHEMA, probe_name, probe_output.cli_version, str(exc))
            raise
        except (OSError, subprocess.TimeoutExpired) as exc:
            self._record_failure(ErrorKind.NETWORK, probe_name, probe_output.cli_version, str(exc))
            raise

    _cls_probe_output: _CodexProbeOutput | None = None

    @classmethod
    def _get_probe_output(cls) -> _CodexProbeOutput:
        if cls._cls_probe_output is None:
            cls._cls_probe_output = _run_codex_probe()
        return cls._cls_probe_output

    def test_ndjson_event_vocabulary_conforms(self) -> None:
        self._check_cache()
        probe_output = self._get_probe_output()

        def _assert(output: _CodexProbeOutput) -> None:
            assert_no_unknown_event_types(output.events)
            assert_session_start_present(output.events)
            assert_turn_completed_usage_nonzero(output.events)
            assert_vocabulary_coverage(output.events, {"thread.started", "turn.completed"})

        self._run_probe_with_discrimination("ndjson_event_vocabulary", probe_output, _assert)

    def test_hook_firing_codex_status(self) -> None:
        self._check_cache()
        probe_output = self._get_probe_output()

        def _assert(output: _CodexProbeOutput) -> None:
            if not output.config_dict:
                pytest.skip("No session_configuration event in NDJSON output")
            assert_hook_event_format(output.config_dict)

        self._run_probe_with_discrimination("hook_firing_codex_status", probe_output, _assert)

    def test_config_acceptance(self) -> None:
        self._check_cache()
        probe_output = self._get_probe_output()

        def _assert(output: _CodexProbeOutput) -> None:
            if not output.config_dict:
                pytest.skip("No session_configuration event in NDJSON output")
            assert_config_schema(output.config_dict, output.cli_version)

        self._run_probe_with_discrimination("config_acceptance", probe_output, _assert)
