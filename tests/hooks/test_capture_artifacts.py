"""Tests for descriptor-anchored shell-capture authority."""

from __future__ import annotations

import base64
import errno
import importlib
import json
import os
import shlex
import stat
import subprocess
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import pytest

import autoskillit.hooks._capture._replay as capture_replay
import autoskillit.hooks._capture_artifacts as capture_artifacts
from autoskillit.hooks._capture._snapshot import (
    CaptureMeasurement,
    CommandOutcome,
    verify_capture_snapshot,
)
from autoskillit.hooks._capture._types import (
    CaptureCleanupOutcome,
    CleanupBlocker,
    CleanupProgress,
    LockContended,
)
from autoskillit.hooks._capture_artifacts import (
    CAPTURE_PATH_COMPONENTS,
    CapturePolicy,
    CaptureSetupError,
    create_capture_artifact,
    open_capture_lifecycle,
    open_capture_root,
    open_project_anchor,
    read_capture_policy,
    run_capture,
    verify_reference_publication_binding,
)
from autoskillit.hooks._capture_contract import (
    CAPTURE_REQUEST_PROTOCOL_VERSION,
    NATIVE_SHELL_CAPTURE_MODE_ENV_VAR,
    PROTECTED_CAPTURE_ENV_VARS,
    CaptureFailureReason,
    CaptureFailureV3,
    CaptureLineageRef,
    CaptureRequest,
    CaptureV2Fields,
    encode_capture_request,
    parse_capture_degraded_v3,
    parse_capture_failure_v3,
    parse_capture_v2,
)
from autoskillit.hooks._capture_lifecycle import (
    CaptureCapacityError,
    CaptureCapacityReason,
    CaptureDeliveryStatus,
    CaptureLifecycleError,
    CaptureLifecycleRecord,
    CaptureLifecycleStore,
    CaptureReferenceStatus,
    CaptureState,
    CaptureTransitionCommittedError,
)

capture_authority = importlib.import_module(capture_artifacts.open_project_anchor.__module__)

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.medium]

_CAPTURE_ID = "0123456789abcdef"


def _capture_dir(project: Path) -> Path:
    return project.joinpath(*CAPTURE_PATH_COMPONENTS)


def _capture_record(project: Path) -> CaptureLifecycleRecord:
    with open_capture_lifecycle(str(project), create=False) as lifecycle:
        record = lifecycle.get_record(_CAPTURE_ID)
    assert record is not None
    return record


def _single_v2_marker(output: str) -> CaptureV2Fields:
    candidates = [
        line.encode()
        for line in output.splitlines()
        if line.startswith("[AutoSkillit shell capture v2:")
    ]
    assert len(candidates) == 1
    return parse_capture_v2(candidates[0])


def _single_failure_marker(output: str) -> CaptureFailureV3:
    candidates = [
        line.encode()
        for line in output.splitlines()
        if line.startswith("[AutoSkillit shell capture failure v3:")
    ]
    assert len(candidates) == 1
    return parse_capture_failure_v3(candidates[0])


def _single_degraded_marker(output: str) -> CaptureFailureV3:
    candidates = [
        line.encode()
        for line in output.splitlines()
        if line.startswith("[AutoSkillit shell capture degraded v3:")
    ]
    assert len(candidates) == 1
    return parse_capture_degraded_v3(candidates[0])


def _runner_request(
    *,
    action: str = "run",
    command: str | None = "printf ran > command_ran",
    cwd: str = "/abs/project",
    capture_id: str = _CAPTURE_ID,
    mode: str = "capture",
    attempt_id: str | None = None,
    lineage_ref: CaptureLineageRef | None = None,
) -> CaptureRequest:
    return CaptureRequest(
        protocol_version=CAPTURE_REQUEST_PROTOCOL_VERSION,
        action=action,
        mode=mode,
        attempt_id=attempt_id,
        lineage_ref=lineage_ref,
        cwd=cwd,
        capture_id=capture_id,
        command=command if action == "run" else None,
    )


def _runner_args(**changes: object) -> list[str]:
    return [encode_capture_request(_runner_request(**changes))]  # type: ignore[arg-type]


def _open_authority(project: Path):
    anchor = open_project_anchor(str(project))
    try:
        root = open_capture_root(anchor, create=True)
    except BaseException:
        anchor.close()
        raise
    return anchor, root


def _create_artifact(anchor, root, capture_id: str = _CAPTURE_ID):
    lifecycle = CaptureLifecycleStore.from_open_authorities(anchor, root)
    return create_capture_artifact(root, capture_id, lifecycle)


def _finalize_artifact(anchor, root, artifact, data: bytes = b"captured"):
    lifecycle = CaptureLifecycleStore.from_open_authorities(anchor, root)
    os.write(artifact.fd, data)
    verified = verify_capture_snapshot(
        fd=artifact.fd,
        capture_id=artifact.authority.capture_id,
        incarnation=artifact.authority.incarnation,
        project_identity=(anchor.identity.device, anchor.identity.inode),
        root_identity=(root.identity.device, root.identity.inode),
        carrier_name=artifact.name,
        carrier_identity=(artifact.identity.device, artifact.identity.inode),
        measurement=CaptureMeasurement.from_bytes(data, inline_bytes=8),
        command_outcome=CommandOutcome.exited(0),
        expected_revision=artifact.authority.expected_revision,
        finalized_at=1_000_000.0,
        retention_deadline=1_003_600.0,
    )
    return lifecycle.commit_verified_snapshot(verified, issue_reference=True)


def _issue_artifact(anchor, root, artifact, data: bytes = b"captured"):
    finalized = _finalize_artifact(anchor, root, artifact, data)
    assert finalized.issuance is not None
    return finalized.issuance


def test_capture_authorities_are_factory_only_and_externally_immutable(tmp_path: Path) -> None:
    identity = capture_artifacts.FileIdentity(device=1, inode=2)
    with pytest.raises(CaptureSetupError, match="open_project_anchor"):
        capture_artifacts.ProjectAnchor(
            fd=-1,
            identity=identity,
            supplied_path=str(tmp_path),
            physical_path=tmp_path,
        )
    with pytest.raises(CaptureSetupError, match="open_capture_root"):
        capture_artifacts.CaptureRoot(
            autoskillit_fd=-1,
            temp_fd=-1,
            fd=-1,
            autoskillit_identity=identity,
            temp_identity=identity,
            identity=identity,
        )
    with pytest.raises(CaptureSetupError, match="create_capture_artifact"):
        capture_artifacts.CaptureArtifact(
            fd=-1,
            name="shell.log",
            identity=identity,
            lease_fd=-1,
            authority=None,  # type: ignore[arg-type]
        )

    anchor = open_project_anchor(str(tmp_path))
    try:
        with pytest.raises(FrozenInstanceError):
            setattr(anchor, "supplied_path", "/unvalidated")
    finally:
        anchor.close()


