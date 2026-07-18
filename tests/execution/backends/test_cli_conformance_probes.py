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
import tomllib
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
from autoskillit.config import OutputBudgetConfig
from autoskillit.core import (
    OUTPUT_DISCIPLINE_DIGEST,
    RESPONSE_BACKSTOP_EXEMPTION_REGISTRY,
    pkg_root,
)
from autoskillit.execution.backends._codex_config import (
    CODEX_TOOL_OUTPUT_TOKEN_LIMIT,
    ensure_codex_mcp_registered,
)
from autoskillit.execution.backends._codex_hooks import sync_hooks_to_codex_config
from autoskillit.execution.backends._probe_cache import (
    PROBE_POLICY_IDENTITY,
    ProbeResult,
    read_probe_cache,
    write_probe_cache,
)
from autoskillit.execution.backends.codex import CodexBackend
from autoskillit.hook_registry import generate_hooks_json
from tests.execution.backends._conformance_assertions import (
    assert_boundary_spill_behavior,
    assert_config_schema,
    assert_generated_child_delivery,
    assert_hook_event_format,
    assert_inline_within_byte_budget,
    assert_no_unknown_event_types,
    assert_sentinels_present,
    assert_session_start_present,
    assert_spill_artifact_integrity,
    assert_terminal_sentinel_preserved,
    assert_turn_completed_usage_nonzero,
    assert_vocabulary_coverage,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.large, pytest.mark.smoke]

_SKIP_REASON = (
    "Set CODEX_SMOKE_TEST=1 and one of: CODEX_API_KEY, OPENAI_API_KEY,"
    " or ~/.codex/auth.json to run Codex smoke tests"
)
_CODEX_AUTH_PATH = Path("~/.codex/auth.json").expanduser()

