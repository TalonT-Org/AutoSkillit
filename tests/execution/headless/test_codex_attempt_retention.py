"""Generated-home Codex attempt retention across headless runner cycles."""

from __future__ import annotations

import errno
import json
import os
import re
import shutil
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from autoskillit.core import (
    CmdSpec,
    NamedResume,
    NoResume,
    PluginLoadMode,
    RetryReason,
    SkillResult,
)
from autoskillit.core.types import KillReason, SubprocessResult, TerminationReason
from autoskillit.core.types._type_results import WriteEvidence
from autoskillit.execution.backends import codex as codex_module
from autoskillit.execution.backends._codex_session_storage import CodexSessionStore
from autoskillit.execution.backends.codex import CodexBackend
from tests.execution.conftest import _launch_inputs, _mock_backend
from tests.fakes import FakePluginArtifactAuthority

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]

_ROLLOUT_RELATIVE_PATH = Path("2026/09/rollout-retained.jsonl")
_THREAD_ID = "thread-retained"
_EXECUTION_CEILING_SECONDS = 321.0


def _generated_home(tmp_path: Path, name: str) -> Path:
    """Create the inert rollout topology required before a Codex attempt enters."""
    home = tmp_path / name
    home.mkdir()
    for public_name in ("sessions", "archived_sessions"):
        inert_target = home / f".inert-{public_name}"
        inert_target.mkdir()
        (home / public_name).symlink_to(inert_target)
    return home


def _write_rollout(path: Path, *, cwd: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            (
                json.dumps({"type": "thread.started", "thread_id": _THREAD_ID}),
                json.dumps(
                    {
                        "type": "session_meta",
                        "payload": {"id": _THREAD_ID, "cwd": str(cwd)},
                    }
                ),
                json.dumps({"type": "turn.completed"}),
            )
        )
        + "\n",
        encoding="utf-8",
    )


class _RolloutRunner:
    """Callback-aware runner that writes a minimal real Codex rollout."""

    def __init__(
        self,
        *,
        write_rollout: bool,
        reap: bool,
        termination: TerminationReason = TerminationReason.NATURAL_EXIT,
        fail_spawn: bool = False,
        required_staged_calls: frozenset[int] = frozenset(),
    ) -> None:
        self.write_rollout = write_rollout
        self.reap = reap
        self.termination = termination
        self.fail_spawn = fail_spawn
        self.required_staged_calls = required_staged_calls
        self.calls: list[dict[str, object]] = []
        self.callback_events: list[tuple[str, int, int]] = []
        self.pass_fd_sets: list[tuple[int, ...]] = []

    async def __call__(
        self,
        cmd: list[str],
        *,
        cwd: Path,
        timeout: float,
        env: Mapping[str, str] | None = None,
        pass_fds: tuple[int, ...] = (),
        **kwargs: object,
    ) -> SubprocessResult:
        del cmd, timeout
        assert env is not None
        session_home = Path(env["CODEX_HOME"])
        assert env["CODEX_SQLITE_HOME"] == str(session_home)
        call_number = len(self.calls) + 1
        staged_rollout = session_home / "sessions" / _ROLLOUT_RELATIVE_PATH
        if call_number in self.required_staged_calls:
            assert staged_rollout.is_file(), "NamedResume must stage the retained rollout first"
        self.calls.append(
            {
                "home": session_home,
                "pass_fds": pass_fds,
                "staged_rollout": staged_rollout,
                "ceiling_seconds": kwargs.get("ceiling_seconds"),
            }
        )
        self.pass_fd_sets.append(pass_fds)
        if self.fail_spawn:
            raise OSError("injected spawn failure")

        on_spawned = kwargs["on_process_spawned"]
        on_reaped = kwargs["on_process_reaped"]
        assert callable(on_spawned)
        assert callable(on_reaped)
        pid = os.getpid()
        pgid = os.getpgrp()
        on_spawned(pid, pgid)
        self.callback_events.append(("spawned", pid, pgid))
        if self.write_rollout:
            _write_rollout(staged_rollout, cwd=cwd)
        if self.reap:
            on_reaped(pid, pgid)
            self.callback_events.append(("reaped", pid, pgid))
        return SubprocessResult(
            returncode=0,
            stdout="",
            stderr="",
            termination=self.termination,
            pid=pid,
        )


def _generated_home_spec(home: Path, cwd: Path):
    def build(_binding, _extras, _attempt_id=None) -> CmdSpec:
        return CmdSpec(
            cmd=("codex", "exec", "/autoskillit:test"),
            cwd=str(cwd),
            env={"CODEX_HOME": str(home), "CODEX_SQLITE_HOME": str(home)},
        )

    return build


def _assert_attempt_leases_are_closed(pass_fd_sets: list[tuple[int, ...]]) -> None:
    for pass_fds in pass_fd_sets:
        for fd in pass_fds:
            with pytest.raises(OSError) as exc_info:
                os.fstat(fd)
            assert exc_info.value.errno == errno.EBADF


