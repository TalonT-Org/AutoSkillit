"""Tests for the cleanup-only capture lifecycle SessionStart hook."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

import pytest

from autoskillit.execution.backends._codex_hooks import generate_codex_hooks_config
from autoskillit.hooks import capture_lifecycle_hook
from autoskillit.hooks._capture_artifacts import (
    CAPTURE_PATH_COMPONENTS,
    create_capture_artifact,
    open_capture_root,
    open_project_anchor,
)
from autoskillit.hooks._capture_lifecycle import LEDGER_NAME, CaptureLifecycleStore

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.medium]

SCRIPT = Path(__file__).resolve().parents[2] / "src/autoskillit/hooks/capture_lifecycle_hook.py"
_CAPTURE_ID = "0123456789abcdef"


def _run_hook(
    project: Path,
    payload: object,
    *,
    headless: bool,
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    if headless:
        env["AUTOSKILLIT_HEADLESS"] = "1"
    else:
        env.pop("AUTOSKILLIT_HEADLESS", None)
    return subprocess.run(
        [sys.executable, str(SCRIPT)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=project,
        env=env,
        timeout=5,
        check=False,
    )


def _seed_due_capture(project: Path) -> Path:
    old = time.time() - 7200
    anchor = open_project_anchor(str(project))
    root = open_capture_root(anchor, create=True)
    lifecycle = CaptureLifecycleStore.from_open_authorities(
        anchor,
        root,
        wall_clock=lambda: old,
        monotonic=lambda: old,
    )
    artifact = create_capture_artifact(root, _CAPTURE_ID, lifecycle)
    os.write(artifact.fd, b"due")
    lifecycle.finalize_capture(
        _CAPTURE_ID,
        size=3,
        sha256="0" * 64,
        failed=False,
    )
    path = project.joinpath(*CAPTURE_PATH_COMPONENTS, artifact.name)
    artifact.close_artifact_fd()
    root.close()
    anchor.close()
    artifact.release_lease()
    return path


@pytest.mark.parametrize("headless", [False, True])
def test_cleanup_hook_deletes_due_capture_in_every_session_scope(
    tmp_path: Path,
    headless: bool,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    artifact = _seed_due_capture(project)

    completed = _run_hook(project, {"cwd": str(project)}, headless=headless)

    assert completed.returncode == 0
    assert completed.stdout == ""
    assert completed.stderr == ""
    assert not artifact.exists()


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {"cwd": ""},
        {"cwd": "relative"},
        {"cwd": 1},
    ],
)
def test_cleanup_hook_rejects_invalid_payload_without_output(
    tmp_path: Path,
    payload: object,
) -> None:
    project = tmp_path / "project"
    project.mkdir()

    completed = _run_hook(project, payload, headless=False)

    assert completed.returncode == 0
    assert completed.stdout == ""
    assert completed.stderr == ""
    assert not (project / ".autoskillit").exists()


def test_cleanup_hook_does_not_create_absent_store(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()

    completed = _run_hook(project, {"cwd": str(project)}, headless=True)

    assert completed.returncode == 0
    assert completed.stdout == ""
    assert completed.stderr == ""
    assert not (project / ".autoskillit").exists()


def test_cleanup_hook_fails_open_with_bounded_stderr(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _seed_due_capture(project)
    ledger = project.joinpath(*CAPTURE_PATH_COMPONENTS, LEDGER_NAME)
    payload = bytearray(ledger.read_bytes())
    payload[-1] ^= 0x01
    ledger.write_bytes(payload)

    completed = _run_hook(project, {"cwd": str(project)}, headless=False)

    assert completed.returncode == 0
    assert completed.stdout == ""
    assert "capture lifecycle cleanup failed" in completed.stderr
    assert len(completed.stderr.encode()) <= 512


def test_cleanup_hook_bounds_multibyte_diagnostic_bytes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    capture_lifecycle_hook._bounded_stderr("🔥" * 512)

    captured = capsys.readouterr()
    assert captured.err
    assert len(captured.err.encode("utf-8")) <= 512


def test_generated_codex_session_start_executes_cleanup_dispatcher(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    artifact = _seed_due_capture(project)
    entries = generate_codex_hooks_config().get("SessionStart", [])
    commands = [
        hook["command"]
        for entry in entries
        for hook in entry["hooks"]
        if "capture_lifecycle_hook" in hook["command"]
    ]
    assert len(commands) == 1
    assert all(
        "session_start_hook" not in hook["command"] for entry in entries for hook in entry["hooks"]
    )

    completed = subprocess.run(
        shlex.split(commands[0]),
        input=json.dumps({"cwd": str(project)}),
        capture_output=True,
        text=True,
        cwd=project,
        env={**os.environ, "AUTOSKILLIT_HEADLESS": "1"},
        timeout=5,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stdout == ""
    assert completed.stderr == ""
    assert not artifact.exists()
