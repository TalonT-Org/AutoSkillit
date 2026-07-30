"""Installed Codex hook snapshot coverage for shell-capture lifecycle owners."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
import tomllib
from pathlib import Path

import pytest

from autoskillit.execution.backends.codex import CodexBackend
from autoskillit.hooks._capture._snapshot import (
    CaptureMeasurement,
    CommandOutcome,
    verify_capture_snapshot,
)
from autoskillit.hooks._capture_artifacts import (
    CAPTURE_PATH_COMPONENTS,
    create_capture_artifact,
    open_capture_root,
    open_project_anchor,
)
from autoskillit.hooks._capture_lifecycle import CaptureLifecycleStore
from tests.execution.backends._conformance_assertions import (
    assert_shell_capture_marker_authority,
)

pytestmark = [pytest.mark.layer("execution"), pytest.mark.medium]

_CAPTURE_ID = "fedcba9876543210"
_TIMEOUT_SECONDS = 15


class _Clock:
    def __init__(self, value: float) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


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
    verified = verify_capture_snapshot(
        fd=artifact.fd,
        capture_id=artifact.authority.capture_id,
        incarnation=artifact.authority.incarnation,
        project_identity=(anchor.identity.device, anchor.identity.inode),
        root_identity=(root.identity.device, root.identity.inode),
        carrier_name=artifact.name,
        carrier_identity=(artifact.identity.device, artifact.identity.inode),
        measurement=CaptureMeasurement.from_bytes(b"due", inline_bytes=3),
        command_outcome=CommandOutcome.exited(0),
        expected_revision=artifact.authority.expected_revision,
        finalized_at=old,
        retention_deadline=old + 3600.0,
    )
    lifecycle.commit_verified_snapshot(verified, issue_reference=False)
    artifact_path = project.joinpath(*CAPTURE_PATH_COMPONENTS, artifact.name)
    artifact.close_artifact_fd()
    artifact.release_lease()
    root.close()
    anchor.close()
    return artifact_path


def _snapshot_codex_hooks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict[str, object], Path, Path, Path]:
    source_home = tmp_path / "source-home"
    source_home.mkdir()
    source_config = source_home / "config.toml"
    source_config.write_text('[foreign]\nowner = "user"\n', encoding="utf-8")
    generated_home = tmp_path / "generated-home"
    generated_home.mkdir()
    project = tmp_path / "physical-project"
    project.mkdir()
    decoy_cwd = tmp_path / "decoy-cwd"
    decoy_cwd.mkdir()
    ambient_home = tmp_path / "ambient-home"
    ambient_home.mkdir()

    monkeypatch.setenv("CODEX_HOME", str(ambient_home))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: ambient_home))
    backend = CodexBackend(source_codex_home=source_home)

    assert backend.ensure_pre_launch(session_dir=generated_home) == []
    source_bytes = source_config.read_bytes()
    assert (generated_home / "config.toml").read_bytes() == source_bytes
    config = tomllib.loads(source_bytes.decode("utf-8"))
    assert config["foreign"] == {"owner": "user"}
    assert config["hooks"]
    assert not (ambient_home / "config.toml").exists()
    return config, generated_home, project, decoy_cwd


def _installed_command(
    config: dict[str, object],
    event_type: str,
    logical_name: str,
) -> str:
    hooks = config["hooks"]
    assert isinstance(hooks, dict)
    entries = hooks[event_type]
    assert isinstance(entries, list)
    commands = [
        hook["command"]
        for entry in entries
        for hook in entry["hooks"]
        if logical_name in hook["command"]
    ]
    assert len(commands) == 1
    return commands[0]


def _invoke_dispatcher(
    command: str,
    payload: dict[str, object],
    *,
    cwd: Path,
    headless: bool,
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "AUTOSKILLIT_AGENT_BACKEND": "codex"}
    if headless:
        env["AUTOSKILLIT_HEADLESS"] = "1"
    else:
        env.pop("AUTOSKILLIT_HEADLESS", None)
    return subprocess.run(
        shlex.split(command),
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
        timeout=_TIMEOUT_SECONDS,
        check=False,
    )


@pytest.mark.parametrize("headless", [False, True])
def test_snapshotted_session_start_owner_reclaims_from_payload_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    headless: bool,
) -> None:
    config, generated_home, project, decoy_cwd = _snapshot_codex_hooks(
        tmp_path,
        monkeypatch,
    )
    artifact = _seed_due_capture(project)
    command = _installed_command(
        config,
        "SessionStart",
        "capture_lifecycle_hook",
    )

    completed = _invoke_dispatcher(
        command,
        {"cwd": str(project)},
        cwd=decoy_cwd,
        headless=headless,
    )

    assert completed.returncode == 0
    assert completed.stdout == ""
    assert completed.stderr == ""
    assert not artifact.exists()
    assert not (decoy_cwd / ".autoskillit").exists()
    assert generated_home != project


@pytest.mark.parametrize("headless", [False, True])
def test_snapshotted_runner_tail_reclaims_after_producer_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    headless: bool,
) -> None:
    config, generated_home, project, decoy_cwd = _snapshot_codex_hooks(
        tmp_path,
        monkeypatch,
    )
    due_artifact = _seed_due_capture(project)
    hook_command = _installed_command(
        config,
        "PreToolUse",
        "shell_capture_hook",
    )

    rewritten = _invoke_dispatcher(
        hook_command,
        {
            "cwd": str(project),
            "turn_id": "installed-lifecycle",
            "tool_input": {
                "command": ("python3 -c \"import os; os.write(1, b'installed-runner-' * 1000)\"")
            },
        },
        cwd=decoy_cwd,
        headless=headless,
    )
    assert rewritten.returncode == 0
    runner_command = json.loads(rewritten.stdout)["hookSpecificOutput"]["updatedInput"]["command"]

    completed = subprocess.run(
        ["bash", "-c", runner_command],
        capture_output=True,
        text=True,
        cwd=decoy_cwd,
        timeout=_TIMEOUT_SECONDS,
        check=False,
    )

    assert completed.returncode == 0
    assert not due_artifact.exists()
    runner_argv = shlex.split(runner_command.splitlines()[-1])
    capture_id = runner_argv[-1]
    authority = assert_shell_capture_marker_authority(
        completed.stdout,
        project,
        capture_id,
        sentinels=(b"installed-runner-",),
    )
    assert authority.capture_bytes == b"installed-runner-" * 1000
    capture_root = project.joinpath(*CAPTURE_PATH_COMPONENTS)
    retained = list(capture_root.glob("shell_*.log"))
    assert len(retained) == 1
    assert not (decoy_cwd / ".autoskillit").exists()
    assert generated_home != project

    reference_expiry = authority.manifest.reference_expiry
    assert reference_expiry is not None
    clock = _Clock(authority.manifest.finalized_at)
    anchor = open_project_anchor(str(project))
    root = open_capture_root(anchor, create=False)
    lifecycle = CaptureLifecycleStore.from_open_authorities(
        anchor,
        root,
        wall_clock=clock,
        monotonic=clock,
    )
    try:
        clock.value = (
            max(
                reference_expiry,
                authority.manifest.retention_deadline,
            )
            + 1.0
        )
        outcome = lifecycle.sweep()
    finally:
        root.close()
        anchor.close()
    assert outcome.deleted == 1
    assert outcome.errors == 0
    assert not retained[0].exists()