async def _run_generated_attempt(
    *,
    backend: Mock,
    runner: _RolloutRunner,
    plugin_authority: FakePluginArtifactAuthority,
    cwd: Path,
    home: Path,
    attempt: int,
    managed_attempt_id: str,
    resume_session_id: str = "",
) -> tuple[SubprocessResult, CmdSpec]:
    from autoskillit.execution.headless._headless_launch import _run_headless_attempt

    launch_resolver, launch_preparation = _launch_inputs(backend, cwd=str(cwd))
    return await _run_headless_attempt(
        build_spec=_generated_home_spec(home, cwd),
        runner=runner,
        backend=backend,
        launch_resolver=launch_resolver,
        launch_preparation=launch_preparation,
        expected_launch_contract=None,
        plugin_authority=plugin_authority,
        plugin_load_mode=PluginLoadMode.GENERATED_HOME,
        provider_extras=None,
        timeout=10.0,
        pty_override=False,
        completion_marker="%%DONE%%",
        stale_threshold=1.0,
        completion_drain_timeout=1.0,
        natural_exit_grace_seconds=1.0,
        linux_tracing_config=None,
        idle_output_timeout=None,
        max_suppression_seconds=0.0,
        child_deferral_ceiling=0.0,
        on_spawn=None,
        enable_deadline_extension=False,
        max_extension_seconds=0.0,
        ceiling_seconds=_EXECUTION_CEILING_SECONDS,
        systemd_scope_enabled=False,
        marker_dir=None,
        session_id=None,
        on_session_id_resolved=None,
        stream_parser=Mock(),
        backend_resume_session_id=resume_session_id,
        lifecycle_observation_enabled=False,
        attempt=attempt,
        managed_attempt_id=managed_attempt_id,
    )


def _retention_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Mock:
    log_dir = tmp_path / "log-root"
    monkeypatch.setattr(codex_module, "default_log_dir", lambda: log_dir)
    backend = _mock_backend(pty_required=False, session_resume_capable=True)
    backend.name = "codex"
    backend.session_attempt_context.side_effect = CodexBackend().session_attempt_context
    return backend