def test_capture_artifact_partial_close_keeps_writer_lease_explicit(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    anchor, root = _open_authority(project)
    artifact = _create_artifact(anchor, root)
    lease_fd = artifact.lease_fd
    try:
        artifact.close_artifact_fd()
        assert artifact.fd == -1
        os.fstat(lease_fd)
    finally:
        artifact.release_lease()
        root.close()
        anchor.close()


class _ReadableStream:
    def __init__(self, value: bytes) -> None:
        self._value = value
        self.closed = False

    def read(self, _size: int) -> bytes:
        value, self._value = self._value, b""
        return value

    def close(self) -> None:
        self.closed = True


class _FakeCaptureProcess:
    def __init__(self, value: bytes) -> None:
        self.stdout = _ReadableStream(value)
        self.terminated = False
        self.killed = False
        self.wait_calls = 0

    def poll(self) -> None:
        return None

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.wait_calls += 1
        return 0


def _record_artifact_fds(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    observed_fds: list[int] = []
    real_create = capture_artifacts.create_capture_artifact

    def record_artifact(root, capture_id, lifecycle):
        artifact = real_create(root, capture_id, lifecycle)
        observed_fds.append(artifact.fd)
        return artifact

    monkeypatch.setattr(capture_artifacts, "create_capture_artifact", record_artifact)
    return observed_fds


def test_settle_failed_capture_preserves_raw_kill_result_after_timeout() -> None:
    class StubbornProcess(_FakeCaptureProcess):
        def __init__(self) -> None:
            super().__init__(b"")
            self.wait_timeouts: list[float | None] = []

        def wait(self, timeout: float | None = None) -> int:
            self.wait_timeouts.append(timeout)
            if len(self.wait_timeouts) == 1:
                raise subprocess.TimeoutExpired("capture", timeout)
            return -9

    process = StubbornProcess()

    settlement = capture_replay.settle_failed_capture(process)
    assert settlement.action == "killed"
    assert settlement.returncode == -9
    assert process.terminated
    assert process.killed
    assert process.wait_timeouts == [
        capture_replay._PROCESS_SETTLE_TIMEOUT_SECONDS,
        capture_replay._PROCESS_SETTLE_TIMEOUT_SECONDS,
    ]


def _record_runtime_fds(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    observed_fds = _record_owned_capture_fds(monkeypatch)
    real_duplicate = capture_artifacts._duplicate_artifact_writer

    def record_duplicate(artifact):
        writer_fd = real_duplicate(artifact)
        observed_fds.append(writer_fd)
        return writer_fd

    monkeypatch.setattr(capture_artifacts, "_duplicate_artifact_writer", record_duplicate)
    return observed_fds


def _record_owned_capture_fds(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    observed_fds: list[int] = []
    real_open_anchor = capture_artifacts.open_project_anchor
    real_open_root = capture_artifacts.open_capture_root
    real_create = capture_artifacts.create_capture_artifact

    def record_anchor(cwd):
        anchor = real_open_anchor(cwd)
        observed_fds.append(anchor.fd)
        return anchor

    def record_root(anchor, *, create):
        root = real_open_root(anchor, create=create)
        observed_fds.extend((root.autoskillit_fd, root.temp_fd, root.fd))
        return root

    def record_artifact(root, capture_id, lifecycle):
        artifact = real_create(root, capture_id, lifecycle)
        observed_fds.append(artifact.fd)
        return artifact

    monkeypatch.setattr(capture_artifacts, "open_project_anchor", record_anchor)
    monkeypatch.setattr(capture_artifacts, "open_capture_root", record_root)
    monkeypatch.setattr(capture_authority, "open_project_anchor", record_anchor)
    monkeypatch.setattr(capture_authority, "open_capture_root", record_root)
    monkeypatch.setattr(capture_artifacts, "create_capture_artifact", record_artifact)
    return observed_fds


def test_project_anchor_accepts_symlink_cwd(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    supplied_cwd = tmp_path / "project-link"
    supplied_cwd.symlink_to(project, target_is_directory=True)

    anchor, root = _open_authority(supplied_cwd)
    artifact = _create_artifact(anchor, root)
    issuance = _issue_artifact(anchor, root, artifact)
    try:
        assert anchor.physical_path == project.resolve()
        assert _capture_dir(project).is_dir()
        assert verify_reference_publication_binding(
            anchor,
            root,
            artifact,
            issuance,
        )
    finally:
        artifact.close_artifact_fd()
        artifact.release_lease()
        root.close()
        anchor.close()


def test_symlinked_cwd_is_opened_before_path_derivation_and_descendants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    supplied_cwd = tmp_path / "project-link"
    supplied_cwd.symlink_to(project, target_is_directory=True)
    events: list[tuple[str, int | None]] = []
    real_open = capture_artifacts.os.open
    real_realpath = capture_artifacts.os.path.realpath

    def track_open(path, flags, mode=0o777, *, dir_fd=None):
        fd = real_open(path, flags, mode, dir_fd=dir_fd)
        if os.fspath(path) == str(supplied_cwd) and dir_fd is None:
            events.append(("project", None))
        elif path in CAPTURE_PATH_COMPONENTS:
            events.append((os.fspath(path), dir_fd))
        return fd

    def track_realpath(path):
        events.append(("derive", None))
        return real_realpath(path)

    monkeypatch.setattr(capture_authority, "_require_capabilities", lambda: None)
    monkeypatch.setattr(capture_authority.os, "open", track_open)
    monkeypatch.setattr(capture_authority.os.path, "realpath", track_realpath)

    anchor, root = _open_authority(supplied_cwd)
    try:
        assert events[:2] == [("project", None), ("derive", None)]
        assert events[2:] == [
            (CAPTURE_PATH_COMPONENTS[0], anchor.fd),
            (CAPTURE_PATH_COMPONENTS[1], root.autoskillit_fd),
            (CAPTURE_PATH_COMPONENTS[2], root.temp_fd),
        ]
    finally:
        root.close()
        anchor.close()


def test_capability_probe_requires_descriptor_relative_stat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supported = set(capture_authority.os.supports_dir_fd)
    supported.discard(capture_authority.os.stat)
    monkeypatch.setattr(capture_authority.os, "supports_dir_fd", supported)

    with pytest.raises(CaptureSetupError, match="filesystem primitives unavailable"):
        capture_authority._require_capabilities()


def test_capability_probe_requires_exclusive_creation_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(capture_authority.os, "O_EXCL", 0)

    with pytest.raises(CaptureSetupError, match="filesystem primitives unavailable"):
        capture_authority._require_capabilities()


def test_capability_probe_requires_descriptor_relative_unlink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supported = set(capture_authority.os.supports_dir_fd)
    supported.discard(capture_authority.os.unlink)
    monkeypatch.setattr(capture_authority.os, "supports_dir_fd", supported)

    with pytest.raises(CaptureSetupError, match="filesystem primitives unavailable"):
        capture_authority._require_capabilities()


@pytest.mark.parametrize("component", CAPTURE_PATH_COMPONENTS)
def test_capture_root_rejects_symlinked_components(component: str, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    external = tmp_path / "external"
    external.mkdir()

    parent = project
    for name in CAPTURE_PATH_COMPONENTS:
        candidate = parent / name
        if name == component:
            candidate.symlink_to(external, target_is_directory=True)
            break
        candidate.mkdir()
        parent = candidate

    anchor = open_project_anchor(str(project))
    try:
        with pytest.raises(CaptureSetupError):
            open_capture_root(anchor, create=True)
    finally:
        anchor.close()


@pytest.mark.parametrize("missing_component", CAPTURE_PATH_COMPONENTS)
def test_capture_root_rejects_missing_components(missing_component: str, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    parent = project
    for name in CAPTURE_PATH_COMPONENTS:
        if name == missing_component:
            break
        parent = parent / name
        parent.mkdir()

    anchor = open_project_anchor(str(project))
    try:
        with pytest.raises(CaptureSetupError, match="missing capture path component"):
            open_capture_root(anchor, create=False)
    finally:
        anchor.close()


@pytest.mark.parametrize("unsafe_component", CAPTURE_PATH_COMPONENTS)
@pytest.mark.parametrize("unsafe_mode", [0o770, 0o707])
def test_capture_root_rejects_writable_components(
    unsafe_component: str,
    unsafe_mode: int,
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    parent = project
    for name in CAPTURE_PATH_COMPONENTS:
        parent = parent / name
        parent.mkdir()
        if name == unsafe_component:
            parent.chmod(unsafe_mode)

    anchor = open_project_anchor(str(project))
    try:
        with pytest.raises(CaptureSetupError, match="unsafe ownership or mode"):
            open_capture_root(anchor, create=True)
    finally:
        anchor.close()


@pytest.mark.parametrize("blocking_component", CAPTURE_PATH_COMPONENTS)
def test_capture_root_rejects_blocking_regular_file_components(
    blocking_component: str, tmp_path: Path
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    parent = project
    for name in CAPTURE_PATH_COMPONENTS:
        candidate = parent / name
        if name == blocking_component:
            candidate.write_text("blocking file")
            break
        candidate.mkdir()
        parent = candidate

    anchor = open_project_anchor(str(project))
    try:
        with pytest.raises(CaptureSetupError, match="unsafe capture path component"):
            open_capture_root(anchor, create=True)
    finally:
        anchor.close()


def test_symlinked_policy_root_is_not_trusted(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    autoskillit_dir = project / CAPTURE_PATH_COMPONENTS[0]
    autoskillit_dir.mkdir()
    external_temp = tmp_path / "external-temp"
    external_temp.mkdir()
    (external_temp / ".hook_config.json").write_text(
        json.dumps({"output_budget_policy": {"disabled": True}})
    )
    (autoskillit_dir / CAPTURE_PATH_COMPONENTS[1]).symlink_to(
        external_temp, target_is_directory=True
    )

    anchor = open_project_anchor(str(project))
    try:
        assert read_capture_policy(anchor) == CapturePolicy()
        with pytest.raises(CaptureSetupError):
            open_capture_root(anchor, create=True)
    finally:
        anchor.close()


@pytest.mark.parametrize(
    "policy_filename",
    [".hook_config.json", ".hook_config_overlay.json"],
)
def test_symlinked_policy_leaf_is_not_trusted(
    policy_filename: str,
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    temp_dir = project.joinpath(*CAPTURE_PATH_COMPONENTS[:2])
    temp_dir.mkdir(parents=True)
    external_config = tmp_path / "external-config.json"
    external_config.write_text(json.dumps({"output_budget_policy": {"disabled": True}}))
    (temp_dir / policy_filename).symlink_to(external_config)

    anchor = open_project_anchor(str(project))
    try:
        assert read_capture_policy(anchor) == CapturePolicy()
    finally:
        anchor.close()


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        pytest.param(1, 1, id="minimum-valid"),
        pytest.param(31, 31, id="ordinary-valid"),
        pytest.param(1_000_000, 1_000_000, id="maximum-valid"),
        pytest.param(1_000_001, 1_000_000, id="clamped-above-maximum"),
        pytest.param(0, CapturePolicy().inline_bytes, id="zero-defaults"),
        pytest.param(-1, CapturePolicy().inline_bytes, id="negative-defaults"),
        pytest.param(True, CapturePolicy().inline_bytes, id="boolean-defaults"),
        pytest.param("31", CapturePolicy().inline_bytes, id="non-integer-defaults"),
    ],
)
def test_verified_policy_merges_overlay_and_bounds_inline_bytes(
    configured: object,
    expected: int,
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    temp_dir = project.joinpath(*CAPTURE_PATH_COMPONENTS[:2])
    temp_dir.mkdir(parents=True)
    (temp_dir / ".hook_config.json").write_text(
        json.dumps(
            {
                "output_budget_policy": {
                    "disabled": False,
                    "shell_max_inline_bytes": configured,
                }
            }
        )
    )
    (temp_dir / ".hook_config_overlay.json").write_text(
        json.dumps({"output_budget_policy": {"disabled": True}})
    )

    anchor = open_project_anchor(str(project))
    try:
        assert read_capture_policy(anchor) == CapturePolicy(disabled=True, inline_bytes=expected)
    finally:
        anchor.close()


@pytest.mark.parametrize("policy_source", ["base", "overlay"])
@pytest.mark.parametrize("policy_disabled", [False, True])
@pytest.mark.parametrize("requested_mode", ["capture", "direct"])
def test_runner_policy_precedence_cross_product(
    requested_mode: str,
    policy_disabled: bool,
    policy_source: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    temp_dir = project.joinpath(*CAPTURE_PATH_COMPONENTS[:2])
    temp_dir.mkdir(parents=True)
    base_disabled = not policy_disabled if policy_source == "overlay" else policy_disabled
    (temp_dir / ".hook_config.json").write_text(
        json.dumps({"output_budget_policy": {"disabled": base_disabled}})
    )
    if policy_source == "overlay":
        (temp_dir / ".hook_config_overlay.json").write_text(
            json.dumps({"output_budget_policy": {"disabled": policy_disabled}})
        )

    project_stat = project.stat()
    reference = CaptureLineageRef(
        schema_version=1,
        launch_id="a" * 32,
        lineage_digest="b" * 64,
        lineage_anchor=str(project),
        anchor_device=project_stat.st_dev,
        anchor_inode=project_stat.st_ino,
    )
    observations: list[dict[str, object]] = []
    monkeypatch.setattr(
        capture_artifacts,
        "validate_lineage_reference",
        lambda supplied_ref, supplied_attempt: (
            supplied_ref == reference and supplied_attempt == "c" * 32
        ),
    )

    def record_observation(
        supplied_ref: CaptureLineageRef,
        supplied_attempt: str,
        **values: object,
    ) -> bool:
        assert supplied_ref == reference
        assert supplied_attempt == "c" * 32
        observations.append(values)
        return True

    monkeypatch.setattr(
        capture_artifacts,
        "record_runner_observation",
        record_observation,
    )

    assert (
        run_capture(
            ":",
            str(project),
            _CAPTURE_ID,
            requested_mode=requested_mode,
            attempt_id="c" * 32,
            lineage_ref=reference,
        )
        == 0
    )

    launch_direct = requested_mode == "direct"
    effective_direct = launch_direct or policy_disabled
    expected_reason = (
        "launch_authorized_direct"
        if launch_direct
        else "project_policy_disabled"
        if policy_disabled
        else "capture_enabled"
    )
    assert observations == [
        {
            "effective_mode": "direct" if effective_direct else "capture",
            "reason": expected_reason,
            "project_policy_disabled": policy_disabled,
        }
    ]
    assert _capture_dir(project).exists() is not effective_direct


@pytest.mark.parametrize("requested_mode", ["capture", "direct"])
def test_runner_observation_failure_prevents_command_execution(
    requested_mode: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    project_stat = project.stat()
    reference = CaptureLineageRef(
        schema_version=1,
        launch_id="a" * 32,
        lineage_digest="b" * 64,
        lineage_anchor=str(project),
        anchor_device=project_stat.st_dev,
        anchor_inode=project_stat.st_ino,
    )
    monkeypatch.setattr(
        capture_artifacts,
        "validate_lineage_reference",
        lambda *_args: True,
    )
    monkeypatch.setattr(
        capture_artifacts,
        "record_runner_observation",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        capture_artifacts,
        "_spawn_bash",
        lambda *_args, **_kwargs: pytest.fail("command must not execute"),
    )

    with pytest.raises(CaptureSetupError, match="runner observation recording failed"):
        run_capture(
            ":",
            str(project),
            _CAPTURE_ID,
            requested_mode=requested_mode,
            attempt_id="c" * 32,
            lineage_ref=reference,
        )


def test_policy_partial_open_failure_closes_autoskillit_fd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    autoskillit_dir = project / CAPTURE_PATH_COMPONENTS[0]
    autoskillit_dir.mkdir(parents=True)
    anchor = open_project_anchor(str(project))
    opened_fds: list[int] = []
    real_open_component = capture_artifacts._open_directory_component

    def record_open_component(parent_fd, name, *, create):
        fd = real_open_component(parent_fd, name, create=create)
        if name == CAPTURE_PATH_COMPONENTS[0]:
            opened_fds.append(fd)
        return fd

    monkeypatch.setattr(
        capture_artifacts,
        "_open_directory_component",
        record_open_component,
    )

    try:
        assert read_capture_policy(anchor) == CapturePolicy()
        assert len(opened_fds) == 1
        with pytest.raises(OSError):
            os.fstat(opened_fds[0])
    finally:
        anchor.close()


@pytest.mark.parametrize("collision", ["symlink", "hardlink", "regular"])
def test_artifact_creation_rejects_existing_entries(collision: str, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    anchor, root = _open_authority(project)
    artifact_path = _capture_dir(project) / f"shell_{_CAPTURE_ID}.log"
    external = tmp_path / "external-secret"
    external.write_bytes(b"must-survive")
    try:
        if collision == "symlink":
            artifact_path.symlink_to(external)
        elif collision == "hardlink":
            try:
                os.link(external, artifact_path)
            except OSError:
                pytest.skip("hardlinks unavailable")
        else:
            artifact_path.write_bytes(b"existing")

        with pytest.raises(CaptureSetupError):
            _create_artifact(anchor, root)

        assert external.read_bytes() == b"must-survive"
        if collision == "regular":
            assert artifact_path.read_bytes() == b"existing"
    finally:
        root.close()
        anchor.close()


def test_publication_requires_current_directory_binding(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    anchor, root = _open_authority(project)
    artifact = _create_artifact(anchor, root)
    issuance = _issue_artifact(anchor, root, artifact)
    capture_dir = _capture_dir(project)
    displaced = capture_dir.with_name("shell_capture-displaced")
    try:
        capture_dir.rename(displaced)
        capture_dir.mkdir()
        assert not verify_reference_publication_binding(
            anchor,
            root,
            artifact,
            issuance,
        )
    finally:
        artifact.close_artifact_fd()
        artifact.release_lease()
        root.close()
        anchor.close()


def test_publication_rederives_symlinked_project_path(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    supplied_cwd = tmp_path / "project-link"
    supplied_cwd.symlink_to(project, target_is_directory=True)
    anchor, root = _open_authority(supplied_cwd)
    artifact = _create_artifact(anchor, root)
    issuance = _issue_artifact(anchor, root, artifact)
    try:
        supplied_cwd.unlink()
        supplied_cwd.symlink_to(replacement, target_is_directory=True)

        assert not verify_reference_publication_binding(
            anchor,
            root,
            artifact,
            issuance,
        )
    finally:
        artifact.close_artifact_fd()
        artifact.release_lease()
        root.close()
        anchor.close()


def test_marker_directory_identity_failure_closes_partial_open_fd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    autoskillit_dir = project / CAPTURE_PATH_COMPONENTS[0]
    autoskillit_dir.mkdir(parents=True)
    anchor = open_project_anchor(str(project))
    opened_fds: list[int] = []
    real_open_component = capture_artifacts._open_directory_component

    def record_open_component(parent_fd, name, *, create):
        fd = real_open_component(parent_fd, name, create=create)
        opened_fds.append(fd)
        return fd

    def fail_identity(_fd, _expected):
        raise OSError("fault injection")

    monkeypatch.setattr(
        capture_artifacts,
        "_open_directory_component",
        record_open_component,
    )
    monkeypatch.setattr(capture_artifacts, "_same_identity", fail_identity)

    try:
        with pytest.raises(OSError, match="fault injection"):
            capture_artifacts._open_and_match_directory(
                anchor.fd,
                CAPTURE_PATH_COMPONENTS[0],
                anchor.identity,
            )
        assert len(opened_fds) == 1
        with pytest.raises(OSError):
            os.fstat(opened_fds[0])
    finally:
        anchor.close()


def test_setup_failure_prevents_user_command_and_emits_failure_marker(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    temp_dir = project.joinpath(*CAPTURE_PATH_COMPONENTS[:2])
    temp_dir.mkdir(parents=True)
    (temp_dir / CAPTURE_PATH_COMPONENTS[2]).write_text("blocking file")
    assert (
        capture_artifacts._main(_runner_args(command="printf ran > command_ran", cwd=str(project)))
        == 1
    )
    captured = capfd.readouterr()
    assert '"status":"capture_failed"' in captured.err
    assert "shell capture v2:" not in captured.out + captured.err
    assert not (project / "command_ran").exists()


@pytest.mark.parametrize("root_shape", ["blocking_file", "symlink"])
def test_validated_direct_bypasses_unusable_capture_root_without_artifact_or_lease(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    root_shape: str,
) -> None:
    project = tmp_path / "project"
    capture_parent = project.joinpath(*CAPTURE_PATH_COMPONENTS[:-1])
    capture_parent.mkdir(parents=True)
    capture_root = capture_parent / CAPTURE_PATH_COMPONENTS[-1]
    if root_shape == "blocking_file":
        capture_root.write_text("must remain a file", encoding="utf-8")
    else:
        external = tmp_path / "external-capture-root"
        external.mkdir()
        capture_root.symlink_to(external, target_is_directory=True)

    project_stat = project.stat()
    reference = CaptureLineageRef(
        schema_version=1,
        launch_id="a" * 32,
        lineage_digest="b" * 64,
        lineage_anchor=str(project),
        anchor_device=project_stat.st_dev,
        anchor_inode=project_stat.st_ino,
    )
    monkeypatch.setattr(
        capture_artifacts,
        "validate_lineage_reference",
        lambda supplied_ref, supplied_attempt: (
            supplied_ref == reference and supplied_attempt == "c" * 32
        ),
    )
    monkeypatch.setattr(
        capture_artifacts,
        "record_runner_observation",
        lambda *_args, **_kwargs: True,
    )

    sentinel = project / "direct-ran"
    assert (
        run_capture(
            f"printf direct > {shlex.quote(str(sentinel))}",
            str(project),
            _CAPTURE_ID,
            requested_mode="direct",
            attempt_id="c" * 32,
            lineage_ref=reference,
        )
        == 0
    )

    assert sentinel.read_text(encoding="utf-8") == "direct"
    assert not list(capture_root.glob("shell_*.log"))
    if root_shape == "blocking_file":
        assert capture_root.read_text(encoding="utf-8") == "must remain a file"
    else:
        assert capture_root.is_symlink()
        assert not list(capture_root.iterdir())


def test_direct_control_flow_exception_settles_and_reraises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    project_stat = project.stat()
    reference = CaptureLineageRef(
        schema_version=1,
        launch_id="a" * 32,
        lineage_digest="b" * 64,
        lineage_anchor=str(project),
        anchor_device=project_stat.st_dev,
        anchor_inode=project_stat.st_ino,
    )

    class InterruptingProcess(_FakeCaptureProcess):
        def wait(self, timeout: float | None = None) -> int:
            del timeout
            raise KeyboardInterrupt

    process = InterruptingProcess(b"")
    settled: list[object] = []
    monkeypatch.setattr(
        capture_artifacts,
        "validate_lineage_reference",
        lambda *_args: True,
    )
    monkeypatch.setattr(
        capture_artifacts,
        "record_runner_observation",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        capture_artifacts,
        "_spawn_bash",
        lambda *_args, **_kwargs: process,
    )
    monkeypatch.setattr(
        capture_artifacts,
        "_own_spawned_process",
        lambda spawned, *, capture_output: spawned,
    )
    monkeypatch.setattr(
        capture_artifacts,
        "_settle_failed_capture",
        lambda supplied: settled.append(supplied),
    )

    with pytest.raises(KeyboardInterrupt):
        run_capture(
            ":",
            str(project),
            _CAPTURE_ID,
            requested_mode="direct",
            attempt_id="c" * 32,
            lineage_ref=reference,
        )

    assert settled == [process]
    assert process.stdout.closed


@pytest.mark.parametrize("reason", tuple(CaptureFailureReason))
def test_setup_failure_reason_survives_runner_transport_without_sensitive_detail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
    reason: CaptureFailureReason,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    sentinel = project / "command_ran"
    command = f"printf ran > {sentinel}"
    sensitive = f"sensitive cause at {project} for {_CAPTURE_ID}"

    def fail_create(*_args, **_kwargs):
        raise CaptureSetupError(reason, "capture setup failed") from RuntimeError(sensitive)

    monkeypatch.setattr(capture_artifacts, "create_capture_artifact", fail_create)

    assert capture_artifacts._main(_runner_args(command=command, cwd=str(project))) == 1

    captured = capfd.readouterr()
    failure = _single_failure_marker(captured.err)
    assert failure.reason is reason
    assert failure.detail == "capture setup failed"
    assert sensitive not in captured.err
    assert str(project) not in captured.err
    assert _CAPTURE_ID not in captured.err
    assert not sentinel.exists()


def test_recovery_contention_is_classified_at_the_runner_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    sentinel = project / "command_ran"

    def contend(*_args, **_kwargs):
        raise LockContended

    monkeypatch.setattr(capture_artifacts, "create_capture_artifact", contend)

    assert (
        capture_artifacts._main(_runner_args(command=f"printf ran > {sentinel}", cwd=str(project)))
        == 1
    )

    failure = _single_failure_marker(capfd.readouterr().err)
    assert failure.reason is CaptureFailureReason.RECOVERY_CONTENDED
    assert not sentinel.exists()


@pytest.mark.parametrize("capture_id", ["", "0123456789abcde", "0123456789abcdeg"])
def test_reject_mode_validates_capture_id(
    capture_id: str,
    capfd: pytest.CaptureFixture[str],
) -> None:
    request = _runner_request(action="reject", command=None)
    raw = json.loads(base64.b64decode(encode_capture_request(request), validate=True))
    raw["capture_id"] = capture_id
    encoded = base64.b64encode(
        json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
    ).decode()
    assert capture_artifacts._main([encoded]) == 1
    captured = capfd.readouterr()
    assert "invalid capture runner invocation" in captured.err
    assert "capture request rejected before command execution" not in captured.err


def test_valid_reject_runs_one_runner_tail_sweep(
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    events: list[str] = []

    def reconcile(requested_cwd, budget):
        assert requested_cwd == "/abs/project"
        assert budget is capture_artifacts._capture_reconcile.RUNNER_TAIL_BUDGET
        events.append("reconcile")
        return CaptureCleanupOutcome()

    monkeypatch.setattr(
        capture_artifacts._capture_reconcile,
        "reconcile_capture_store",
        reconcile,
    )

    assert capture_artifacts._main(_runner_args(action="reject", command=None)) == 1
    assert events == ["reconcile"]
    assert "capture request rejected before command execution" in capfd.readouterr().err


def test_runner_tail_consumes_byte_pressure_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budgets = []

    def reconcile(_requested_cwd, budget):
        budgets.append(budget)
        return CaptureCleanupOutcome()

    monkeypatch.setattr(capture_artifacts, "_BYTE_PRESSURE_OBSERVED", True)
    monkeypatch.setattr(
        capture_artifacts._capture_reconcile,
        "reconcile_capture_store",
        reconcile,
    )

    capture_artifacts._sweep_after_runner("/abs/project")
    capture_artifacts._sweep_after_runner("/abs/project")

    assert budgets == [
        capture_artifacts._capture_types.TRANSITION_RESCUE_BUDGET,
        capture_artifacts._capture_reconcile.RUNNER_TAIL_BUDGET,
    ]


def test_runner_tail_preserves_dispatch_result_and_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    def dispatch(*_args) -> int:
        events.append("dispatch")
        return 37

    def reconcile(_requested_cwd, _budget):
        events.append("reconcile")
        return CaptureCleanupOutcome()

    monkeypatch.setattr(capture_artifacts, "_dispatch_runner", dispatch)
    monkeypatch.setattr(
        capture_artifacts._capture_reconcile,
        "reconcile_capture_store",
        reconcile,
    )

    assert capture_artifacts._main(_runner_args()) == 37
    assert events == ["dispatch", "reconcile"]


def test_runner_tail_cleanup_failure_does_not_replace_user_result(
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(capture_artifacts, "_dispatch_runner", lambda *_args: 23)

    def fail_reconcile(_requested_cwd, _budget):
        raise RuntimeError("🔥" * 512)

    monkeypatch.setattr(
        capture_artifacts._capture_reconcile,
        "reconcile_capture_store",
        fail_reconcile,
    )

    assert capture_artifacts._main(_runner_args()) == 23
    captured = capfd.readouterr()
    assert captured.out == ""
    assert "failed" in captured.err
    assert "runner-tail reconciliation raised an unexpected exception" in captured.err
    assert len(captured.err.encode("utf-8")) <= 512


def test_runner_tail_reports_sweep_outcome_errors(
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(capture_artifacts, "_dispatch_runner", lambda *_args: 23)
    monkeypatch.setattr(
        capture_artifacts._capture_reconcile,
        "reconcile_capture_store",
        lambda requested_cwd, budget: CaptureCleanupOutcome(
            errors=2,
            remaining_due=1,
            blocker=CleanupBlocker.LEDGER_INTEGRITY,
        ),
    )

    assert capture_artifacts._main(_runner_args()) == 23
    captured = capfd.readouterr()
    assert captured.out == ""
    assert "blocker=ledger_integrity errors=2" in captured.err


def test_runner_tail_deferred_outcome_emits_nothing_per_command(
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """The incident: bounded budget progress with no errors must stay silent
    on every command, not just occasionally — DEFERRED never produces a
    per-command runner-tail message."""
    monkeypatch.setattr(capture_artifacts, "_dispatch_runner", lambda *_args: 23)
    monkeypatch.setattr(
        capture_artifacts._capture_reconcile,
        "reconcile_capture_store",
        lambda requested_cwd, budget: CaptureCleanupOutcome(
            examined=2,
            deleted=2,
            remaining_due=3,
            progress=CleanupProgress.RETIRED,
            blocker=CleanupBlocker.RECORD_BUDGET,
            errors=0,
        ),
    )

    assert capture_artifacts._main(_runner_args()) == 23
    captured = capfd.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_runner_tail_still_sweeps_after_unexpected_dispatch_exception(
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    swept: list[bool] = []

    def fail_dispatch(*_args):
        raise RuntimeError("fault injection")

    def reconcile(_requested_cwd, _budget):
        swept.append(True)
        return CaptureCleanupOutcome()

    monkeypatch.setattr(capture_artifacts, "_dispatch_runner", fail_dispatch)
    monkeypatch.setattr(
        capture_artifacts._capture_reconcile,
        "reconcile_capture_store",
        reconcile,
    )

    assert capture_artifacts._main(_runner_args()) == 1
    assert swept == [True]
    assert "capture runner failed" in capfd.readouterr().err


def test_malformed_runner_invocation_reconciles_only_with_absolute_cwd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reconciled: list[str] = []

    def reconcile(requested_cwd, _budget):
        reconciled.append(requested_cwd)
        return CaptureCleanupOutcome()

    monkeypatch.setattr(
        capture_artifacts._capture_reconcile,
        "reconcile_capture_store",
        reconcile,
    )

    assert capture_artifacts._main(["not-base64"]) == 1
    assert capture_artifacts._main([]) == 1
    assert reconciled == []


@pytest.mark.parametrize(
    ("request_mode", "ambient_mode", "attempt_id", "lineage_ref"),
    [
        ("capture", "direct", None, None),
        (
            "direct",
            "capture",
            "d" * 32,
            CaptureLineageRef(
                schema_version=1,
                launch_id="a" * 32,
                lineage_digest="b" * 64,
                lineage_anchor="/lineage/anchor",
                anchor_device=12,
                anchor_inode=34,
            ),
        ),
    ],
)
def test_dispatch_uses_only_request_mode_and_keeps_lineage_anchor_distinct_from_cwd(
    monkeypatch: pytest.MonkeyPatch,
    request_mode: str,
    ambient_mode: str,
    attempt_id: str | None,
    lineage_ref: CaptureLineageRef | None,
) -> None:
    observed: dict[str, object] = {}

    def record_run(command: str, cwd: str, capture_id: str, **kwargs: object) -> int:
        observed.update(
            command=command,
            cwd=cwd,
            capture_id=capture_id,
            **kwargs,
        )
        return 17

    monkeypatch.setenv(NATIVE_SHELL_CAPTURE_MODE_ENV_VAR, ambient_mode)
    monkeypatch.setattr(capture_artifacts, "run_capture", record_run)
    request = _runner_request(
        mode=request_mode,
        cwd="/command/cwd",
        attempt_id=attempt_id,
        lineage_ref=lineage_ref,
    )

    assert capture_artifacts._dispatch_runner(request) == 17
    assert observed["requested_mode"] == request_mode
    assert observed["attempt_id"] == attempt_id
    assert observed["lineage_ref"] == lineage_ref
    assert observed["cwd"] == "/command/cwd"
    if lineage_ref is not None:
        assert lineage_ref.lineage_anchor == "/lineage/anchor"
        assert observed["cwd"] != lineage_ref.lineage_anchor


def test_spawn_scrubs_all_protected_controls_from_user_bash_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def record_popen(*_args, **kwargs):
        observed.update(kwargs)
        return SimpleNamespace()

    for name in PROTECTED_CAPTURE_ENV_VARS:
        monkeypatch.setenv(name, f"hostile-{name}")
    monkeypatch.setenv("PHASE4_UNRELATED_ENV", "preserved")
    monkeypatch.setattr(capture_artifacts.subprocess, "Popen", record_popen)
    capture_artifacts._spawn_bash(
        capture_artifacts._resolve_bash(),
        "printf safe",
        capture_output=False,
    )

    child_environment = observed["env"]
    assert isinstance(child_environment, dict)
    assert not PROTECTED_CAPTURE_ENV_VARS.intersection(child_environment)
    assert child_environment["PHASE4_UNRELATED_ENV"] == "preserved"
    for name in PROTECTED_CAPTURE_ENV_VARS:
        assert os.environ[name] == f"hostile-{name}"


@pytest.mark.parametrize(
    "spawn_errno",
    [None, errno.EPERM, errno.E2BIG],
    ids=("success", "spawn-failure", "e2big"),
)
def test_spawn_bash_anchors_and_closes_inherited_cwd_fd(
    monkeypatch: pytest.MonkeyPatch,
    spawn_errno: int | None,
) -> None:
    inherited_cwd_fds: list[int] = []
    closed_fds: list[int] = []
    fchdir_fds: list[int] = []
    popen_kwargs: list[dict[str, object]] = []
    real_open = capture_artifacts.os.open
    real_close = capture_artifacts.os.close
    real_fchdir = capture_artifacts.os.fchdir

    def record_open(path, flags, mode=0o777, *, dir_fd=None):
        fd = real_open(path, flags, mode, dir_fd=dir_fd)
        if path == "." and dir_fd is None:
            inherited_cwd_fds.append(fd)
        return fd

    def record_close(fd):
        if fd in inherited_cwd_fds:
            closed_fds.append(fd)
        real_close(fd)

    def record_fchdir(fd):
        fchdir_fds.append(fd)
        real_fchdir(fd)

    process = SimpleNamespace()

    def record_popen(*_args, **kwargs):
        popen_kwargs.append(kwargs)
        if spawn_errno is not None:
            raise OSError(spawn_errno, "fault injection")
        return process

    monkeypatch.setattr(capture_artifacts.os, "open", record_open)
    monkeypatch.setattr(capture_artifacts.os, "close", record_close)
    monkeypatch.setattr(capture_artifacts.os, "fchdir", record_fchdir)
    monkeypatch.setattr(capture_artifacts.subprocess, "Popen", record_popen)

    if spawn_errno is None:
        assert (
            capture_artifacts._spawn_bash(
                "/bin/bash",
                "printf safe",
                capture_output=False,
            )
            is process
        )
    else:
        message = (
            "argument/environment exceeds system limit"
            if spawn_errno == errno.E2BIG
            else "cannot spawn capture shell"
        )
        with pytest.raises(CaptureSetupError, match=message):
            capture_artifacts._spawn_bash(
                "/bin/bash",
                "printf safe",
                capture_output=False,
            )

    assert len(inherited_cwd_fds) == 1
    inherited_cwd_fd = inherited_cwd_fds[0]
    assert fchdir_fds == [inherited_cwd_fd, inherited_cwd_fd]
    assert closed_fds == [inherited_cwd_fd]
    with pytest.raises(OSError):
        os.fstat(inherited_cwd_fd)
    assert len(popen_kwargs) == 1
    assert popen_kwargs[0]["close_fds"] is True


@pytest.mark.skipif(
    not Path("/proc/self/fd").is_dir(),
    reason="descriptor target probe requires procfs",
)
@pytest.mark.parametrize("capture_output", [False, True], ids=("direct", "capture"))
def test_spawn_bash_does_not_leak_inherited_cwd_fd_to_child(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capture_output: bool,
) -> None:
    execution_dir = tmp_path / "execution"
    execution_dir.mkdir()
    monkeypatch.chdir(execution_dir)
    result_path = execution_dir / "fd-result"
    script = "\n".join(
        [
            "import os",
            "target = os.stat('.')",
            "leaked = []",
            "for name in os.listdir('/proc/self/fd'):",
            "    try:",
            "        value = os.stat('/proc/self/fd/' + name)",
            "    except FileNotFoundError:",
            "        continue",
            "    if (value.st_dev, value.st_ino) == (target.st_dev, target.st_ino):",
            "        leaked.append(name)",
            (
                "print(','.join(leaked))"
                if capture_output
                else "open('fd-result', 'w').write(','.join(leaked))"
            ),
        ]
    )
    process = capture_artifacts._spawn_bash(
        "/bin/bash",
        shlex.join([sys.executable, "-c", script]),
        capture_output=capture_output,
    )
    stdout, _stderr = process.communicate(timeout=10)

    assert process.returncode == 0
    result = stdout.decode().strip() if capture_output else result_path.read_text()
    assert result == ""


def test_e2big_spawn_failure_is_explicit_bounded_and_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()

    def fail_spawn(*_args, **_kwargs):
        raise OSError(errno.E2BIG, "argument list too long")

    monkeypatch.setattr(capture_artifacts.subprocess, "Popen", fail_spawn)

    assert run_capture("printf must-not-run", str(project), _CAPTURE_ID) == 1
    captured = capfd.readouterr()
    assert "argument/environment exceeds system limit" in captured.err
    assert len(captured.err.encode("utf-8")) <= 512
    assert "must-not-run" not in captured.out


def test_verified_disabled_policy_runs_without_capture(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "project"
    temp_dir = project.joinpath(*CAPTURE_PATH_COMPONENTS[:2])
    temp_dir.mkdir(parents=True)
    (temp_dir / ".hook_config.json").write_text(
        json.dumps({"output_budget_policy": {"disabled": True}})
    )

    assert run_capture("printf policy-disabled", str(project), _CAPTURE_ID) == 0
    captured = capfd.readouterr()
    assert captured.out == "policy-disabled"
    assert not _capture_dir(project).exists()


def test_capture_preserves_native_bash_command_name(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "project"
    project.mkdir()

    assert run_capture('printf "%s" "$0"', str(project), _CAPTURE_ID) == 0

    captured = capfd.readouterr()
    assert captured.out == capture_artifacts._resolve_bash()


def test_spawn_failure_closes_created_artifact_fd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    observed_fds = _record_owned_capture_fds(monkeypatch)

    def fail_spawn(*args, **kwargs):
        raise OSError("fault injection")

    monkeypatch.setattr(subprocess, "Popen", fail_spawn)
    assert (
        capture_artifacts._main(_runner_args(command="printf ran > command_ran", cwd=str(project)))
        == 1
    )

    captured = capfd.readouterr()
    assert '"status":"capture_failed"' in captured.err
    assert "shell capture v2:" not in captured.out + captured.err
    assert not (project / "command_ran").exists()
    assert len(observed_fds) == 9
    for fd in observed_fds:
        with pytest.raises(OSError):
            os.fstat(fd)


def test_spawn_failure_reports_failed_state_recovery_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    logged_events: list[tuple[str, bool]] = []

    def fail_spawn(*_args, **_kwargs):
        raise OSError("primary spawn failure")

    def fail_recovery(*_args, **_kwargs):
        raise capture_artifacts.CaptureLifecycleError("secondary recovery failure")

    def record_error(message: str, *, exc_info: bool = False) -> None:
        logged_events.append((message, exc_info))

    monkeypatch.setattr(capture_artifacts, "_spawn_bash", fail_spawn)
    monkeypatch.setattr(
        capture_artifacts.CaptureLifecycleStore,
        "commit_capture_failure",
        fail_recovery,
    )
    monkeypatch.setattr(capture_artifacts.logger, "error", record_error)

    assert run_capture("printf never", str(project), _CAPTURE_ID) == 1
    captured = capfd.readouterr()
    failure = _single_failure_marker(captured.err)
    assert failure.reason is CaptureFailureReason.FILESYSTEM_IO
    assert "primary spawn failure" not in captured.err
    assert "secondary recovery failure" not in captured.err
    assert ("capture_failure_commit_failed", True) in logged_events


def test_post_duplication_failure_closes_all_fds_and_prevents_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    observed_fds = _record_owned_capture_fds(monkeypatch)
    duplicated_fds: list[int] = []
    real_dup = capture_artifacts.os.dup

    def record_dup(fd):
        duplicated_fd = real_dup(fd)
        duplicated_fds.append(duplicated_fd)
        return duplicated_fd

    def fail_duplicated_identity(_fd, _expected):
        raise OSError("fault injection after fd duplication")

    def unexpected_spawn(*_args, **_kwargs):
        raise AssertionError("command must not spawn after fd duplication failure")

    monkeypatch.setattr(capture_artifacts.os, "dup", record_dup)
    monkeypatch.setattr(capture_artifacts, "_same_identity", fail_duplicated_identity)
    monkeypatch.setattr(capture_artifacts, "_spawn_bash", unexpected_spawn)
    assert (
        capture_artifacts._main(_runner_args(command="printf ran > command_ran", cwd=str(project)))
        == 1
    )

    captured = capfd.readouterr()
    artifact_path = _capture_dir(project) / f"shell_{_CAPTURE_ID}.log"
    assert '"status":"capture_failed"' in captured.err
    assert "shell capture v2:" not in captured.out + captured.err
    assert not (project / "command_ran").exists()
    assert artifact_path.read_bytes() == b""
    assert len(observed_fds) == 9
    assert len(duplicated_fds) == 2
    for fd in [*observed_fds, *duplicated_fds]:
        with pytest.raises(OSError):
            os.fstat(fd)


@pytest.mark.parametrize("capture_output", [False, True], ids=("direct", "capture"))
def test_restore_failure_closes_pipe_and_inherited_cwd_fd(
    monkeypatch: pytest.MonkeyPatch,
    capture_output: bool,
) -> None:
    process = _FakeCaptureProcess(b"")
    runner_cwd_fd = os.open(".", os.O_RDONLY | os.O_DIRECTORY)
    inherited_cwd_fds: list[int] = []
    closed_fds: list[int] = []
    popen_kwargs: list[dict[str, object]] = []
    real_open = capture_artifacts.os.open
    real_close = capture_artifacts.os.close
    real_fchdir = capture_artifacts.os.fchdir
    fchdir_calls = 0

    def record_open(path, flags, mode=0o777, *, dir_fd=None):
        fd = real_open(path, flags, mode, dir_fd=dir_fd)
        if path == "." and dir_fd is None:
            inherited_cwd_fds.append(fd)
        return fd

    def record_close(fd):
        if fd in inherited_cwd_fds:
            closed_fds.append(fd)
        real_close(fd)

    def fail_restore(fd):
        nonlocal fchdir_calls
        fchdir_calls += 1
        if fchdir_calls == 2:
            raise OSError("fault injection")
        real_fchdir(fd)

    def record_popen(*_args, **kwargs):
        popen_kwargs.append(kwargs)
        return process

    monkeypatch.setattr(capture_artifacts.os, "open", record_open)
    monkeypatch.setattr(capture_artifacts.os, "close", record_close)
    monkeypatch.setattr(capture_artifacts.os, "fchdir", fail_restore)
    monkeypatch.setattr(capture_artifacts.subprocess, "Popen", record_popen)

    try:
        with pytest.raises(CaptureSetupError, match="cannot restore runner cwd"):
            capture_artifacts._spawn_bash(
                "/bin/bash",
                "printf never",
                capture_output=capture_output,
            )
        assert process.stdout.closed
        assert process.terminated
        assert process.wait_calls == 1
        assert len(inherited_cwd_fds) == 1
        assert closed_fds == inherited_cwd_fds
        assert popen_kwargs[0]["close_fds"] is True
        with pytest.raises(OSError):
            os.fstat(inherited_cwd_fds[0])
    finally:
        real_fchdir(runner_cwd_fd)
        os.close(runner_cwd_fd)


def test_post_creation_identity_failure_closes_artifact_and_emits_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    artifact_fds: list[int] = []
    real_fstat = os.fstat

    def fail_artifact_identity(fd):
        value = real_fstat(fd)
        if stat.S_ISREG(value.st_mode):
            artifact_fds.append(fd)
            raise OSError("fault injection")
        return value

    monkeypatch.setattr(capture_artifacts.os, "fstat", fail_artifact_identity)
    assert (
        capture_artifacts._main(_runner_args(command="printf ran > command_ran", cwd=str(project)))
        == 1
    )
    captured = capfd.readouterr()
    assert '"status":"capture_failed"' in captured.err
    assert "shell capture v2:" not in captured.out + captured.err
    assert not (project / "command_ran").exists()
    assert artifact_fds
    for fd in artifact_fds:
        with pytest.raises(OSError):
            real_fstat(fd)


def test_capture_pipe_closes_on_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    processes: list[subprocess.Popen[bytes]] = []
    real_spawn = capture_artifacts._spawn_bash

    def record_spawn(*args, **kwargs):
        process = real_spawn(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(capture_artifacts, "_spawn_bash", record_spawn)

    assert run_capture("printf output", str(project), _CAPTURE_ID) == 0
    assert processes
    assert processes[0].stdout is not None
    assert processes[0].stdout.closed


def test_inline_delivery_commits_final_without_reference_or_marker(
    tmp_path: Path,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()

    assert run_capture("printf inline", str(project), _CAPTURE_ID) == 0

    captured = capfd.readouterr()
    record = _capture_record(project)
    assert captured.out == "inline"
    assert "shell capture v2:" not in captured.out
    assert record.state is CaptureState.FINALIZED
    assert record.reference_status is CaptureReferenceStatus.NOT_REQUESTED
    assert record.delivery_status is CaptureDeliveryStatus.DELIVERED
    assert record.manifest is not None
    assert record.manifest.reference_hash is None


def test_oversized_delivery_publishes_parseable_resolvable_v2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(
        capture_artifacts,
        "read_capture_policy",
        lambda _anchor: CapturePolicy(inline_bytes=8),
    )
    expected = b"0123456789abcdef"

    assert run_capture("printf 0123456789abcdef", str(project), _CAPTURE_ID) == 0

    captured = capfd.readouterr()
    parsed = _single_v2_marker(captured.out)
    record = _capture_record(project)
    assert parsed.reference_status == "published"
    assert parsed.reference is not None
    assert parsed.total_bytes == len(expected)
    assert parsed.command_outcome_kind == "exited"
    assert parsed.command_outcome_value == parsed.shell_returncode == 0
    assert record.state is CaptureState.FINALIZED
    assert record.reference_status is CaptureReferenceStatus.PUBLISHED
    assert record.delivery_status is CaptureDeliveryStatus.DELIVERED
    with open_capture_lifecycle(str(project), create=False) as lifecycle:
        with lifecycle.open_verified_capture(parsed.reference) as reader:
            assert reader.read(0, len(expected)) == expected


@pytest.mark.parametrize(
    "command,kind,value",
    (
        ("printf 0123456789abcdef; exit 143", "exited", 143),
        ("printf 0123456789abcdef; kill -TERM $$", "signaled", 15),
    ),
)
def test_oversized_v2_preserves_distinct_raw_wait_outcome(
    command: str,
    kind: str,
    value: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(
        capture_artifacts,
        "read_capture_policy",
        lambda _anchor: CapturePolicy(inline_bytes=8),
    )

    assert run_capture(command, str(project), _CAPTURE_ID) == 143

    parsed = _single_v2_marker(capfd.readouterr().out)
    record = _capture_record(project)
    assert record.manifest is not None
    assert parsed.command_outcome_kind == kind
    assert parsed.command_outcome_value == value
    assert parsed.shell_returncode == 143
    assert parsed.reference_status == "published"
    assert parsed.reference is not None
    assert record.manifest.command_outcome.kind.value == kind
    assert record.manifest.command_outcome.value == value
    assert record.manifest.command_outcome.shell_returncode == 143
    assert record.delivery_status is CaptureDeliveryStatus.DELIVERED
    with open_capture_lifecycle(str(project), create=False) as lifecycle:
        with lifecycle.open_verified_capture(parsed.reference) as reader:
            assert reader.read(0, 16) == b"0123456789abcdef"


def test_bash_resolution_ignores_ambient_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    attacker_bin = tmp_path / "attacker-bin"
    attacker_bin.mkdir()
    fake_bash = attacker_bin / "bash"
    fake_bash.write_text("#!/bin/sh\nprintf attacker-controlled\nexit 99\n")
    fake_bash.chmod(0o755)
    monkeypatch.setenv("PATH", str(attacker_bin))

    assert run_capture("printf trusted-bash", str(project), _CAPTURE_ID) == 0
    captured = capfd.readouterr()
    assert captured.out == "trusted-bash"
    assert "attacker-controlled" not in captured.out + captured.err


@pytest.mark.parametrize("candidate_kind", ["symlink", "world-writable"])
def test_bash_resolution_rejects_untrusted_explicit_candidate(
    candidate_kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "candidate-target"
    target.write_text("#!/bin/sh\nexit 0\n")
    target.chmod(0o755)
    candidate = target
    if candidate_kind == "symlink":
        candidate = tmp_path / "bash"
        candidate.symlink_to(target)
    else:
        target.chmod(0o777)

    monkeypatch.setattr(
        capture_artifacts,
        "_TRUSTED_BASH_CANDIDATES",
        (str(candidate),),
    )

    with pytest.raises(CaptureSetupError, match="trusted bash executable unavailable"):
        capture_artifacts._resolve_bash()


def test_capture_stream_failure_closes_pipe_and_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    observed_fds: list[int] = []
    real_create = capture_artifacts.create_capture_artifact

    class FailingStream:
        closed = False

        def read(self, _size):
            raise OSError("fault injection")

        def close(self):
            self.closed = True

    class FailingProcess:
        def __init__(self) -> None:
            self.stdout = FailingStream()
            self.terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            del timeout
            return 0

    process = FailingProcess()

    def record_artifact(root, capture_id, lifecycle):
        artifact = real_create(root, capture_id, lifecycle)
        observed_fds.append(artifact.fd)
        return artifact

    monkeypatch.setattr(capture_artifacts, "create_capture_artifact", record_artifact)
    monkeypatch.setattr(capture_artifacts, "_spawn_bash", lambda *_args, **_kwargs: process)

    assert run_capture("printf output", str(project), _CAPTURE_ID) == 1
    captured = capfd.readouterr()
    failure = _single_failure_marker(captured.err)
    record = _capture_record(project)
    assert failure.stage == "capture_readback"
    assert "shell capture v2:" not in captured.out + captured.err
    assert record.state is CaptureState.FAILED
    assert record.reference_status is CaptureReferenceStatus.NOT_REQUESTED
    assert record.delivery_status is CaptureDeliveryStatus.NOT_ATTEMPTED
    assert process.terminated
    assert process.stdout.closed
    assert observed_fds
    for fd in observed_fds:
        with pytest.raises(OSError):
            os.fstat(fd)


def test_capture_control_flow_exception_settles_and_reraises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    process = _FakeCaptureProcess(b"")
    settled: list[object] = []

    monkeypatch.setattr(
        capture_artifacts,
        "_spawn_bash",
        lambda *_args, **_kwargs: process,
    )
    monkeypatch.setattr(
        capture_artifacts,
        "_drain_capture",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    monkeypatch.setattr(
        capture_artifacts,
        "_settle_failed_capture",
        lambda supplied: settled.append(supplied),
    )

    with pytest.raises(KeyboardInterrupt):
        run_capture("printf output", str(project), _CAPTURE_ID)

    assert settled == [process]
    assert _capture_record(project).state is CaptureState.FAILED
    assert process.stdout.closed


def test_capture_readback_failure_after_partial_output_closes_runtime_fds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    observed_fds = _record_runtime_fds(monkeypatch)

    class PartialReadbackStream:
        def __init__(self) -> None:
            self.read_calls = 0
            self.closed = False

        def read(self, _size):
            self.read_calls += 1
            if self.read_calls == 1:
                return b"partial-output"
            raise OSError("fault injection during readback")

        def close(self):
            self.closed = True

    process = _FakeCaptureProcess(b"")
    process.stdout = PartialReadbackStream()
    monkeypatch.setattr(capture_artifacts, "_spawn_bash", lambda *_args, **_kwargs: process)

    assert run_capture("printf output", str(project), _CAPTURE_ID) == 1

    captured = capfd.readouterr()
    artifact_path = _capture_dir(project) / f"shell_{_CAPTURE_ID}.log"
    assert '"status":"capture_failed"' in captured.err
    failure = _single_failure_marker(captured.err)
    assert failure.reason is CaptureFailureReason.FILESYSTEM_IO
    assert "fault injection during readback" not in captured.err
    assert "shell capture v2:" not in captured.out + captured.err
    assert artifact_path.read_bytes() == b"partial-output"
    assert process.terminated
    assert process.stdout.closed
    assert process.wait_calls == 1
    assert len(observed_fds) == 6
    for fd in observed_fds:
        with pytest.raises(OSError):
            os.fstat(fd)


def test_digest_failure_emits_failure_and_closes_runtime_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    observed_fds = _record_artifact_fds(monkeypatch)
    process = _FakeCaptureProcess(b"captured-output")

    class BrokenDigest:
        def update(self, _chunk: bytes) -> None:
            raise RuntimeError("fault injection")

        def hexdigest(self) -> str:
            raise AssertionError("digest failure must stop before finalization")

    monkeypatch.setattr(capture_artifacts, "_spawn_bash", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(capture_artifacts, "hashlib", SimpleNamespace(sha256=BrokenDigest))

    assert run_capture("printf output", str(project), _CAPTURE_ID) == 1
    captured = capfd.readouterr()
    failure = _single_failure_marker(captured.err)
    record = _capture_record(project)
    assert failure.stage == "capture_readback"
    assert "shell capture v2:" not in captured.out + captured.err
    assert record.state is CaptureState.FAILED
    assert record.reference_status is CaptureReferenceStatus.NOT_REQUESTED
    assert record.delivery_status is CaptureDeliveryStatus.NOT_ATTEMPTED
    assert process.terminated
    assert process.stdout.closed
    assert process.wait_calls == 1
    assert observed_fds
    for fd in observed_fds:
        with pytest.raises(OSError):
            os.fstat(fd)


def test_completed_carrier_fsync_failure_prevents_finalization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    artifact_fds = _record_artifact_fds(monkeypatch)
    real_fsync = os.fsync

    def fail_carrier_fsync(fd: int) -> None:
        if fd in artifact_fds:
            raise OSError("carrier fsync failed")
        real_fsync(fd)

    monkeypatch.setattr(capture_artifacts.os, "fsync", fail_carrier_fsync)

    assert run_capture("printf output", str(project), _CAPTURE_ID) == 1
    monkeypatch.setattr(capture_artifacts.os, "fsync", real_fsync)

    captured = capfd.readouterr()
    record = _capture_record(project)
    assert _single_failure_marker(captured.err).stage == (
        "capture_artifact_integrity_verification"
    )
    assert "shell capture v2:" not in captured.out + captured.err
    assert record.state is CaptureState.FAILED
    assert record.manifest is None
    assert record.reference_status is CaptureReferenceStatus.NOT_REQUESTED


def test_artifact_content_tampering_prevents_capture_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    process = _FakeCaptureProcess(b"captured-output")
    real_drain = capture_artifacts._drain_capture

    def tamper_after_drain(process, artifact_writer_fd, inline_bytes):
        result = real_drain(process, artifact_writer_fd, inline_bytes)
        os.ftruncate(artifact_writer_fd, 0)
        os.lseek(artifact_writer_fd, 0, os.SEEK_SET)
        capture_artifacts._write_all(artifact_writer_fd, b"tampered")
        return result

    monkeypatch.setattr(capture_artifacts, "_spawn_bash", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(capture_artifacts, "_drain_capture", tamper_after_drain)

    assert run_capture("printf output", str(project), _CAPTURE_ID) == 1
    captured = capfd.readouterr()
    assert '"status":"capture_failed"' in captured.err
    assert "capture artifact integrity verification failed" in captured.err
    assert "shell capture v2:" not in captured.out + captured.err
    assert captured.out == ""


def test_stdout_delivery_failure_closes_resources_without_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    observed_fds = _record_artifact_fds(monkeypatch)
    process = _FakeCaptureProcess(b"captured-output")

    def fail_stdout_delivery(*_args, **_kwargs) -> None:
        raise RuntimeError("fault injection")

    monkeypatch.setattr(capture_artifacts, "_spawn_bash", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        capture_replay,
        "write_and_flush_hook_stdout",
        fail_stdout_delivery,
    )

    assert run_capture("printf output", str(project), _CAPTURE_ID) == 1
    captured = capfd.readouterr()
    failure = _single_failure_marker(captured.err)
    record = _capture_record(project)
    assert failure.stage == "capture_stdout_write_and_flush"
    assert "shell capture v2:" not in captured.out + captured.err
    assert record.state is CaptureState.FINALIZED
    assert record.reference_status is CaptureReferenceStatus.NOT_REQUESTED
    assert record.delivery_status is CaptureDeliveryStatus.FAILED
    assert process.stdout.closed
    assert process.wait_calls == 1
    assert _capture_dir(project).joinpath(f"shell_{_CAPTURE_ID}.log").read_bytes() == (
        b"captured-output"
    )
    assert observed_fds
    for fd in observed_fds:
        with pytest.raises(OSError):
            os.fstat(fd)


def test_degraded_finalization_delivers_verified_output_and_child_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    process = _FakeCaptureProcess(b"captured-output")

    def fail_finalization(*_args, **_kwargs):
        raise CaptureCapacityError(CaptureCapacityReason.PROJECTED_COMPACTED_BYTES)

    monkeypatch.setattr(capture_artifacts, "_spawn_bash", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        CaptureLifecycleStore,
        "commit_verified_snapshot",
        fail_finalization,
    )

    assert run_capture("printf output", str(project), _CAPTURE_ID) == 0
    captured = capfd.readouterr()
    degraded = _single_degraded_marker(captured.err)
    assert captured.out == "captured-output"
    assert degraded.reason is CaptureFailureReason.PROJECTED_COMPACTED_BYTES_EXHAUSTED
    assert degraded.stage == "capture_finalization"
    assert degraded.shell_returncode == 0
    assert degraded.settlement_returncode is None


def test_degraded_stdout_failure_preserves_delivery_error_as_cause(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    process = _FakeCaptureProcess(b"captured-output")
    finalization_error = CaptureCapacityError(CaptureCapacityReason.PROJECTED_COMPACTED_BYTES)
    delivery_error = OSError("degraded stdout delivery failed")
    logged_exceptions: list[BaseException] = []

    def fail_finalization(*_args, **_kwargs):
        raise finalization_error

    def fail_stdout_delivery(*_args, **_kwargs) -> None:
        raise delivery_error

    def record_error(message: str, *, exc_info: bool = False) -> None:
        if message == "capture_shell_execution_failed" and exc_info:
            logged = sys.exc_info()[1]
            assert logged is not None
            logged_exceptions.append(logged)

    monkeypatch.setattr(capture_artifacts, "_spawn_bash", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        CaptureLifecycleStore,
        "commit_verified_snapshot",
        fail_finalization,
    )
    monkeypatch.setattr(
        capture_replay,
        "write_and_flush_hook_stdout",
        fail_stdout_delivery,
    )
    monkeypatch.setattr(capture_artifacts.logger, "error", record_error)

    assert run_capture("printf output", str(project), _CAPTURE_ID) == 1
    captured = capfd.readouterr()
    assert logged_exceptions == [finalization_error]
    assert finalization_error.__cause__ is delivery_error
    assert '"status":"capture_degraded"' not in captured.err
    assert _single_failure_marker(captured.err).stage == "capture_finalization"


@pytest.mark.parametrize(
    "results,fail_flush,expected",
    (
        pytest.param(
            [2, OSError("partial write failed")],
            False,
            b"in",
            id="partial-then-error",
        ),
        pytest.param([6], True, b"inline", id="flush-error"),
        pytest.param([None], False, b"", id="none"),
        pytest.param([0], False, b"", id="zero"),
        pytest.param([False], False, b"", id="boolean"),
        pytest.param([-1], False, b"", id="negative"),
        pytest.param([7], False, b"inline", id="oversized-count"),
    ),
)
def test_partial_write_and_flush_failures_record_delivery_authoritatively(
    results: list[int | None | BaseException],
    fail_flush: bool,
    expected: bytes,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    process = _FakeCaptureProcess(b"inline")
    stream = _ShortWriteStream(results, fail_flush=fail_flush)
    monkeypatch.setattr(capture_artifacts, "_spawn_bash", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        capture_artifacts.sys,
        "stdout",
        SimpleNamespace(buffer=stream),
    )

    assert run_capture("printf inline", str(project), _CAPTURE_ID) == 1

    captured = capfd.readouterr()
    record = _capture_record(project)
    assert bytes(stream.written) == expected
    assert _single_failure_marker(captured.err).stage == "capture_stdout_write_and_flush"
    assert record.state is CaptureState.FINALIZED
    assert record.reference_status is CaptureReferenceStatus.NOT_REQUESTED
    expected_status = CaptureDeliveryStatus.UNKNOWN if expected else CaptureDeliveryStatus.FAILED
    assert record.delivery_status is expected_status


def test_progressive_short_writes_complete_delivery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    process = _FakeCaptureProcess(b"inline")
    stream = _ShortWriteStream([1, 2, 3])
    monkeypatch.setattr(capture_artifacts, "_spawn_bash", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        capture_artifacts.sys,
        "stdout",
        SimpleNamespace(buffer=stream),
    )

    assert run_capture("printf inline", str(project), _CAPTURE_ID) == 0

    captured = capfd.readouterr()
    record = _capture_record(project)
    assert bytes(stream.written) == b"inline"
    assert stream.flushed
    assert captured.err == ""
    assert record.state is CaptureState.FINALIZED
    assert record.reference_status is CaptureReferenceStatus.NOT_REQUESTED
    assert record.delivery_status is CaptureDeliveryStatus.DELIVERED


def test_successful_finalization_uses_lifecycle_time_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    process = _FakeCaptureProcess(b"inline")
    monkeypatch.setattr(capture_artifacts, "_spawn_bash", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        CaptureLifecycleStore,
        "capture_finalization_window",
        lambda _self: (9_000_000_000.0, 9_000_000_333.0),
    )

    assert run_capture("printf inline", str(project), _CAPTURE_ID) == 0

    capfd.readouterr()
    record = _capture_record(project)
    assert record.manifest is not None
    assert record.manifest.finalized_at == 9_000_000_000.0
    assert record.manifest.retention_deadline == 9_000_000_333.0


def test_begin_delivery_failure_emits_no_capture_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    process = _FakeCaptureProcess(b"inline")
    real_transition = CaptureLifecycleStore.transition_delivery

    def fail_begin(self, value, *, expected, target):
        if target is CaptureDeliveryStatus.ATTEMPTING:
            raise OSError("begin delivery failed")
        return real_transition(self, value, expected=expected, target=target)

    monkeypatch.setattr(capture_artifacts, "_spawn_bash", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(CaptureLifecycleStore, "transition_delivery", fail_begin)

    assert run_capture("printf inline", str(project), _CAPTURE_ID) == 1

    captured = capfd.readouterr()
    record = _capture_record(project)
    assert captured.out == ""
    assert _single_failure_marker(captured.err).stage == "capture_delivery_begin"
    assert record.state is CaptureState.FINALIZED
    assert record.reference_status is CaptureReferenceStatus.NOT_REQUESTED
    assert record.delivery_status is CaptureDeliveryStatus.NOT_ATTEMPTED


def test_oversized_begin_failure_invalidates_unemitted_reference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    process = _FakeCaptureProcess(b"oversized")
    real_transition = CaptureLifecycleStore.transition_delivery

    def fail_begin(self, value, *, expected, target):
        if target is CaptureDeliveryStatus.ATTEMPTING:
            raise OSError("begin delivery failed")
        return real_transition(self, value, expected=expected, target=target)

    monkeypatch.setattr(capture_artifacts, "_spawn_bash", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        capture_artifacts,
        "read_capture_policy",
        lambda _anchor: CapturePolicy(inline_bytes=1),
    )
    monkeypatch.setattr(CaptureLifecycleStore, "transition_delivery", fail_begin)

    assert run_capture("printf oversized", str(project), _CAPTURE_ID) == 1

    captured = capfd.readouterr()
    record = _capture_record(project)
    assert captured.out == ""
    assert _single_failure_marker(captured.err).stage == "capture_delivery_begin"
    assert record.state is CaptureState.FINALIZED
    assert record.reference_status is CaptureReferenceStatus.UNAVAILABLE
    assert record.delivery_status is CaptureDeliveryStatus.NOT_ATTEMPTED


def test_transfer_failure_invalidates_issued_reference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    process = _FakeCaptureProcess(b"oversized")

    def fail_transfer(_self, _lifecycle, _finalized):
        raise OSError("transfer failed")

    monkeypatch.setattr(capture_artifacts, "_spawn_bash", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        capture_artifacts,
        "read_capture_policy",
        lambda _anchor: CapturePolicy(inline_bytes=1),
    )
    monkeypatch.setattr(capture_artifacts.CaptureArtifact, "transfer_to_reader", fail_transfer)

    assert run_capture("printf oversized", str(project), _CAPTURE_ID) == 1

    captured = capfd.readouterr()
    record = _capture_record(project)
    assert captured.out == ""
    assert _single_failure_marker(captured.err).stage == "capture_reader_transfer"
    assert record.state is CaptureState.FINALIZED
    assert record.reference_status is CaptureReferenceStatus.UNAVAILABLE
    assert record.delivery_status is CaptureDeliveryStatus.NOT_ATTEMPTED


def test_render_failure_after_begin_records_failed_delivery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    process = _FakeCaptureProcess(b"inline")

    def fail_render(_finalized) -> bytes:
        raise RuntimeError("render failed")

    monkeypatch.setattr(capture_artifacts, "_spawn_bash", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(capture_replay, "render_inline_capture", fail_render)

    assert run_capture("printf inline", str(project), _CAPTURE_ID) == 1

    captured = capfd.readouterr()
    record = _capture_record(project)
    assert captured.out == ""
    assert _single_failure_marker(captured.err).stage == "capture_replay_rendering"
    assert record.state is CaptureState.FINALIZED
    assert record.reference_status is CaptureReferenceStatus.NOT_REQUESTED
    assert record.delivery_status is CaptureDeliveryStatus.FAILED


def test_finish_delivery_failure_preserves_attempting_after_flushed_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    process = _FakeCaptureProcess(b"inline")
    real_transition = CaptureLifecycleStore.transition_delivery

    def fail_finish(self, value, *, expected, target):
        if target is CaptureDeliveryStatus.DELIVERED:
            raise OSError("finish delivery failed")
        return real_transition(self, value, expected=expected, target=target)

    monkeypatch.setattr(capture_artifacts, "_spawn_bash", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(CaptureLifecycleStore, "transition_delivery", fail_finish)

    assert run_capture("printf inline", str(project), _CAPTURE_ID) == 1

    captured = capfd.readouterr()
    record = _capture_record(project)
    assert captured.out == "inline"
    assert _single_failure_marker(captured.err).stage == "capture_delivery_finish"
    assert record.state is CaptureState.FINALIZED
    assert record.reference_status is CaptureReferenceStatus.NOT_REQUESTED
    assert record.delivery_status is CaptureDeliveryStatus.UNKNOWN


def test_oversized_finish_failure_leaves_flushed_marker_and_resolvable_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    process = _FakeCaptureProcess(b"oversized")
    real_transition = CaptureLifecycleStore.transition_delivery

    def fail_finish(self, value, *, expected, target):
        if target is CaptureDeliveryStatus.DELIVERED:
            raise OSError("finish delivery failed")
        return real_transition(self, value, expected=expected, target=target)

    monkeypatch.setattr(capture_artifacts, "_spawn_bash", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        capture_artifacts,
        "read_capture_policy",
        lambda _anchor: CapturePolicy(inline_bytes=1),
    )
    monkeypatch.setattr(CaptureLifecycleStore, "transition_delivery", fail_finish)

    assert run_capture("printf oversized", str(project), _CAPTURE_ID) == 1

    captured = capfd.readouterr()
    parsed = _single_v2_marker(captured.out)
    record = _capture_record(project)
    assert parsed.reference_status == "published"
    assert parsed.reference is not None
    assert _single_failure_marker(captured.err).stage == "capture_delivery_finish"
    assert record.state is CaptureState.FINALIZED
    assert record.reference_status is CaptureReferenceStatus.PUBLISHED
    assert record.delivery_status is CaptureDeliveryStatus.UNKNOWN
    with open_capture_lifecycle(str(project), create=False) as lifecycle:
        with lifecycle.open_verified_capture(parsed.reference) as reader:
            assert reader.read(0, parsed.total_bytes) == b"oversized"


@pytest.mark.parametrize(
    "durable_target",
    (CaptureDeliveryStatus.ATTEMPTING, CaptureDeliveryStatus.DELIVERED),
)
def test_delivery_transition_accepts_durable_successor_after_exception(
    durable_target: CaptureDeliveryStatus,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    process = _FakeCaptureProcess(b"inline")
    real_transition = CaptureLifecycleStore.transition_delivery
    injected = False

    def append_then_fail(self, value, *, expected, target):
        nonlocal injected
        result = real_transition(self, value, expected=expected, target=target)
        if target is durable_target and not injected:
            injected = True
            raise CaptureTransitionCommittedError("post-append fault")
        return result

    monkeypatch.setattr(capture_artifacts, "_spawn_bash", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        CaptureLifecycleStore,
        "transition_delivery",
        append_then_fail,
    )

    assert run_capture("printf inline", str(project), _CAPTURE_ID) == 0

    captured = capfd.readouterr()
    assert captured.out == "inline"
    assert captured.err == ""
    assert _capture_record(project).delivery_status is CaptureDeliveryStatus.DELIVERED


@pytest.mark.parametrize(
    ("uncertain_target", "expected_stdout"),
    (
        (CaptureDeliveryStatus.ATTEMPTING, ""),
        (CaptureDeliveryStatus.DELIVERED, "inline"),
    ),
)
def test_delivery_transition_does_not_accept_unclassified_successor_after_exception(
    uncertain_target: CaptureDeliveryStatus,
    expected_stdout: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    process = _FakeCaptureProcess(b"inline")
    real_transition = CaptureLifecycleStore.transition_delivery
    injected = False

    def append_then_fail(self, value, *, expected, target):
        nonlocal injected
        result = real_transition(self, value, expected=expected, target=target)
        if target is uncertain_target and not injected:
            injected = True
            raise OSError("unclassified post-append fault")
        return result

    monkeypatch.setattr(capture_artifacts, "_spawn_bash", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        CaptureLifecycleStore,
        "transition_delivery",
        append_then_fail,
    )

    assert run_capture("printf inline", str(project), _CAPTURE_ID) == 1

    captured = capfd.readouterr()
    assert injected
    assert captured.out == expected_stdout
    assert _single_failure_marker(captured.err).stage in {
        "capture_delivery_begin",
        "capture_delivery_finish",
    }


@pytest.mark.parametrize(
    (
        "phase",
        "expected_reference",
        "expected_delivery",
        "revision_delta",
        "resolves",
    ),
    (
        (
            "issued",
            CaptureReferenceStatus.UNAVAILABLE,
            CaptureDeliveryStatus.NOT_ATTEMPTED,
            1,
            False,
        ),
        (
            "published",
            CaptureReferenceStatus.UNAVAILABLE,
            CaptureDeliveryStatus.NOT_ATTEMPTED,
            1,
            False,
        ),
        (
            "attempting",
            CaptureReferenceStatus.PUBLISHED,
            CaptureDeliveryStatus.UNKNOWN,
            1,
            True,
        ),
        (
            "delivered",
            CaptureReferenceStatus.PUBLISHED,
            CaptureDeliveryStatus.DELIVERED,
            0,
            True,
        ),
    ),
)
def test_restart_normalization_never_reissues_or_reemits_reference(
    phase: str,
    expected_reference: CaptureReferenceStatus,
    expected_delivery: CaptureDeliveryStatus,
    revision_delta: int,
    resolves: bool,
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    anchor, root = _open_authority(project)
    artifact = _create_artifact(anchor, root)
    try:
        finalized = _finalize_artifact(anchor, root, artifact)
        assert finalized.issuance is not None
        token = finalized.issuance.token
        lifecycle = CaptureLifecycleStore.from_open_authorities(
            anchor,
            root,
            wall_clock=lambda: 1_000_001.0,
        )
        publication = None
        if phase != "issued":
            publication = lifecycle.publish_reference(finalized)
        if phase in {"attempting", "delivered"}:
            assert publication is not None
            lifecycle.transition_delivery(
                publication,
                expected=CaptureDeliveryStatus.NOT_ATTEMPTED,
                target=CaptureDeliveryStatus.ATTEMPTING,
            )
        if phase == "delivered":
            assert publication is not None
            lifecycle.transition_delivery(
                publication,
                expected=CaptureDeliveryStatus.ATTEMPTING,
                target=CaptureDeliveryStatus.DELIVERED,
            )
        before = lifecycle.get_record(_CAPTURE_ID)
        assert before is not None and before.manifest is not None

        artifact.close_artifact_fd()
        artifact.release_lease()
        restarted = CaptureLifecycleStore.from_open_authorities(
            anchor,
            root,
            wall_clock=lambda: 1_000_001.0,
        )
        after = restarted.get_record(_CAPTURE_ID)
        recovered = restarted.recover_interrupted_delivery(_CAPTURE_ID)

        assert after == recovered
        assert after is not None and after.manifest is not None
        assert after.reference_status is expected_reference
        assert after.delivery_status is expected_delivery
        assert after.revision == before.revision + revision_delta
        assert after.manifest_bytes == before.manifest_bytes
        assert after.manifest.reference_hash == before.manifest.reference_hash
        assert not hasattr(recovered, "token")
        if resolves:
            with restarted.open_verified_capture(token) as reader:
                assert reader.read(0, 8) == b"captured"
        else:
            with pytest.raises(CaptureLifecycleError, match="reference"):
                restarted.open_verified_capture(token)
    finally:
        artifact.close_artifact_fd()
        artifact.release_lease()
        root.close()
        anchor.close()


def test_publication_failure_invalidates_issued_token_without_delivery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    process = _FakeCaptureProcess(b"oversized")

    def fail_publication(_self, _finalized):
        raise OSError("publication failed")

    monkeypatch.setattr(capture_artifacts, "_spawn_bash", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        capture_artifacts,
        "read_capture_policy",
        lambda _anchor: CapturePolicy(inline_bytes=1),
    )
    monkeypatch.setattr(CaptureLifecycleStore, "publish_reference", fail_publication)

    assert run_capture("printf oversized", str(project), _CAPTURE_ID) == 1

    captured = capfd.readouterr()
    record = _capture_record(project)
    assert captured.out == ""
    assert _single_failure_marker(captured.err).stage == "capture_reference_publication"
    assert record.state is CaptureState.FINALIZED
    assert record.reference_status is CaptureReferenceStatus.UNAVAILABLE
    assert record.delivery_status is CaptureDeliveryStatus.NOT_ATTEMPTED


def test_publication_accepts_durable_successor_after_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    process = _FakeCaptureProcess(b"oversized")
    real_publish = CaptureLifecycleStore.publish_reference

    def append_then_fail(self, finalized):
        real_publish(self, finalized)
        raise CaptureTransitionCommittedError("post-publication fault")

    monkeypatch.setattr(capture_artifacts, "_spawn_bash", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        capture_artifacts,
        "read_capture_policy",
        lambda _anchor: CapturePolicy(inline_bytes=1),
    )
    monkeypatch.setattr(CaptureLifecycleStore, "publish_reference", append_then_fail)

    assert run_capture("printf oversized", str(project), _CAPTURE_ID) == 0

    captured = capfd.readouterr()
    parsed = _single_v2_marker(captured.out)
    record = _capture_record(project)
    assert parsed.reference_status == "published"
    assert captured.err == ""
    assert record.reference_status is CaptureReferenceStatus.PUBLISHED
    assert record.delivery_status is CaptureDeliveryStatus.DELIVERED


def test_publication_does_not_accept_unclassified_successor_after_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    process = _FakeCaptureProcess(b"oversized")
    real_publish = CaptureLifecycleStore.publish_reference

    def append_then_fail(self, finalized):
        real_publish(self, finalized)
        raise OSError("unclassified post-publication fault")

    monkeypatch.setattr(capture_artifacts, "_spawn_bash", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        capture_artifacts,
        "read_capture_policy",
        lambda _anchor: CapturePolicy(inline_bytes=1),
    )
    monkeypatch.setattr(CaptureLifecycleStore, "publish_reference", append_then_fail)

    assert run_capture("printf oversized", str(project), _CAPTURE_ID) == 1

    captured = capfd.readouterr()
    record = _capture_record(project)
    assert captured.out == ""
    assert _single_failure_marker(captured.err).stage == "capture_reference_publication"
    assert record.reference_status is CaptureReferenceStatus.UNAVAILABLE


def test_failure_marker_emission_failure_returns_capture_failure_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    process = _FakeCaptureProcess(b"captured-output")

    def fail_marker(*_args, **_kwargs) -> None:
        raise RuntimeError("fault injection")

    monkeypatch.setattr(capture_artifacts, "_spawn_bash", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        capture_replay,
        "write_and_flush_hook_stdout",
        fail_marker,
    )
    monkeypatch.setattr(capture_replay, "_emit_failure", fail_marker)

    assert run_capture("printf output", str(project), _CAPTURE_ID) == 1
    assert process.stdout.closed


def test_artifact_write_failure_emits_failure_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    sentinel = tmp_path / "command-ran"

    def fail_write(fd, data):
        raise OSError("fault injection")

    monkeypatch.setattr(capture_artifacts, "_write_all", fail_write)

    command = f"printf ran > {shlex.quote(str(sentinel))}; printf output"
    assert run_capture(command, str(project), _CAPTURE_ID) == 1
    captured = capfd.readouterr()
    assert sentinel.read_text() == "ran"
    raw_failure = next(
        line
        for line in captured.err.splitlines()
        if line.startswith("[AutoSkillit shell capture failure v3:")
    )
    payload = json.loads(
        raw_failure.removeprefix("[AutoSkillit shell capture failure v3:").removesuffix("]")
    )
    assert payload["status"] == "capture_failed"
    assert _single_failure_marker(captured.err).stage == "artifact_write"
    assert "shell capture v2:" not in captured.out + captured.err


class _ShortWriteStream:
    def __init__(
        self,
        results: list[int | None | BaseException],
        *,
        fail_flush: bool = False,
    ) -> None:
        self.results = results
        self.fail_flush = fail_flush
        self.written = bytearray()
        self.flushed = False

    def write(self, value: memoryview) -> int | None:
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        if isinstance(result, int) and not isinstance(result, bool) and result > 0:
            self.written.extend(value[:result])
        return result

    def flush(self) -> None:
        if self.fail_flush:
            raise OSError("flush failed")
        self.flushed = True


def test_hook_output_write_all_accepts_progressive_short_writes() -> None:
    stream = _ShortWriteStream([1, 2, 3])

    capture_replay.write_all_stream(stream, b"abcdef", boundary="test")
    stream.flush()

    assert bytes(stream.written) == b"abcdef"
    assert stream.flushed


@pytest.mark.parametrize("result", (None, 0, False, -1, 4))
def test_hook_output_write_all_rejects_invalid_progress(result: int | None) -> None:
    stream = _ShortWriteStream([result])

    with pytest.raises(OSError, match="made no progress"):
        capture_replay.write_all_stream(
            stream,
            b"abc",
            boundary="test",
        )


def test_hook_output_write_all_preserves_partial_error_boundary() -> None:
    stream = _ShortWriteStream([2, OSError("write failed")])

    with pytest.raises(OSError, match="write failed"):
        capture_replay.write_all_stream(
            stream,
            b"abcdef",
            boundary="test",
        )

    assert bytes(stream.written) == b"ab"


def test_publication_binding_failure_emits_typed_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    observed_fds = _record_runtime_fds(monkeypatch)

    def fail_verification(_anchor, _root, _artifact, _issuance):
        raise OSError("fault injection")

    monkeypatch.setattr(
        capture_artifacts,
        "verify_reference_publication_binding",
        fail_verification,
    )
    monkeypatch.setattr(
        capture_artifacts,
        "read_capture_policy",
        lambda _anchor: CapturePolicy(inline_bytes=1),
    )

    assert run_capture("printf output", str(project), _CAPTURE_ID) == 1
    captured = capfd.readouterr()
    record = _capture_record(project)
    assert '"status":"capture_failed"' in captured.err
    assert "shell capture v2:" not in captured.out + captured.err
    assert record.state is CaptureState.FINALIZED
    assert record.reference_status is CaptureReferenceStatus.UNAVAILABLE
    assert record.delivery_status is CaptureDeliveryStatus.NOT_ATTEMPTED
    assert len(observed_fds) >= 6
    for fd in observed_fds:
        with pytest.raises(OSError):
            os.fstat(fd)
