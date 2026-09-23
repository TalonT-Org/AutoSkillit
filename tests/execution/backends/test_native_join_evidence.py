"""Failure evidence and cleanup for the credentialed join gates."""

from __future__ import annotations

import io
import json
import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoskillit.execution.backends._codex_catalog import (
    CodexCatalogAcquisitionError,
    run_owned_bounded,
)
from tests.conftest import production_interpreter_env
from tests.execution.backends import test_codex_managed_route_live_gate as live

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]


def test_bounded_runner_keeps_partial_output_and_reaps_timed_out_child(tmp_path: Path) -> None:
    capture = tmp_path / "evidence"
    with pytest.raises(CodexCatalogAcquisitionError):
        run_owned_bounded(
            (
                sys.executable,
                "-c",
                "import os,sys,time; print(os.getpid(), flush=True); "
                "print('started', file=sys.stderr, flush=True); time.sleep(60)",
            ),
            cwd=tmp_path,
            environment=production_interpreter_env(),
            deadline=time.monotonic() + 2,
            stdout_limit=4096,
            capture_dir=capture,
        )
    child_pid = int((capture / "stdout.txt").read_text())
    assert (capture / "stderr.txt").read_text() == "started\n"
    command = json.loads((capture / "command.json").read_text())
    assert command["schema_version"] == 1
    assert command["argv"][0] == sys.executable
    assert command["cwd"] == str(tmp_path)
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)


def test_bounded_runner_settles_child_when_selector_creation_fails(tmp_path: Path) -> None:
    stdout = io.BytesIO()
    stderr = io.BytesIO()
    settled: list[BaseException] = []

    def settle_preserving(exc: BaseException, *, timeout: float) -> SimpleNamespace:
        settled.append(exc)
        return SimpleNamespace(complete=True)

    owner = SimpleNamespace(
        process=SimpleNamespace(stdout=stdout, stderr=stderr),
        settle_preserving=settle_preserving,
    )

    def fail_selector() -> None:
        raise RuntimeError("selector unavailable")

    with pytest.raises(RuntimeError, match="selector unavailable"):
        run_owned_bounded(
            ("codex",),
            cwd=tmp_path,
            environment={},
            deadline=time.monotonic() + 1,
            stdout_limit=4096,
            spawn=lambda *_args, **_kwargs: owner,
            selector_factory=fail_selector,
        )

    assert len(settled) == 1
    assert stdout.closed and stderr.closed


def test_pending_wave_watcher_finishes_when_native_run_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    watchers: list[threading.Thread] = []

    def create_watcher(**kwargs) -> threading.Thread:
        watcher = threading.Thread(**kwargs)
        watchers.append(watcher)
        return watcher

    def failed_run(**kwargs):
        raise RuntimeError("native launch failed")

    monkeypatch.setattr(
        live, "threading", SimpleNamespace(Event=threading.Event, Thread=create_watcher)
    )
    monkeypatch.setattr(live, "run_live_codex_parent_bounded", failed_run)
    monkeypatch.setenv("AUTOSKILLIT_STATE_ROOT", str(tmp_path))
    with pytest.raises(RuntimeError, match="native launch failed"):
        live._run_denial_then_release(
            repository=tmp_path,
            env={},
            launch_id="cleanup-session",
            artifact_digest="digest",
            log_dir=tmp_path / "logs",
            label="cleanup",
            evidence_dir=tmp_path / "evidence",
        )
    assert watchers and all(not watcher.is_alive() for watcher in watchers)