@pytest.mark.anyio
async def test_generated_home_attempt_retains_rollout_before_nudge_and_named_resume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A promoted rollout remains resumable after its original home is removed."""
    backend = _retention_backend(tmp_path, monkeypatch)
    cwd = tmp_path.resolve()
    first_home = _generated_home(tmp_path, "first-home")
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    authority = FakePluginArtifactAuthority(plugin_dir)
    runner = _RolloutRunner(
        write_rollout=True,
        reap=True,
        required_staged_calls=frozenset({2, 3}),
    )

    await _run_generated_attempt(
        backend=backend,
        runner=runner,
        plugin_authority=authority,
        cwd=cwd,
        home=first_home,
        attempt=1,
        managed_attempt_id="a" * 32,
    )

    store = CodexSessionStore(log_dir=tmp_path / "log-root")
    canonical_rollout = store.active_root / _ROLLOUT_RELATIVE_PATH
    assert canonical_rollout.is_file()
    assert store.read_index(str(cwd))[0].session_id == _THREAD_ID

    from autoskillit.execution.headless._headless_launch import _attempt_contract_nudge

    def build_resume_cmd(**kwargs: object) -> CmdSpec:
        session_home = str(kwargs["session_home"])
        return CmdSpec(
            cmd=("codex", "exec", "resume", _THREAD_ID),
            cwd=str(cwd),
            env={"CODEX_HOME": session_home, "CODEX_SQLITE_HOME": session_home},
            is_resume=True,
        )

    backend.build_resume_cmd.side_effect = build_resume_cmd
    nudge_resolver, nudge_preparation = _launch_inputs(backend, cwd=str(cwd))
    nudge_parser = Mock()
    nudge_parser.parse_stdout.return_value = SimpleNamespace(
        output="%%DONE%%", raw={}, session_id=_THREAD_ID
    )
    nudge_result = await _attempt_contract_nudge(
        skill_result=SkillResult(
            success=False,
            result="",
            session_id=_THREAD_ID,
            subtype="empty_output",
            is_error=False,
            exit_code=0,
            needs_retry=True,
            retry_reason=RetryReason.EARLY_STOP,
            stderr="",
            kill_reason=KillReason.NATURAL_EXIT,
            evidence=WriteEvidence.none_observed(),
        ),
        subprocess_result=SubprocessResult(
            returncode=0,
            stdout="",
            stderr="",
            termination=TerminationReason.NATURAL_EXIT,
            pid=0,
        ),
        expected_output_patterns=[],
        completion_marker="%%DONE%%",
        cwd=str(cwd),
        runner=runner,
        backend=backend,
        result_parser=nudge_parser,
        retry_reason=RetryReason.EARLY_STOP,
        plugin_authority=authority,
        plugin_load_mode=PluginLoadMode.GENERATED_HOME,
        session_env={"CODEX_HOME": str(first_home)},
        launch_resolver=nudge_resolver,
        launch_preparation=nudge_preparation,
        natural_exit_grace_seconds=1.0,
        attempt=2,
        ceiling_seconds=_EXECUTION_CEILING_SECONDS,
    )

    assert nudge_result is not None and nudge_result.success
    assert canonical_rollout.is_file(), "the first attempt promotes before nudge enters"
    assert runner.calls[1]["staged_rollout"] == first_home / "sessions" / _ROLLOUT_RELATIVE_PATH
    assert backend.build_resume_cmd.call_args.kwargs["session_home"] == str(first_home)

    shutil.rmtree(first_home)
    assert not first_home.exists()
    resumed_home = _generated_home(tmp_path, "resumed-home")
    assert not list(resumed_home.rglob("*.sqlite"))

    await _run_generated_attempt(
        backend=backend,
        runner=runner,
        plugin_authority=authority,
        cwd=cwd,
        home=resumed_home,
        attempt=3,
        managed_attempt_id="b" * 32,
        resume_session_id=_THREAD_ID,
    )

    attempt_calls = backend.session_attempt_context.call_args_list
    assert [call.kwargs["attempt"] for call in attempt_calls] == [1, 2, 3]
    assert isinstance(attempt_calls[0].kwargs["current_resume_spec"], NoResume)
    assert all(
        isinstance(call.kwargs["current_resume_spec"], NamedResume) for call in attempt_calls[1:]
    )
    assert [call.kwargs["project_dir"] for call in attempt_calls] == [cwd, cwd, cwd]
    assert [call.kwargs["ceiling_seconds"] for call in attempt_calls] == [
        _EXECUTION_CEILING_SECONDS,
        _EXECUTION_CEILING_SECONDS,
        _EXECUTION_CEILING_SECONDS,
    ]
    assert all(re.fullmatch(r"[0-9a-f]{16}", call.kwargs["launch_id"]) for call in attempt_calls)
    assert attempt_calls[0].kwargs["launch_id"] == "a" * 16
    assert attempt_calls[2].kwargs["launch_id"] == "b" * 16
    assert [call["home"] for call in runner.calls] == [first_home, first_home, resumed_home]
    assert all(runner.pass_fd_sets)
    assert [call["ceiling_seconds"] for call in runner.calls] == [
        _EXECUTION_CEILING_SECONDS,
        _EXECUTION_CEILING_SECONDS,
        _EXECUTION_CEILING_SECONDS,
    ]
    assert [event[0] for event in runner.callback_events] == [
        "spawned",
        "reaped",
        "spawned",
        "reaped",
        "spawned",
        "reaped",
    ]
    assert all(pid > 0 and pgid > 0 for _event, pid, pgid in runner.callback_events)
    _assert_attempt_leases_are_closed(runner.pass_fd_sets)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("mode", "write_rollout", "reap", "termination"),
    [
        pytest.param("timeout", True, True, TerminationReason.TIMED_OUT, id="timeout"),
        pytest.param("failed-spawn", False, False, TerminationReason.NATURAL_EXIT, id="spawn"),
        pytest.param("failed-reap", True, False, TerminationReason.NATURAL_EXIT, id="reap"),
        pytest.param("absent-rollout", False, True, TerminationReason.NATURAL_EXIT, id="rollout"),
    ],
)
async def test_generated_home_attempt_requires_confirmed_spawn_reap_and_rollout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    write_rollout: bool,
    reap: bool,
    termination: TerminationReason,
) -> None:
    """Only a reaped attempt with a valid rollout can make durable history."""
    backend = _retention_backend(tmp_path, monkeypatch)
    cwd = tmp_path.resolve()
    home = _generated_home(tmp_path, "generated-home")
    plugin_dir = tmp_path / "plugin"
    plugin_dir.mkdir()
    authority = FakePluginArtifactAuthority(plugin_dir)
    runner = _RolloutRunner(
        write_rollout=write_rollout,
        reap=reap,
        termination=termination,
        fail_spawn=mode == "failed-spawn",
    )
    store = CodexSessionStore(log_dir=tmp_path / "log-root")

    if mode == "timeout":
        await _run_generated_attempt(
            backend=backend,
            runner=runner,
            plugin_authority=authority,
            cwd=cwd,
            home=home,
            attempt=1,
            managed_attempt_id="c" * 32,
        )
        assert (store.active_root / _ROLLOUT_RELATIVE_PATH).is_file()
        assert not [path for path in store.views_root.iterdir() if path.name != ".locks"]
    else:
        expected_message = (
            "injected spawn failure"
            if mode == "failed-spawn"
            else "child-reaped proof"
            if mode == "failed-reap"
            else "no rollout data"
        )
        with pytest.raises((OSError, RuntimeError), match=expected_message):
            await _run_generated_attempt(
                backend=backend,
                runner=runner,
                plugin_authority=authority,
                cwd=cwd,
                home=home,
                attempt=1,
                managed_attempt_id="c" * 32,
            )
        assert not (store.active_root / _ROLLOUT_RELATIVE_PATH).exists()
        retained = [path for path in store.views_root.iterdir() if path.name != ".locks"]
        if mode == "failed-spawn":
            assert retained == []
        else:
            assert len(retained) == 1

    assert len(runner.pass_fd_sets[0]) == 1
    assert runner.pass_fd_sets[0][0] >= 0
    _assert_attempt_leases_are_closed(runner.pass_fd_sets)
