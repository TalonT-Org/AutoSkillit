"""Live backend CLI conformance probes — backend smoke-test gated.

Wires real CLI output through shared assertion helpers. Each probe checks
``ProbeCache`` before invoking the CLI, delegates to assertion functions, and
discriminates OSError/TimeoutExpired (network) from AssertionError (schema).
The original Codex schema probes also record ``CanaryState`` issue updates.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import pty
import re
import select
import shlex
import shutil
import signal
import subprocess
import sys
import time
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
    CLAUDE_CODE_CAPABILITIES,
    OUTPUT_DISCIPLINE_DIGEST,
    RESPONSE_BACKSTOP_EXEMPTION_REGISTRY,
    pkg_root,
)
from autoskillit.execution.backends._codex_config import (
    CODEX_HISTORY_RETENTION_TOKEN_LIMIT,
    ensure_codex_mcp_registered,
)
from autoskillit.execution.backends._codex_hooks import sync_hooks_to_codex_config
from autoskillit.execution.backends._codex_parse import CodexResultParser, CodexStreamParser
from autoskillit.execution.backends._probe_cache import (
    PROBE_POLICY_IDENTITY,
    ProbeResult,
    read_probe_cache,
    write_probe_cache,
)
from autoskillit.execution.backends.codex import CodexBackend
from autoskillit.hook_registry import generate_hooks_json
from autoskillit.hooks._capture_artifacts import run_capture
from tests.execution.backends._conformance_assertions import (
    assert_boundary_spill_behavior,
    assert_config_schema,
    assert_generated_child_delivery,
    assert_hook_event_format,
    assert_inline_within_byte_budget,
    assert_no_unknown_event_types,
    assert_sentinels_present,
    assert_session_start_present,
    assert_shell_capture_marker_authority,
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

_CLAUDE_STARTUP_SKIP_REASON = (
    "Set CLAUDE_STARTUP_READINESS_SMOKE=1 with ANTHROPIC_API_KEY or "
    "CLAUDE_CODE_OAUTH_TOKEN to run the isolated interactive startup-readiness trace"
)
_skip_unless_claude_startup_smoke = pytest.mark.skipif(
    not os.environ.get("CLAUDE_STARTUP_READINESS_SMOKE")
    or not os.environ.get("CLAUDE_CODE_SMOKE_TEST")
    or not shutil.which("claude")
    or (not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")),
    reason=_CLAUDE_STARTUP_SKIP_REASON,
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
    "After the tool completes, stop without running any other tool."
)
_POLICY_DENIAL_MARKERS = (
    "blocked by policy",
    "not permitted",
    "policy_violation",
    "rejected:",
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

_skip_unless_codex_selection_smoke = pytest.mark.skipif(
    not os.environ.get("CODEX_SMOKE_TEST")
    or not shutil.which("codex")
    or (
        not os.environ.get("CODEX_API_KEY")
        and not os.environ.get("OPENAI_API_KEY")
        and not _CODEX_AUTH_PATH.is_file()
    ),
    reason=(
        "Set CODEX_SMOKE_TEST=1 and provide an environment API key or authenticated "
        "~/.codex/auth.json to run the Codex skill-selection probe"
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
    physical_project: Path | None = None


class _GeneratedChildProbeOutput(NamedTuple):
    parent_events: list[dict]
    child_events: list[dict]
    parent_id: str
    agent_role: str
    cli_version: str


class _CodexSelectionProbeOutput(NamedTuple):
    final_text: str
    completed_mcp_items: tuple[dict, ...]


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


def _prepare_codex_selection_profile(tmp_path: Path, workspace: Path) -> Path:
    _, profile_codex_home, _ = _isolated_cli_env(tmp_path / "source-profile", workspace)
    if _CODEX_AUTH_PATH.is_file():
        (profile_codex_home / "auth.json").symlink_to(_CODEX_AUTH_PATH.resolve())

    profile_config = profile_codex_home / "config.toml"
    ensure_codex_mcp_registered(config_path=profile_config, headless_auto_gate=False)
    sync_hooks_to_codex_config(config_path=profile_config)

    skill_dir = profile_codex_home / "skills" / "investigate"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: investigate\n"
        "description: Follow this skill when the user asks to investigate something.\n"
        "---\n"
        "Respond with exactly LOCAL_INVESTIGATE_SKILL_FOLLOWED and do not call tools.\n",
        encoding="utf-8",
    )
    return profile_codex_home


def _run_codex_selection_case(
    *,
    case_root: Path,
    source_codex_home: Path,
    workspace: Path,
    prompt: str,
    model: str,
) -> _CodexSelectionProbeOutput:
    env, case_codex_home, _ = _isolated_cli_env(case_root, workspace)
    case_sqlite_home = case_root / "codex-sqlite-home"
    case_sqlite_home.mkdir()

    env.update(
        {
            "CODEX_SQLITE_HOME": str(case_sqlite_home),
            "AUTOSKILLIT_AGENT_BACKEND": "codex",
            "AUTOSKILLIT_AGENT_BACKEND__BACKEND": "codex",
            "AUTOSKILLIT_MCP_CLIENT_BACKEND": "codex",
        }
    )
    for inherited_headless_flag in (
        "AUTOSKILLIT_HEADLESS",
        "AUTOSKILLIT_HEADLESS_AUTO_GATE",
        "AUTOSKILLIT_SESSION_TYPE",
        "AUTOSKILLIT_SKILL_NAME",
    ):
        env.pop(inherited_headless_flag, None)

    backend = CodexBackend(source_codex_home=source_codex_home)
    pre_launch_errors = backend.ensure_pre_launch(session_dir=case_codex_home)
    assert not pre_launch_errors, f"Codex pre-launch failed: {pre_launch_errors}"
    backend.setup_session_dir(case_codex_home)

    venv_bin = Path(__file__).resolve().parents[3] / ".venv" / "bin"
    env["PATH"] = f"{venv_bin}{os.pathsep}{env.get('PATH', '')}"
    timeout = int(os.environ.get("CODEX_SELECTION_SMOKE_TIMEOUT", "900"))
    result = subprocess.run(  # noqa: S603
        [
            "codex",
            "exec",
            "--json",
            "--sandbox",
            "workspace-write",
            "--model",
            model,
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
            f"Codex selection probe failed with rc={result.returncode}: "
            f"{result.stdout}\n{result.stderr}"
        )

    stream_parser = CodexStreamParser()
    completed_mcp_items: list[dict] = []
    for line in result.stdout.splitlines():
        event = stream_parser.parse_line(line)
        if event is None or event.backend_data is None:
            continue
        raw = event.backend_data.raw
        item = raw.get("item", {})
        if (
            raw.get("type") == "item.completed"
            and isinstance(item, dict)
            and item.get("type") == "mcp_tool_call"
        ):
            completed_mcp_items.append(item)

    parsed = CodexResultParser().parse_stdout(result.stdout, exit_code=result.returncode)
    return _CodexSelectionProbeOutput(
        final_text=parsed.output,
        completed_mcp_items=tuple(completed_mcp_items),
    )


def _completed_mcp_tool_names(output: _CodexSelectionProbeOutput) -> list[str]:
    return [
        str(item.get("tool_name") or item.get("tool") or "") for item in output.completed_mcp_items
    ]


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


@_skip_unless_codex_selection_smoke
def test_codex_selects_local_skill_and_explicit_recipe_delegation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(
        ["git", "init", "-q"],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )

    for selector in (
        "AUTOSKILLIT_AGENT_BACKEND",
        "AUTOSKILLIT_AGENT_BACKEND__BACKEND",
        "AUTOSKILLIT_MCP_CLIENT_BACKEND",
    ):
        monkeypatch.setenv(selector, "codex")
    for headless_flag in (
        "AUTOSKILLIT_HEADLESS",
        "AUTOSKILLIT_HEADLESS_AUTO_GATE",
        "AUTOSKILLIT_SESSION_TYPE",
        "AUTOSKILLIT_SKILL_NAME",
    ):
        monkeypatch.delenv(headless_flag, raising=False)

    source_codex_home = _prepare_codex_selection_profile(tmp_path, workspace)
    model = os.environ.get("GENERATED_CHILD_SMOKE_MODEL", "gpt-5.4")

    local_skill = _run_codex_selection_case(
        case_root=tmp_path / "local-skill-case",
        source_codex_home=source_codex_home,
        workspace=workspace,
        prompt="Use the investigate skill.",
        model=model,
    )
    local_tool_names = _completed_mcp_tool_names(local_skill)
    assert "LOCAL_INVESTIGATE_SKILL_FOLLOWED" in local_skill.final_text
    assert local_tool_names == []

    nonexistent_cwd = tmp_path / "missing-run-skill-cwd"
    delegated = _run_codex_selection_case(
        case_root=tmp_path / "delegation-case",
        source_codex_home=source_codex_home,
        workspace=workspace,
        prompt=(
            "This is an explicit recipe-step delegation request. Call run_skill exactly once "
            "to delegate /investigate positive-probe to a separate L1 worker, passing "
            f"cwd={str(nonexistent_cwd)!r}. Do not call open_kitchen. After run_skill "
            "returns its preflight result, stop without calling any other tool."
        ),
        model=model,
    )
    delegated_tool_names = _completed_mcp_tool_names(delegated)
    assert delegated_tool_names == ["run_skill"]
    run_skill_item = delegated.completed_mcp_items[delegated_tool_names.index("run_skill")]
    assert "preflight:cwd" in json.dumps(run_skill_item.get("result"), sort_keys=True)


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


def _policy_denial_reason(transcript: str) -> str | None:
    for line in transcript.splitlines():
        lowered = line.lower()
        if any(marker in lowered for marker in _POLICY_DENIAL_MARKERS):
            return line.strip()
    return None


def _run_shell_capture_probe(backend: str, tmp_path: Path) -> _DenyRoundTripOutput:
    tmp_path.mkdir(parents=True, exist_ok=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "output_budget_probe.txt").write_text(("output_budget_probe " * 1_000) + "\n")
    env, codex_home, _claude_config = _isolated_cli_env(tmp_path, workspace)

    if backend == "codex":
        env["AUTOSKILLIT_AGENT_BACKEND"] = "codex"
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
    if result.returncode != 0 and _policy_denial_reason(transcript) is None:
        raise OSError(
            f"{backend} shell-capture probe failed with rc={result.returncode}: {transcript}"
        )
    return _DenyRoundTripOutput(
        transcript=transcript,
        cli_version=_cli_version(command[0], env),
        physical_project=workspace,
    )


def _parse_capture_runner(command: str) -> tuple[str, str] | None:
    try:
        argv = shlex.split(command.splitlines()[-1])
        runner_index = next(
            index for index, value in enumerate(argv) if value.endswith("_capture_artifacts.py")
        )
        if argv[runner_index - 1] != "-I" or argv[runner_index + 1] != "run":
            return None
        encoded = argv[runner_index + 2]
        capture_id = argv[runner_index + 4]
        if re.fullmatch(r"[0-9a-f]{16}", capture_id) is None:
            return None
        decoded = base64.b64decode(encoded, validate=True).decode()
        return decoded, capture_id
    except (
        StopIteration,
        IndexError,
        ValueError,
        binascii.Error,
        UnicodeDecodeError,
    ):
        return None


def _assert_shell_capture_round_trip(output: _DenyRoundTripOutput) -> None:
    denial_reason = _policy_denial_reason(output.transcript)
    assert denial_reason is None, (
        "Policy denial detected in shell-capture transcript. "
        f"The generated harness was rejected by Codex's exec-policy engine: {denial_reason}"
    )
    completed_items: list[dict[str, object]] = []
    for line in output.transcript.splitlines():
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(event, dict) or event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != "command_execution":
            continue
        if item.get("status") == "completed":
            completed_items.append(item)

    assert completed_items, (
        "No completed command_execution event found — the rewritten command did not execute"
    )
    parsed = [
        (runner, item)
        for item in completed_items
        if isinstance(command := item.get("command"), str)
        if "autoskillit-shell-capture" in command
        if (runner := _parse_capture_runner(command)) is not None
    ]
    assert parsed, "No completed rewritten command invoked the isolated shell-capture runner"
    matching = [
        (capture_id, item)
        for (command, capture_id), item in parsed
        if command == _OUTPUT_BUDGET_CANARY_COMMAND
    ]
    assert len(matching) == 1, (
        "The completed runner invocation did not transport the canary command"
    )
    capture_id, completed_item = matching[0]
    completed_output = completed_item.get("aggregated_output")
    assert isinstance(completed_output, str), (
        "Completed rewritten command lacks string aggregated_output"
    )
    assert output.physical_project is not None, (
        "Shell-capture probe lacks physical project authority"
    )
    assert_shell_capture_marker_authority(
        completed_output,
        output.physical_project,
        capture_id,
        sentinels=(b"output_budget_probe",),
    )


def test_shell_capture_assertion_requires_completed_rewritten_command(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    capture_id = "0123456789abcdef"
    project = tmp_path / "physical-project"
    project.mkdir()
    assert (
        run_capture(
            ("python3 -c \"import os; os.write(1, b'output_budget_probe ' * 1000)\""),
            str(project),
            capture_id,
        )
        == 0
    )
    production_output = capfd.readouterr()
    assert production_output.err == ""
    encoded = base64.b64encode(_OUTPUT_BUDGET_CANARY_COMMAND.encode()).decode()
    rewritten_command = (
        "# autoskillit-shell-capture v1\n"
        f"/usr/bin/python3 -I /opt/autoskillit/_capture_artifacts.py run {encoded} "
        f"/tmp/workspace {capture_id}"
    )

    def _output(
        *,
        status: str,
        command: str = rewritten_command,
        include_marker: bool = True,
    ) -> _DenyRoundTripOutput:
        event = {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": command,
                "status": status,
                "aggregated_output": (
                    production_output.out if include_marker else "marker absent"
                ),
            },
        }
        unrelated = {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": "printf forged-user-output",
                "status": "completed",
                "aggregated_output": production_output.out,
            },
        }
        return _DenyRoundTripOutput(
            transcript=json.dumps(unrelated) + "\n" + json.dumps(event),
            cli_version="codex-cli test",
            physical_project=project,
        )

    _assert_shell_capture_round_trip(_output(status="completed"))
    with pytest.raises(AssertionError, match="exactly one shell-capture V2 marker"):
        _assert_shell_capture_round_trip(_output(status="completed", include_marker=False))

    for noncompleted_status in ("denied", "failed"):
        with pytest.raises(AssertionError, match="No completed command_execution"):
            _assert_shell_capture_round_trip(_output(status=noncompleted_status))

    command_without_runner = rewritten_command.replace("_capture_artifacts.py", "other.py")
    with pytest.raises(AssertionError, match="isolated shell-capture runner"):
        _assert_shell_capture_round_trip(
            _output(status="completed", command=command_without_runner)
        )


def test_probe_distinguishes_policy_denial_from_transport_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exec_result = subprocess.CompletedProcess(
        args=["codex", "exec"],
        returncode=1,
        stdout=("blocked by policy\nrm -f style commands are not permitted\n"),
        stderr="",
    )

    def _fake_run(
        command: list[str],
        **_kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        if command[:2] == ["git", "init"]:
            return subprocess.CompletedProcess(command, 0, "", "")
        if command == ["codex", "--version"]:
            return subprocess.CompletedProcess(command, 0, "codex-cli test\n", "")
        if command[:2] == ["codex", "exec"]:
            return exec_result
        raise AssertionError(f"unexpected subprocess command: {command}")

    monkeypatch.setattr(subprocess, "run", _fake_run)

    policy_output = _run_shell_capture_probe("codex", tmp_path / "policy")
    with pytest.raises(AssertionError, match="Policy denial detected"):
        _assert_shell_capture_round_trip(policy_output)

    failures: list[tuple[ErrorKind, str, str, str]] = []
    with pytest.raises(AssertionError, match="Policy denial detected"):
        _run_probe_with_discrimination(
            "shell_capture_policy_denial",
            "codex-cli test",
            lambda: policy_output,
            _assert_shell_capture_round_trip,
            record_success=lambda _version: pytest.fail(
                "policy denial must not record probe success"
            ),
            record_failure=lambda kind, name, version, detail: failures.append(
                (kind, name, version, detail)
            ),
        )
    assert failures[0][0] is ErrorKind.SCHEMA

    exec_result = subprocess.CompletedProcess(
        args=["codex", "exec"],
        returncode=1,
        stdout="request timed out before any event was emitted",
        stderr="codex process crashed",
    )
    with pytest.raises(OSError, match="shell-capture probe failed"):
        _run_shell_capture_probe("codex", tmp_path / "transport-direct")

    failures.clear()
    with pytest.raises(OSError, match="shell-capture probe failed"):
        _run_probe_with_discrimination(
            "shell_capture_transport_failure",
            "codex-cli test",
            lambda: _run_shell_capture_probe("codex", tmp_path / "transport-dispatch"),
            _assert_shell_capture_round_trip,
            record_success=lambda _version: pytest.fail(
                "transport failure must not record probe success"
            ),
            record_failure=lambda kind, name, version, detail: failures.append(
                (kind, name, version, detail)
            ),
        )
    assert failures[0][0] is ErrorKind.NETWORK


def _exercise_shell_capture_probe(backend: str, tmp_path: Path) -> None:
    workspace = tmp_path / "version-workspace"
    workspace.mkdir()
    version_env, _, _ = _isolated_cli_env(tmp_path / "version-env", workspace)
    binary = "codex" if backend == "codex" else "claude"
    cli_version = _cli_version(binary, version_env)
    cache_path = tmp_path / f"{backend}-shell-capture-probe-cache.json"
    cached = read_probe_cache(cache_path, cli_version, PROBE_POLICY_IDENTITY)
    if cached is not None and cached.passed:
        pytest.skip(f"Shell-capture probe cached as passed for {cli_version}")

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
        f"shell_capture_round_trip_{backend}",
        cli_version,
        lambda: _run_shell_capture_probe(backend, tmp_path / "round-trip"),
        _assert_shell_capture_round_trip,
        record_success=_record_success,
        record_failure=_record_failure,
    )


@_skip_unless_codex_output_budget_smoke
class TestCodexShellCaptureRoundTrip:
    def test_hook_fires_and_command_is_rewritten(self, tmp_path: Path) -> None:
        _exercise_shell_capture_probe("codex", tmp_path)


_SOURCE_SPILL_THRESHOLD = OutputBudgetConfig().inline_max_chars
_CODEX_HEURISTIC_BYTES = CODEX_HISTORY_RETENTION_TOKEN_LIMIT * 4
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
        '"pipeline_health":"true"}. Then make every autoskillit run_cmd call below, '
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
            CODEX_HISTORY_RETENTION_TOKEN_LIMIT * 4,
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


class _ClaudeStartupProbeResult(NamedTuple):
    ready: bool
    tool_list_observed: bool
    open_kitchen_result_observed: bool
    question_detected: bool
    output_bytes: int
    output_sha256: str
    trace_path: Path


def _write_delayed_startup_plugin(
    tmp_path: Path,
    *,
    delay_ms: int,
    trace_path: Path,
) -> Path:
    plugin_dir = tmp_path / "plugin"
    (plugin_dir / ".claude-plugin").mkdir(parents=True)
    (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": "autoskillit-startup-probe",
                "version": "1.0.0",
                "description": "Isolated startup-readiness conformance probe",
            }
        ),
        encoding="utf-8",
    )
    autoskillit_executable = Path(sys.executable).with_name("autoskillit")
    assert autoskillit_executable.is_file()
    shim = tmp_path / "delayed_autoskillit.py"
    shim.write_text(
        "\n".join(
            (
                "import json",
                "import os",
                "import subprocess",
                "import sys",
                "import threading",
                "import time",
                "from pathlib import Path",
                "trace = Path(sys.argv[1])",
                "delay_ms = int(sys.argv[2])",
                "executable = sys.argv[3]",
                "write_lock = threading.Lock()",
                "request_lock = threading.Lock()",
                "requests = {}",
                "def record(payload):",
                "    payload['monotonic_ns'] = time.monotonic_ns()",
                "    payload['server_pid'] = os.getpid()",
                "    with write_lock:",
                "        with trace.open('a', encoding='utf-8') as handle:",
                "            handle.write(json.dumps(payload, sort_keys=True) + '\\n')",
                "record({'event': 'server_delay_started'})",
                "time.sleep(delay_ms / 1000)",
                "record({'event': 'server_exec_started'})",
                "child = subprocess.Popen(",
                "    [executable],",
                "    stdin=subprocess.PIPE,",
                "    stdout=subprocess.PIPE,",
                "    stderr=subprocess.PIPE,",
                ")",
                "def pump_client():",
                "    assert child.stdin is not None",
                "    for line in sys.stdin.buffer:",
                "        try:",
                "            payload = json.loads(line)",
                "        except (json.JSONDecodeError, UnicodeDecodeError):",
                "            payload = {}",
                "        method = payload.get('method')",
                "        params = payload.get('params')",
                "        tool_name = params.get('name') if isinstance(params, dict) else None",
                "        request_id = payload.get('id')",
                "        if request_id is not None:",
                "            with request_lock:",
                "                requests[str(request_id)] = (method, tool_name)",
                "        record({'event': 'client_message', 'method': method, "
                "'tool_name': tool_name})",
                "        child.stdin.write(line)",
                "        child.stdin.flush()",
                "    child.stdin.close()",
                "def pump_stderr():",
                "    assert child.stderr is not None",
                "    for chunk in iter(lambda: child.stderr.read(8192), b''):",
                "        sys.stderr.buffer.write(chunk)",
                "        sys.stderr.buffer.flush()",
                "threading.Thread(target=pump_client, daemon=True).start()",
                "threading.Thread(target=pump_stderr, daemon=True).start()",
                "assert child.stdout is not None",
                "for line in child.stdout:",
                "    try:",
                "        payload = json.loads(line)",
                "    except (json.JSONDecodeError, UnicodeDecodeError):",
                "        payload = {}",
                "    request_id = payload.get('id')",
                "    with request_lock:",
                "        request = requests.get(str(request_id))",
                "    if request is not None and request[0] == 'tools/list':",
                "        result = payload.get('result')",
                "        tools = result.get('tools', []) if isinstance(result, dict) else []",
                "        names = [tool.get('name') for tool in tools if isinstance(tool, dict)]",
                "        schema_bytes = len(json.dumps(tools, sort_keys=True).encode('utf-8'))",
                "        record({'event': 'tool_list_snapshot', 'tool_names': names, "
                "'schema_bytes': schema_bytes})",
                "    elif (request is not None and request[0] == 'tools/call' "
                "and isinstance(request[1], str) "
                "and request[1].endswith('open_kitchen')):",
                "        result = payload.get('result')",
                "        is_error = result.get('isError') if isinstance(result, dict) else True",
                "        record({'event': 'open_kitchen_result', 'is_error': bool(is_error), "
                "'has_jsonrpc_error': 'error' in payload})",
                "    sys.stdout.buffer.write(line)",
                "    sys.stdout.buffer.flush()",
                "raise SystemExit(child.wait())",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    (plugin_dir / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "autoskillit": {
                        "command": sys.executable,
                        "args": [
                            str(shim),
                            str(trace_path),
                            str(delay_ms),
                            str(autoskillit_executable),
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return plugin_dir


def _read_startup_trace(trace_path: Path) -> list[dict[str, object]]:
    if not trace_path.exists():
        return []
    events: list[dict[str, object]] = []
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def _run_claude_startup_probe(
    tmp_path: Path,
    *,
    delay_ms: int,
    connect_timeout_ms: int,
) -> _ClaudeStartupProbeResult:
    from autoskillit.cli._prompts import _MCP_RETRY_INSTRUCTION

    trace_dir = Path.cwd() / ".autoskillit" / "temp" / "claude-startup-readiness"
    trace_dir.mkdir(parents=True, exist_ok=True)
    trace_path = trace_dir / f"trace-{time.time_ns()}-{delay_ms}.jsonl"
    terminal_path = trace_path.with_suffix(".terminal.bin")
    plugin_dir = _write_delayed_startup_plugin(
        tmp_path,
        delay_ms=delay_ms,
        trace_path=trace_path,
    )
    home = tmp_path / "home"
    config_dir = tmp_path / "claude-config"
    home.mkdir()
    config_dir.mkdir()
    (config_dir / "settings.json").write_text(
        json.dumps(
            {
                "env": {"DISABLE_AUTOUPDATER": "1"},
                "theme": "dark",
            }
        ),
        encoding="utf-8",
    )
    executable = Path(shutil.which("claude") or "").resolve(strict=True)
    onboarding_state: dict[str, object] = {
        "autoUpdates": False,
        "hasCompletedOnboarding": True,
        "installMethod": "global",
        "lastOnboardingVersion": CLAUDE_CODE_CAPABILITIES.min_version,
        "theme": "dark",
    }
    for onboarding_path in (
        home / ".claude.json",
        config_dir / ".claude.json",
        config_dir.parent / ".claude.json",
    ):
        onboarding_path.write_text(json.dumps(onboarding_state), encoding="utf-8")
    isolated_binary = home / ".local" / "bin" / "claude"
    isolated_binary.parent.mkdir(parents=True)
    isolated_binary.symlink_to(executable)
    environment = dict(os.environ)
    environment.pop("CLAUDECODE", None)
    environment.update(
        {
            "CLAUDE_CODE_EXECPATH": str(executable),
            "CLAUDE_CONFIG_DIR": str(config_dir),
            "HOME": str(home),
            "IS_DEMO": "1",
            "MCP_CONNECTION_NONBLOCKING": "0",
            "MCP_CONNECT_TIMEOUT_MS": str(connect_timeout_ms),
        }
    )
    command = [
        str(executable),
        "--dangerously-skip-permissions",
        "--plugin-dir",
        str(plugin_dir),
        "--append-system-prompt",
        _MCP_RETRY_INSTRUCTION,
        (
            "Call open_kitchen now. During startup follow the bounded silent retry "
            "contract. After a successful result output AUTOSKILLIT_STARTUP_READY "
            "and no question."
        ),
        "--tools",
        "AskUserQuestion",
    ]
    master_fd, slave_fd = pty.openpty()
    started_ns = time.monotonic_ns()
    process = subprocess.Popen(
        command,
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        cwd=tmp_path,
        env=environment,
        close_fds=True,
        start_new_session=True,
    )
    os.close(slave_fd)
    retained = bytearray()
    deadline = time.monotonic() + 90
    try:
        while time.monotonic() < deadline:
            readable, _, _ = select.select([master_fd], [], [], 0.1)
            if readable:
                try:
                    chunk = os.read(master_fd, 64 * 1024)
                except OSError:
                    break
                if not chunk:
                    break
                retained.extend(chunk)
                del retained[: -256 * 1024]
            if any(
                event.get("event") == "open_kitchen_result"
                for event in _read_startup_trace(trace_path)
            ):
                break
            if process.poll() is not None:
                break
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        terminal_path.write_bytes(bytes(retained))
        os.close(master_fd)
    output = bytes(retained)
    lowered = output.lower()
    trace_events = _read_startup_trace(trace_path)
    tool_list_observed = any(
        event.get("event") == "tool_list_snapshot"
        and "open_kitchen" in event.get("tool_names", [])
        for event in trace_events
    )
    open_kitchen_result_observed = any(
        event.get("event") == "open_kitchen_result"
        and event.get("is_error") is False
        and event.get("has_jsonrpc_error") is False
        for event in trace_events
    )
    result = _ClaudeStartupProbeResult(
        ready=tool_list_observed and open_kitchen_result_observed,
        tool_list_observed=tool_list_observed,
        open_kitchen_result_observed=open_kitchen_result_observed,
        question_detected=(
            b"askuserquestion" in lowered
            or b"what would you like" in lowered
            or b"would you like me to" in lowered
        ),
        output_bytes=len(output),
        output_sha256=hashlib.sha256(output).hexdigest(),
        trace_path=trace_path,
    )
    with trace_path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "event": "probe_terminal",
                    "delay_ms": delay_ms,
                    "connect_timeout_ms": connect_timeout_ms,
                    "elapsed_ms": (time.monotonic_ns() - started_ns) // 1_000_000,
                    "ready": result.ready,
                    "tool_list_observed": result.tool_list_observed,
                    "open_kitchen_result_observed": (result.open_kitchen_result_observed),
                    "question_detected": result.question_detected,
                    "output_bytes": result.output_bytes,
                    "output_sha256": result.output_sha256,
                    "executable": str(executable),
                },
                sort_keys=True,
            )
            + "\n"
        )
    return result


@_skip_unless_claude_startup_smoke
@pytest.mark.timeout(120)
@pytest.mark.parametrize(
    ("delay_ms", "connect_timeout_ms"),
    [(0, 5_000), (1_000, 5_000), (2_500, 1_000)],
    ids=["immediate", "within-budget", "fresh-over-budget-retry"],
)
def test_claude_startup_readiness_addressability_trace(
    tmp_path: Path,
    delay_ms: int,
    connect_timeout_ms: int,
) -> None:
    result = _run_claude_startup_probe(
        tmp_path,
        delay_ms=delay_ms,
        connect_timeout_ms=connect_timeout_ms,
    )

    assert result.ready, f"startup did not converge; bounded trace: {result.trace_path}"
    assert result.tool_list_observed
    assert result.open_kitchen_result_observed
    assert not result.question_detected
    assert result.output_bytes <= 256 * 1024

    if delay_ms > connect_timeout_ms:
        trace_events = _read_startup_trace(result.trace_path)
        attempts = [
            event for event in trace_events if event.get("event") == "server_delay_started"
        ]
        success = next(
            event for event in trace_events if event.get("event") == "open_kitchen_result"
        )
        first_attempt = attempts[0]
        assert len(attempts) >= 2
        successful_attempt = next(
            event for event in attempts if event.get("server_pid") == success.get("server_pid")
        )

        assert first_attempt["server_pid"] != successful_attempt["server_pid"]
        assert not any(
            event.get("event") == "server_exec_started"
            and event.get("server_pid") == first_attempt["server_pid"]
            for event in trace_events
        )
        assert (
            int(first_attempt["monotonic_ns"])
            < int(successful_attempt["monotonic_ns"])
            < int(success["monotonic_ns"])
        )