_skip_unless_codex_smoke = pytest.mark.skipif(
    not os.environ.get("CODEX_SMOKE_TEST")
    or (
        not os.environ.get("CODEX_API_KEY")
        and not os.environ.get("OPENAI_API_KEY")
        and not _CODEX_AUTH_PATH.exists()
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

_PROBE_BACKEND = "codex"
_CANARY_TITLE_PREFIX = "[Canary] codex conformance probe"


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

_skip_unless_codex_generated_child_smoke = pytest.mark.skipif(
    not os.environ.get("CODEX_SMOKE_TEST")
    or not shutil.which("codex")
    or (
        not os.environ.get("CODEX_API_KEY")
        and not os.environ.get("OPENAI_API_KEY")
        and not _CODEX_AUTH_PATH.is_file()
    ),
    reason=(
        "Set CODEX_SMOKE_TEST=1 and provide an environment API key or authenticated "
        "~/.codex/auth.json to run the generated Codex child probe"
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


class _GeneratedChildProbeOutput(NamedTuple):
    parent_events: list[dict]
    child_events: list[dict]
    parent_id: str
    agent_role: str
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


def _read_ndjson(path: Path) -> list[dict]:
    events: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _run_generated_child_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> _GeneratedChildProbeOutput:
    source_auth = _CODEX_AUTH_PATH
    profile_home = tmp_path / "profile-home"
    profile_codex_home = profile_home / ".codex"
    session_home = tmp_path / "session-home"
    workspace = tmp_path / "workspace"
    for directory in (profile_codex_home, session_home, workspace):
        directory.mkdir(parents=True)
    if source_auth.is_file():
        (profile_codex_home / "auth.json").symlink_to(source_auth.resolve())

    monkeypatch.setenv("HOME", str(profile_home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: profile_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(profile_home / ".config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(profile_home / ".local" / "share"))
    profile_config = profile_codex_home / "config.toml"
    ensure_codex_mcp_registered(config_path=profile_config, headless_auto_gate=False)
    sync_hooks_to_codex_config(config_path=profile_config)
    CodexBackend().setup_session_dir(session_home)

    agent_role = "wp-elaborator"
    agent_toml = session_home / "agents" / f"{agent_role}.toml"
    assert agent_toml.is_file(), f"generated role missing: {agent_toml}"
    agent_definition = tomllib.loads(agent_toml.read_text(encoding="utf-8"))
    assert OUTPUT_DISCIPLINE_DIGEST in agent_definition["developer_instructions"]
    session_config = tomllib.loads((session_home / "config.toml").read_text(encoding="utf-8"))
    assert session_config["agents"][agent_role]["config_file"] == (f"agents/{agent_role}.toml")

    env = dict(os.environ)
    env.update(
        {
            "HOME": str(profile_home),
            "CODEX_HOME": str(session_home),
            "XDG_CONFIG_HOME": str(profile_home / ".config"),
            "XDG_DATA_HOME": str(profile_home / ".local" / "share"),
        }
    )
    subprocess.run(
        ["git", "init", "-q"],
        cwd=workspace,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    prompt = (
        "This is a generated-subagent delivery probe. Call spawn_agent exactly once with "
        f'agent_type="{agent_role}", fork_context=false, and a message asking the child '
        "to reply exactly child-delivery-complete without using tools. Then call wait_agent "
        "with only the returned agent id until it reports completed. Finally respond exactly "
        "parent-delivery-complete. You may call tool_search only to discover spawn_agent and "
        "wait_agent; do not call other tool types."
    )
    timeout = int(os.environ.get("GENERATED_CHILD_SMOKE_TIMEOUT", "900"))
    result = subprocess.run(  # noqa: S603
        [
            "codex",
            "exec",
            "--json",
            "--sandbox",
            "workspace-write",
            "--model",
            os.environ.get("GENERATED_CHILD_SMOKE_MODEL", "gpt-5.4"),
            prompt,
        ],
        cwd=workspace,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise OSError(
            f"generated child probe failed with rc={result.returncode}: "
            f"{result.stdout}\n{result.stderr}"
        )
    stdout_events = []
    for line in result.stdout.splitlines():
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(event, dict):
            stdout_events.append(event)
    parent_ids = [
        str(event.get("thread_id", ""))
        for event in stdout_events
        if event.get("type") == "thread.started" and event.get("thread_id")
    ]
    assert len(parent_ids) == 1, f"expected one parent thread, got {parent_ids}"
    parent_id = parent_ids[0]

    rollout_root = (session_home / "sessions").resolve()
    rollout_events = [_read_ndjson(path) for path in rollout_root.rglob("rollout-*.jsonl")]
    parent_events: list[dict] = []
    child_events: list[dict] = []
    for events in rollout_events:
        session_metas = [
            event.get("payload", {}) for event in events if event.get("type") == "session_meta"
        ]
        if any(meta.get("id") == parent_id for meta in session_metas):
            parent_events = events
        if any(
            (meta.get("forked_from_id") or meta.get("parent_thread_id")) == parent_id
            for meta in session_metas
        ):
            child_events.extend(events)
    assert parent_events, f"parent rollout not found for {parent_id} under {rollout_root}"
    return _GeneratedChildProbeOutput(
        parent_events=parent_events,
        child_events=child_events,
        parent_id=parent_id,
        agent_role=agent_role,
        cli_version=_cli_version("codex", env),
    )


def _assert_generated_child_probe(output: _GeneratedChildProbeOutput) -> None:
    assert_generated_child_delivery(
        output.parent_events,
        output.child_events,
        parent_id=output.parent_id,
        agent_role=output.agent_role,
        output_discipline_digest=OUTPUT_DISCIPLINE_DIGEST,
    )


@_skip_unless_codex_generated_child_smoke
def test_generated_codex_child_receives_output_discipline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "version-workspace"
    workspace.mkdir()
    version_env, _, _ = _isolated_cli_env(tmp_path / "version-env", workspace)
    cli_version = _cli_version("codex", version_env)
    _run_probe_with_discrimination(
        "generated_codex_child",
        cli_version,
        lambda: _run_generated_child_probe(tmp_path / "generated-child", monkeypatch),
        _assert_generated_child_probe,
        record_success=lambda _version: None,
        record_failure=lambda _kind, _name, _version, _detail: None,
    )


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


_SOURCE_SPILL_THRESHOLD = OutputBudgetConfig().inline_max_chars
_CODEX_HEURISTIC_BYTES = CODEX_TOOL_OUTPUT_TOKEN_LIMIT * 4
_SERIALIZED_ENVELOPE_SLACK_BYTES = 4096
_LARGE_OUTPUT_CASE_BYTES = tuple(
    sorted(
        {
            _SOURCE_SPILL_THRESHOLD - 1,
            _SOURCE_SPILL_THRESHOLD,
            _SOURCE_SPILL_THRESHOLD + 1,
            _CODEX_HEURISTIC_BYTES - 1,
            _CODEX_HEURISTIC_BYTES,
            _CODEX_HEURISTIC_BYTES + 1,
            500_000,
        }
    )
)
_OPEN_KITCHEN_TERMINAL_SENTINEL = "success=false: escalate_stop_no_ci, escalate_stop"
_TRANSPORT_TRUNCATION_MARKERS = (
    "[tool output truncated]",
    "[output truncated by transport]",
)


class _LargeOutputProbe(NamedTuple):
    transcript: str
    cli_version: str
    expected_payloads: dict[int, str]


def _large_payload(size: int) -> tuple[str, tuple[str, str, str]]:
    sentinels = (
        f"HEAD-SENTINEL::{size}",
        f"MIDDLE-SENTINEL::{size}",
        f"TAIL-SENTINEL::{size}",
    )
    fixed = sum(len(value) for value in sentinels) + 2
    assert fixed <= size
    filler = size - fixed
    before_middle = filler // 2
    after_middle = filler - before_middle
    payload = (
        sentinels[0]
        + "\n"
        + ("a" * before_middle)
        + sentinels[1]
        + ("z" * after_middle)
        + "\n"
        + sentinels[2]
    )
    assert len(payload.encode("utf-8")) == size
    return payload, sentinels


def _walk_json_values(value: object):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json_values(child)
    elif isinstance(value, str):
        try:
            nested = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return
        yield from _walk_json_values(nested)


def _run_cmd_payloads(transcript: str) -> list[dict]:
    payloads: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for line in transcript.splitlines():
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        for candidate in _walk_json_values(event):
            if not {"success", "exit_code", "stdout", "stderr"}.issubset(candidate):
                continue
            key = (
                str(candidate.get("stdout", "")),
                str(candidate.get("stdout_artifact_path", "")),
            )
            if key not in seen:
                seen.add(key)
                payloads.append(candidate)
    return payloads


def _large_output_prompt(workspace: Path) -> str:
    calls = "\n".join(
        f'- run_cmd cmd="LC_ALL=C head -c {size} '
        f'.autoskillit/temp/probe-fixtures/case-{size}.txt" cwd="{workspace}"'
        for size in _LARGE_OUTPUT_CASE_BYTES
    )
    return (
        "This is an output-budget conformance probe. First call the autoskillit "
        "open_kitchen tool with name=remediation and overrides "
        '{"task":"test task","issue_url":"https://github.com/test/test/issues/1",'
        '"is_fleet_dispatch":"true","adversarial_review_level":"true",'
        '"local_review_rounds":"true","base_branch":"true",'
        '"post_run_diagnostics":"true"}. Then make every autoskillit run_cmd call below, '
        "in order. Do not substitute native shell tools and do not omit a call.\n"
        f"{calls}\nAfter all calls, respond with exactly: probe-complete"
    )


def _run_large_output_probe(backend: str, tmp_path: Path) -> _LargeOutputProbe:
    workspace = tmp_path / "workspace"
    fixture_dir = workspace / ".autoskillit" / "temp" / "probe-fixtures"
    fixture_dir.mkdir(parents=True)
    expected_payloads: dict[int, str] = {}
    for size in _LARGE_OUTPUT_CASE_BYTES:
        payload, _ = _large_payload(size)
        expected_payloads[size] = payload
        (fixture_dir / f"case-{size}.txt").write_text(payload, encoding="utf-8")

    env, codex_home, claude_config = _isolated_cli_env(tmp_path / "isolated", workspace)
    venv_bin = Path(__file__).resolve().parents[3] / ".venv" / "bin"
    env["PATH"] = f"{venv_bin}{os.pathsep}{env.get('PATH', '')}"
    env["AUTOSKILLIT_FEATURES__EXPERIMENTAL_ENABLED"] = "true"
    prompt = _large_output_prompt(workspace)

    if backend == "codex":
        config_path = codex_home / "config.toml"
        ensure_codex_mcp_registered(config_path=config_path, headless_auto_gate=False)
        sync_hooks_to_codex_config(config_path=config_path)
        subprocess.run(
            ["git", "init", "-q"],
            cwd=workspace,
            env=env,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        command = [
            "codex",
            "exec",
            "--json",
            "--sandbox",
            "workspace-write",
            prompt,
        ]
    elif backend == "claude-code":
        (claude_config / "settings.json").write_text(
            json.dumps(generate_hooks_json(), indent=2) + "\n",
            encoding="utf-8",
        )
        command = [
            "claude",
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            "--dangerously-skip-permissions",
            "--plugin-dir",
            str(pkg_root()),
        ]
    else:  # pragma: no cover - parametrization is sealed below
        raise ValueError(f"unsupported probe backend: {backend}")

    timeout = int(os.environ.get("OUTPUT_BUDGET_LARGE_SMOKE_TIMEOUT", "900"))
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
        raise OSError(f"{backend} large-output probe rc={result.returncode}: {transcript}")
    return _LargeOutputProbe(
        transcript=transcript,
        cli_version=_cli_version(command[0], env),
        expected_payloads=expected_payloads,
    )


def _assert_large_output_probe(output: _LargeOutputProbe) -> None:
    payloads = _run_cmd_payloads(output.transcript)
    observed_by_size: dict[int, dict] = {}
    for size, expected in output.expected_payloads.items():
        head = f"HEAD-SENTINEL::{size}"
        matches = [entry for entry in payloads if head in str(entry.get("stdout", ""))]
        assert len(matches) == 1, f"expected one run_cmd result for {size}, got {len(matches)}"
        observed_by_size[size] = matches[0]

    spill_by_size: dict[int, bool] = {}
    for size, entry in observed_by_size.items():
        expected = output.expected_payloads[size]
        _, sentinels = _large_payload(size)
        inline = str(entry["stdout"])
        artifact_path = str(entry.get("stdout_artifact_path", ""))
        spilled = bool(artifact_path)
        spill_by_size[size] = spilled
        assert_inline_within_byte_budget(
            json.dumps(entry),
            CODEX_TOOL_OUTPUT_TOKEN_LIMIT * 4,
            envelope_slack_bytes=_SERIALIZED_ENVELOPE_SLACK_BYTES,
        )
        if spilled:
            assert "[spilled " in inline
            assert_sentinels_present(inline, (sentinels[0], sentinels[2]))
            assert_spill_artifact_integrity(artifact_path, expected, sentinels)
        else:
            assert inline == expected
            assert_sentinels_present(inline, sentinels)

    assert_boundary_spill_behavior(spill_by_size, _SOURCE_SPILL_THRESHOLD)
    assert_terminal_sentinel_preserved(
        output.transcript,
        _OPEN_KITCHEN_TERMINAL_SENTINEL,
        _TRANSPORT_TRUNCATION_MARKERS,
    )
    assert len(_OPEN_KITCHEN_TERMINAL_SENTINEL.encode("utf-8")) < (
        RESPONSE_BACKSTOP_EXEMPTION_REGISTRY["open_kitchen"].max_utf8_bytes
    )


def _exercise_large_output_probe(backend: str, tmp_path: Path) -> None:
    version_workspace = tmp_path / "version-workspace"
    version_workspace.mkdir()
    version_env, _, _ = _isolated_cli_env(tmp_path / "version-env", version_workspace)
    binary = "codex" if backend == "codex" else "claude"
    cli_version = _cli_version(binary, version_env)
    cache_path = tmp_path / f"{backend}-large-output-probe-cache.json"
    cached = read_probe_cache(cache_path, cli_version, PROBE_POLICY_IDENTITY)
    if cached is not None and cached.passed:
        pytest.skip(f"Large-output probe cached as passed for {cli_version}")

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

    _run_probe_with_discrimination(
        f"large_output_boundaries_{backend}",
        cli_version,
        lambda: _run_large_output_probe(backend, tmp_path / "round-trip"),
        _assert_large_output_probe,
        record_success=lambda version: _record(True, version, None),
        record_failure=lambda kind, name, version, detail: _record(
            False, version, f"{kind.value}:{name}:{detail}"
        ),
    )


@_skip_unless_codex_output_budget_smoke
class TestCodexLargeOutputAndOpenKitchenRoundTrip:
    def test_boundaries_spill_integrity_and_terminal_sentinel(self, tmp_path: Path) -> None:
        _exercise_large_output_probe("codex", tmp_path)


@_skip_unless_claude_output_budget_smoke
class TestClaudeCodeLargeOutputAndOpenKitchenRoundTrip:
    def test_boundaries_spill_integrity_and_terminal_sentinel(self, tmp_path: Path) -> None:
        _exercise_large_output_probe("claude-code", tmp_path)


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
