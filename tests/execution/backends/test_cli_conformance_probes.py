"""Live backend CLI conformance probes — backend smoke-test gated.

Wires real CLI output through shared assertion helpers. Each probe checks
``ProbeCache`` before invoking the CLI, delegates to assertion functions, and
discriminates OSError/TimeoutExpired (network) from AssertionError (schema).
The original Codex schema probes also record ``CanaryState`` issue updates.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple, Protocol, TypeVar

import pytest

from autoskillit._probe_canary import (
    CanaryIssueUpdater,
    CanaryState,
    ErrorKind,
)
from autoskillit.execution.backends._codex_hooks import sync_hooks_to_codex_config
from autoskillit.execution.backends._probe_cache import (
    PROBE_POLICY_IDENTITY,
    ProbeResult,
    read_probe_cache,
    write_probe_cache,
)
from autoskillit.hook_registry import generate_hooks_json
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

_CLAUDE_CODE_SKIP_REASON = (
    "Set CLAUDE_CODE_SMOKE_TEST=1 and have 'claude' on PATH to run Claude Code smoke tests"
)

_skip_unless_claude_code_smoke = pytest.mark.skipif(
    not os.environ.get("CLAUDE_CODE_SMOKE_TEST") or not shutil.which("claude"),
    reason=_CLAUDE_CODE_SKIP_REASON,
)

_skip_unless_codex_config_parse_probe = pytest.mark.skipif(
    not os.environ.get("CODEX_CONFIG_PARSE_PROBE") or not shutil.which("codex"),
    reason="Set CODEX_CONFIG_PARSE_PROBE=1 and have 'codex' on PATH to run the config probe",
)

_PROBE_BACKEND = "codex"
_CANARY_TITLE_PREFIX = "[Canary] codex conformance probe"


@_skip_unless_codex_config_parse_probe
def test_installed_codex_parses_multiline_developer_instructions(tmp_path: Path) -> None:
    """Guard the exact interactive ``-c`` TOML value accepted by installed Codex."""
    from autoskillit.core import OUTPUT_DISCIPLINE_DIGEST
    from autoskillit.execution.backends._codex_config import _format_toml_value

    caller_prompt = 'caller "prompt"\nwith a second line and \\ path'
    combined = f"{caller_prompt}\n\n{OUTPUT_DISCIPLINE_DIGEST}"
    override = f"developer_instructions={_format_toml_value(combined)}"
    env = dict(os.environ)
    env["CODEX_HOME"] = str(tmp_path / "codex-home")
    Path(env["CODEX_HOME"]).mkdir()

    result = subprocess.run(  # noqa: S603
        ["codex", "-c", override, "doctor", "--json"],
        capture_output=True,
        text=True,
        timeout=20,
        env=env,
    )

    assert result.stdout, result.stderr
    config_check = json.loads(result.stdout)["checks"]["config.load"]
    assert config_check["status"] == "ok", config_check


class _CodexProbeOutput(NamedTuple):
    events: list[dict]
    config_dict: dict
    cli_version: str


class _VersionedProbeOutput(Protocol):
    cli_version: str


_ProbeOutputT = TypeVar("_ProbeOutputT", bound=_VersionedProbeOutput)


def _run_probe_with_discrimination(
    probe_name: str,
    cli_version: str,
    probe_fn: Callable[[], _ProbeOutputT],
    assertion_fn: Callable[[_ProbeOutputT], None],
    *,
    record_success: Callable[[str], None],
    record_failure: Callable[[ErrorKind, str, str, str], None],
) -> None:
    """Run one live probe and distinguish transport failures from contract drift."""
    try:
        probe_output = probe_fn()
    except (OSError, subprocess.TimeoutExpired) as exc:
        record_failure(ErrorKind.NETWORK, probe_name, cli_version, str(exc))
        raise

    try:
        assertion_fn(probe_output)
    except AssertionError as exc:
        record_failure(ErrorKind.SCHEMA, probe_name, probe_output.cli_version, str(exc))
        raise
    record_success(probe_output.cli_version)


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

    _cls_state_dir: Path | None = None

    @pytest.fixture(autouse=True, scope="class")
    def _probe_class_state_dir(self, tmp_path_factory: pytest.TempPathFactory) -> None:
        type(self)._cls_state_dir = tmp_path_factory.mktemp("canary_state")

    @pytest.fixture(autouse=True)
    def _probe_state(self, tmp_path: Path) -> None:
        self._cache_path = tmp_path / "probe_cache.json"
        cls_dir = type(self)._cls_state_dir
        self._state_path = (cls_dir if cls_dir is not None else tmp_path) / "canary_state.json"

    def _check_cache(self) -> None:
        cli_version = _get_codex_version()
        cached = read_probe_cache(self._cache_path, cli_version, PROBE_POLICY_IDENTITY)
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
                policy_identity=PROBE_POLICY_IDENTITY,
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
        state.save(self._state_path)
        if state.should_report():
            repo_slug = os.environ.get("GITHUB_REPOSITORY", "")
            if repo_slug and "/" in repo_slug:
                owner, repo = repo_slug.split("/", 1)
                updater = CanaryIssueUpdater(owner=owner, repo=repo)
                title = f"{_CANARY_TITLE_PREFIX}: {probe_name}"
                body = _make_canary_body(probe_name, kind, cli_version, detail)
                updater.ensure_issue(state, title, body)
        write_probe_cache(
            self._cache_path,
            ProbeResult(
                cli_version=cli_version,
                policy_identity=PROBE_POLICY_IDENTITY,
                passed=False,
                failure_detail=detail,
                probe_timestamp=datetime.now(UTC).isoformat(),
            ),
        )

    _cls_probe_output: _CodexProbeOutput | None = None
    _cls_probe_exc: BaseException | None = None

    @classmethod
    def _get_probe_output(cls) -> _CodexProbeOutput:
        if cls._cls_probe_exc is not None:
            raise cls._cls_probe_exc
        if cls._cls_probe_output is None:
            try:
                cls._cls_probe_output = _run_codex_probe()
            except BaseException as exc:
                cls._cls_probe_exc = exc
                raise
        return cls._cls_probe_output

    def test_ndjson_event_vocabulary_conforms(self) -> None:
        self._check_cache()

        def _assert(output: _CodexProbeOutput) -> None:
            assert_no_unknown_event_types(output.events)
            assert_session_start_present(output.events)
            assert_turn_completed_usage_nonzero(output.events)
            assert_vocabulary_coverage(output.events, {"thread.started", "turn.completed"})

        _run_probe_with_discrimination(
            "ndjson_event_vocabulary",
            _get_codex_version(),
            self._get_probe_output,
            _assert,
            record_success=self._record_success,
            record_failure=self._record_failure,
        )

    def test_hook_firing_codex_status(self) -> None:
        self._check_cache()

        def _assert(output: _CodexProbeOutput) -> None:
            if not output.config_dict:
                pytest.skip("No session_configuration event in NDJSON output")
            assert_hook_event_format(output.config_dict)

        _run_probe_with_discrimination(
            "hook_firing_codex_status",
            _get_codex_version(),
            self._get_probe_output,
            _assert,
            record_success=self._record_success,
            record_failure=self._record_failure,
        )

    def test_config_acceptance(self) -> None:
        self._check_cache()

        def _assert(output: _CodexProbeOutput) -> None:
            if not output.config_dict:
                pytest.skip("No session_configuration event in NDJSON output")
            assert_config_schema(output.config_dict, output.cli_version)

        _run_probe_with_discrimination(
            "config_acceptance",
            _get_codex_version(),
            self._get_probe_output,
            _assert,
            record_success=self._record_success,
            record_failure=self._record_failure,
        )


_OUTPUT_BUDGET_CANARY_COMMAND = "rg -n output_budget_probe ."
_OUTPUT_BUDGET_CANARY_PROMPT = (
    "This is a hook conformance probe. Use the shell tool exactly once to run "
    f"`{_OUTPUT_BUDGET_CANARY_COMMAND}` without changing or bounding it. "
    "After the tool is denied, repeat the complete denial reason verbatim and stop. "
    "Do not run any other tool."
)

_skip_unless_codex_output_budget_smoke = pytest.mark.skipif(
    not os.environ.get("CODEX_SMOKE_TEST")
    or not shutil.which("codex")
    or (not os.environ.get("CODEX_API_KEY") and not os.environ.get("OPENAI_API_KEY")),
    reason=(
        "Set CODEX_SMOKE_TEST=1 and CODEX_API_KEY or OPENAI_API_KEY to run the "
        "isolated Codex output-budget hook probe"
    ),
)

_skip_unless_claude_output_budget_smoke = pytest.mark.skipif(
    not os.environ.get("CLAUDE_CODE_SMOKE_TEST")
    or not shutil.which("claude")
    or (not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")),
    reason=(
        "Set CLAUDE_CODE_SMOKE_TEST=1 and an environment-provided Claude credential "
        "to run the isolated Claude Code output-budget hook probe"
    ),
)


class _DenyRoundTripOutput(NamedTuple):
    transcript: str
    cli_version: str


def _isolated_cli_env(tmp_path: Path, workspace: Path) -> tuple[dict[str, str], Path, Path]:
    home = tmp_path / "home"
    codex_home = tmp_path / "codex-home"
    claude_config = tmp_path / "claude-config"
    for directory in (home, codex_home, claude_config):
        directory.mkdir(parents=True)

    env = dict(os.environ)
    env.update(
        {
            "HOME": str(home),
            "CODEX_HOME": str(codex_home),
            "CLAUDE_CONFIG_DIR": str(claude_config),
            "XDG_CONFIG_HOME": str(home / ".config"),
            "XDG_DATA_HOME": str(home / ".local" / "share"),
            "AUTOSKILLIT_CWD": str(workspace),
            "AUTOSKILLIT_ALLOWED_WRITE_PREFIXES": str(workspace),
        }
    )
    return env, codex_home, claude_config


def _cli_version(binary: str, env: dict[str, str]) -> str:
    try:
        result = subprocess.run(  # noqa: S603
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
        return result.stdout.strip() or result.stderr.strip() or "unknown"
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"


def _run_output_budget_deny_probe(backend: str, tmp_path: Path) -> _DenyRoundTripOutput:
    tmp_path.mkdir(parents=True, exist_ok=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    env, codex_home, claude_config = _isolated_cli_env(tmp_path, workspace)

    if backend == "codex":
        config_path = codex_home / "config.toml"
        sync_hooks_to_codex_config(config_path=config_path)
        init_result = subprocess.run(  # noqa: S603
            ["git", "init", "-q"],
            cwd=workspace,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if init_result.returncode != 0:
            raise OSError(f"isolated git init failed: {init_result.stderr}")
        command = [
            "codex",
            "exec",
            "--json",
            "--sandbox",
            "workspace-write",
            _OUTPUT_BUDGET_CANARY_PROMPT,
        ]
    elif backend == "claude-code":
        settings_path = claude_config / "settings.json"
        settings_path.write_text(
            json.dumps(generate_hooks_json(), indent=2) + "\n",
            encoding="utf-8",
        )
        command = [
            "claude",
            "-p",
            _OUTPUT_BUDGET_CANARY_PROMPT,
            "--output-format",
            "stream-json",
            "--verbose",
            "--dangerously-skip-permissions",
            "--tools",
            "Bash",
        ]
    else:  # pragma: no cover - callers pass a sealed backend literal
        raise ValueError(f"unsupported probe backend: {backend}")

    timeout = int(os.environ.get("OUTPUT_BUDGET_HOOK_SMOKE_TIMEOUT", "120"))
    result = subprocess.run(  # noqa: S603
        command,
        cwd=workspace,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    transcript = result.stdout + "\n" + result.stderr
    if result.returncode != 0:
        raise OSError(f"{backend} deny probe failed with rc={result.returncode}: {transcript}")
    return _DenyRoundTripOutput(
        transcript=transcript,
        cli_version=_cli_version(command[0], env),
    )


def _assert_output_budget_deny_round_trip(output: _DenyRoundTripOutput) -> None:
    assert _OUTPUT_BUDGET_CANARY_COMMAND in output.transcript
    assert "rg -l" in output.transcript
    assert "head -c 4000" in output.transcript
    assert ".autoskillit/temp/" in output.transcript


def _exercise_output_budget_deny_probe(backend: str, tmp_path: Path) -> None:
    workspace = tmp_path / "version-workspace"
    workspace.mkdir()
    version_env, _, _ = _isolated_cli_env(tmp_path / "version-env", workspace)
    binary = "codex" if backend == "codex" else "claude"
    cli_version = _cli_version(binary, version_env)
    cache_path = tmp_path / f"{backend}-output-budget-probe-cache.json"
    cached = read_probe_cache(cache_path, cli_version, PROBE_POLICY_IDENTITY)
    if cached is not None and cached.passed:
        pytest.skip(f"Output-budget deny probe cached as passed for {cli_version}")

    def _record(passed: bool, version: str, detail: str | None) -> None:
        write_probe_cache(
            cache_path,
            ProbeResult(
                cli_version=version,
                policy_identity=PROBE_POLICY_IDENTITY,
                passed=passed,
                failure_detail=detail,
                probe_timestamp=datetime.now(UTC).isoformat(),
            ),
        )

    def _record_success(version: str) -> None:
        _record(True, version, None)

    def _record_failure(
        kind: ErrorKind,
        probe_name: str,
        version: str,
        detail: str,
    ) -> None:
        _record(False, version, f"{kind.value}:{probe_name}:{detail}")

    _run_probe_with_discrimination(
        f"output_budget_deny_round_trip_{backend}",
        cli_version,
        lambda: _run_output_budget_deny_probe(backend, tmp_path / "round-trip"),
        _assert_output_budget_deny_round_trip,
        record_success=_record_success,
        record_failure=_record_failure,
    )


@_skip_unless_codex_output_budget_smoke
class TestCodexOutputBudgetDenyRoundTrip:
    def test_hook_fires_and_reason_reaches_model(self, tmp_path: Path) -> None:
        _exercise_output_budget_deny_probe("codex", tmp_path)


@_skip_unless_claude_output_budget_smoke
class TestClaudeCodeOutputBudgetDenyRoundTrip:
    def test_hook_fires_and_reason_reaches_model(self, tmp_path: Path) -> None:
        _exercise_output_budget_deny_probe("claude-code", tmp_path)


@_skip_unless_claude_code_smoke
class TestClaudeCodeOutputSchemaProbe:
    """Retirement signal for ClaudeCodeCompatMiddleware (_wire_compat.py).

    Claude Code bug anthropics/claude-code#25081 silently drops ALL tools
    from any tools/list response containing ``outputSchema``.
    ClaudeCodeCompatMiddleware strips the field as a workaround.

    This probe constructs a standalone FastMCP server with an
    output_schema-bearing tool and verifies list_tools() returns it.
    Once Claude Code fixes #25081 and this probe passes against the
    real CLI, the middleware can be retired.
    """

    @pytest.mark.anyio
    async def test_output_schema_tool_list_not_dropped(self) -> None:
        from fastmcp import FastMCP
        from fastmcp.client import Client

        server = FastMCP("probe")

        @server.tool(
            output_schema={
                "type": "object",
                "properties": {"result": {"type": "string"}},
            }
        )
        def echo(x: str) -> str:
            return x

        async with Client(server) as client:
            tools = await client.list_tools()

        assert any(t.name == "echo" for t in tools)
