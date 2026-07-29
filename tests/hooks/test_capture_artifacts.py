"""Tests for descriptor-anchored shell-capture authority."""

from __future__ import annotations

import base64
import importlib
import json
import os
import stat
import subprocess
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace

import pytest

import autoskillit.hooks._capture_artifacts as capture_artifacts
from autoskillit.hooks._capture._snapshot import CaptureMeasurement
from autoskillit.hooks._capture_artifacts import (
    CAPTURE_PATH_COMPONENTS,
    CapturePolicy,
    CaptureSetupError,
    create_capture_artifact,
    current_artifact_path_if_bound,
    open_capture_root,
    open_project_anchor,
    read_capture_policy,
    run_capture,
)
from autoskillit.hooks._capture_lifecycle import CaptureLifecycleStore

capture_authority = importlib.import_module(capture_artifacts.open_project_anchor.__module__)

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.medium]

_CAPTURE_ID = "0123456789abcdef"


def _capture_dir(project: Path) -> Path:
    return project.joinpath(*CAPTURE_PATH_COMPONENTS)


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

    assert capture_artifacts._settle_failed_capture(process) == -9
    assert process.terminated
    assert process.killed
    assert process.wait_timeouts == [
        capture_artifacts._PROCESS_SETTLE_TIMEOUT_SECONDS,
        capture_artifacts._PROCESS_SETTLE_TIMEOUT_SECONDS,
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
    try:
        assert anchor.physical_path == project.resolve()
        assert _capture_dir(project).is_dir()
        assert current_artifact_path_if_bound(anchor, root, artifact) == str(
            _capture_dir(project) / artifact.name
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


def test_symlinked_policy_leaf_is_not_trusted(tmp_path: Path) -> None:
    project = tmp_path / "project"
    temp_dir = project.joinpath(*CAPTURE_PATH_COMPONENTS[:2])
    temp_dir.mkdir(parents=True)
    external_config = tmp_path / "external-config.json"
    external_config.write_text(json.dumps({"output_budget_policy": {"disabled": True}}))
    (temp_dir / ".hook_config.json").symlink_to(external_config)

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


def test_marker_path_requires_current_directory_binding(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    anchor, root = _open_authority(project)
    artifact = _create_artifact(anchor, root)
    capture_dir = _capture_dir(project)
    displaced = capture_dir.with_name("shell_capture-displaced")
    try:
        capture_dir.rename(displaced)
        capture_dir.mkdir()
        assert current_artifact_path_if_bound(anchor, root, artifact) is None
    finally:
        artifact.close_artifact_fd()
        artifact.release_lease()
        root.close()
        anchor.close()


def test_marker_path_rederives_symlinked_project_path(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    replacement = tmp_path / "replacement"
    replacement.mkdir()
    supplied_cwd = tmp_path / "project-link"
    supplied_cwd.symlink_to(project, target_is_directory=True)
    anchor, root = _open_authority(supplied_cwd)
    artifact = _create_artifact(anchor, root)
    try:
        supplied_cwd.unlink()
        supplied_cwd.symlink_to(replacement, target_is_directory=True)

        assert current_artifact_path_if_bound(anchor, root, artifact) is None
    finally:
        artifact.close_artifact_fd()
        artifact.release_lease()
        root.close()
        anchor.close()


def test_capture_marker_encodes_path_control_characters(
    capfd: pytest.CaptureFixture[str],
) -> None:
    result = capture_artifacts._DrainResult(
        measurement=CaptureMeasurement(
            total_bytes=2,
            sha256="a" * 64,
            inline=b"",
            head=b"h",
            tail=b"t",
        ),
        write_error=None,
    )

    capture_artifacts._emit_capture(result, "/project\n] forged", inline_bytes=1)

    captured = capfd.readouterr()
    assert "/project\\n\\u005d forged" in captured.out
    assert "\n] forged" not in captured.out


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
    encoded = base64.b64encode(b"printf ran > command_ran").decode()

    assert capture_artifacts._main(["run", encoded, str(project), _CAPTURE_ID]) == 1
    captured = capfd.readouterr()
    assert "CAPTURE_FAILED" in captured.err
    assert "SHELL_OUTPUT_CAPTURED" not in captured.out + captured.err
    assert not (project / "command_ran").exists()


@pytest.mark.parametrize("capture_id", ["", "0123456789abcde", "0123456789abcdeg"])
def test_reject_mode_validates_capture_id(
    capture_id: str,
    capfd: pytest.CaptureFixture[str],
) -> None:
    assert capture_artifacts._main(["reject", "", "/abs/project", capture_id]) == 1
    captured = capfd.readouterr()
    assert "invalid capture id" in captured.err
    assert "capture request rejected before command execution" not in captured.err


def test_valid_reject_runs_one_runner_tail_sweep(
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    events: list[str] = []

    class Store:
        def sweep(self) -> SimpleNamespace:
            events.append("sweep")
            return SimpleNamespace(errors=0)

    class OpenLifecycle:
        def __enter__(self):
            events.append("open")
            return Store()

        def __exit__(self, *_args):
            events.append("close")

    monkeypatch.setattr(
        capture_artifacts,
        "open_capture_lifecycle",
        lambda requested_cwd, *, create: OpenLifecycle(),
    )

    assert capture_artifacts._main(["reject", "", "/abs/project", _CAPTURE_ID]) == 1
    assert events == ["open", "sweep", "close"]
    assert "capture request rejected before command execution" in capfd.readouterr().err


def test_runner_tail_preserves_dispatch_result_and_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class Store:
        def sweep(self) -> SimpleNamespace:
            events.append("sweep")
            return SimpleNamespace(errors=0)

    class OpenLifecycle:
        def __enter__(self):
            events.append("open")
            return Store()

        def __exit__(self, *_args):
            events.append("close")

    def dispatch(*_args) -> int:
        events.append("dispatch")
        return 37

    monkeypatch.setattr(capture_artifacts, "_dispatch_runner", dispatch)
    monkeypatch.setattr(
        capture_artifacts,
        "open_capture_lifecycle",
        lambda requested_cwd, *, create: OpenLifecycle(),
    )

    assert capture_artifacts._main(["run", "encoded", "/abs/project", _CAPTURE_ID]) == 37
    assert events == ["dispatch", "open", "sweep", "close"]


def test_runner_tail_cleanup_failure_does_not_replace_user_result(
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(capture_artifacts, "_dispatch_runner", lambda *_args: 23)

    def fail_open(_requested_cwd, *, create):
        raise capture_artifacts.CaptureLifecycleError("🔥" * 512)

    monkeypatch.setattr(capture_artifacts, "open_capture_lifecycle", fail_open)

    assert capture_artifacts._main(["run", "encoded", "/abs/project", _CAPTURE_ID]) == 23
    captured = capfd.readouterr()
    assert captured.out == ""
    assert "shell capture cleanup failed" in captured.err
    assert len(captured.err.encode("utf-8")) <= 512


def test_runner_tail_reports_sweep_outcome_errors(
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    class Store:
        def sweep(self) -> SimpleNamespace:
            return SimpleNamespace(errors=2)

    class OpenLifecycle:
        def __enter__(self):
            return Store()

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(capture_artifacts, "_dispatch_runner", lambda *_args: 23)
    monkeypatch.setattr(
        capture_artifacts,
        "open_capture_lifecycle",
        lambda requested_cwd, *, create: OpenLifecycle(),
    )

    assert capture_artifacts._main(["run", "encoded", "/abs/project", _CAPTURE_ID]) == 23
    captured = capfd.readouterr()
    assert captured.out == ""
    assert "cleanup deferred after 2 errors" in captured.err


def test_runner_tail_still_sweeps_after_unexpected_dispatch_exception(
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    swept: list[bool] = []

    class Store:
        def sweep(self) -> SimpleNamespace:
            swept.append(True)
            return SimpleNamespace(errors=0)

    class OpenLifecycle:
        def __enter__(self):
            return Store()

        def __exit__(self, *_args):
            return None

    def fail_dispatch(*_args):
        raise RuntimeError("fault injection")

    monkeypatch.setattr(capture_artifacts, "_dispatch_runner", fail_dispatch)
    monkeypatch.setattr(
        capture_artifacts,
        "open_capture_lifecycle",
        lambda requested_cwd, *, create: OpenLifecycle(),
    )

    assert capture_artifacts._main(["run", "encoded", "/abs/project", _CAPTURE_ID]) == 1
    assert swept == [True]
    assert "capture runner failed" in capfd.readouterr().err


def test_malformed_runner_invocation_does_not_trigger_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_open(*_args, **_kwargs):
        raise AssertionError("malformed invocation must not trigger cleanup")

    monkeypatch.setattr(capture_artifacts, "open_capture_lifecycle", unexpected_open)

    assert capture_artifacts._main(["reject", "nonempty", "/abs/project", _CAPTURE_ID]) == 1
    assert capture_artifacts._main(["run", "encoded", "relative", _CAPTURE_ID]) == 1


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
    encoded = base64.b64encode(b"printf ran > command_ran").decode()

    assert capture_artifacts._main(["run", encoded, str(project), _CAPTURE_ID]) == 1

    captured = capfd.readouterr()
    assert "CAPTURE_FAILED" in captured.err
    assert "SHELL_OUTPUT_CAPTURED" not in captured.out + captured.err
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

    def fail_spawn(*_args, **_kwargs):
        raise OSError("primary spawn failure")

    def fail_recovery(*_args, **_kwargs):
        raise capture_artifacts.CaptureLifecycleError("secondary recovery failure")

    monkeypatch.setattr(capture_artifacts, "_spawn_bash", fail_spawn)
    monkeypatch.setattr(
        capture_artifacts.CaptureLifecycleStore,
        "commit_capture_failure",
        fail_recovery,
    )

    assert run_capture("printf never", str(project), _CAPTURE_ID) == 1
    captured = capfd.readouterr()
    assert "primary spawn failure" in captured.err
    assert "secondary recovery failure" in captured.err


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
    encoded = base64.b64encode(b"printf ran > command_ran").decode()

    assert capture_artifacts._main(["run", encoded, str(project), _CAPTURE_ID]) == 1

    captured = capfd.readouterr()
    artifact_path = _capture_dir(project) / f"shell_{_CAPTURE_ID}.log"
    assert "CAPTURE_FAILED" in captured.err
    assert "SHELL_OUTPUT_CAPTURED" not in captured.out + captured.err
    assert not (project / "command_ran").exists()
    assert artifact_path.read_bytes() == b""
    assert len(observed_fds) == 9
    assert len(duplicated_fds) == 2
    for fd in [*observed_fds, *duplicated_fds]:
        with pytest.raises(OSError):
            os.fstat(fd)


def test_restore_failure_closes_pipe_and_original_cwd_fd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    anchor = open_project_anchor(str(project))
    process = _FakeCaptureProcess(b"")
    runner_cwd_fd = os.open(".", os.O_RDONLY | os.O_DIRECTORY)
    original_cwd_fds: list[int] = []
    real_open = capture_artifacts.os.open
    real_fchdir = capture_artifacts.os.fchdir
    fchdir_calls = 0

    def record_open(path, flags, mode=0o777, *, dir_fd=None):
        fd = real_open(path, flags, mode, dir_fd=dir_fd)
        if path == "." and dir_fd is None:
            original_cwd_fds.append(fd)
        return fd

    def fail_restore(fd):
        nonlocal fchdir_calls
        fchdir_calls += 1
        if fchdir_calls == 2:
            raise OSError("fault injection")
        real_fchdir(fd)

    monkeypatch.setattr(capture_artifacts.os, "open", record_open)
    monkeypatch.setattr(capture_artifacts.os, "fchdir", fail_restore)
    monkeypatch.setattr(capture_artifacts.subprocess, "Popen", lambda *_args, **_kwargs: process)

    try:
        with pytest.raises(CaptureSetupError, match="cannot restore runner cwd"):
            capture_artifacts._spawn_bash(
                anchor,
                "/bin/bash",
                "printf never",
                capture_output=True,
            )
        assert process.stdout.closed
        assert process.terminated
        assert process.wait_calls == 1
        assert len(original_cwd_fds) == 1
        with pytest.raises(OSError):
            os.fstat(original_cwd_fds[0])
    finally:
        real_fchdir(runner_cwd_fd)
        os.close(runner_cwd_fd)
        anchor.close()


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
    encoded = base64.b64encode(b"printf ran > command_ran").decode()

    assert capture_artifacts._main(["run", encoded, str(project), _CAPTURE_ID]) == 1
    captured = capfd.readouterr()
    assert "CAPTURE_FAILED" in captured.err
    assert "SHELL_OUTPUT_CAPTURED" not in captured.out + captured.err
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
    assert "CAPTURE_FAILED" in captured.err
    assert "SHELL_OUTPUT_CAPTURED" not in captured.out + captured.err
    assert process.terminated
    assert process.stdout.closed
    assert observed_fds
    for fd in observed_fds:
        with pytest.raises(OSError):
            os.fstat(fd)


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
    assert "CAPTURE_FAILED" in captured.err
    assert "OSError: fault injection during readback" in captured.err
    assert "SHELL_OUTPUT_CAPTURED" not in captured.out + captured.err
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
    assert "CAPTURE_FAILED" in captured.err
    assert "SHELL_OUTPUT_CAPTURED" not in captured.out + captured.err
    assert process.terminated
    assert process.stdout.closed
    assert process.wait_calls == 1
    assert observed_fds
    for fd in observed_fds:
        with pytest.raises(OSError):
            os.fstat(fd)


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
    assert "CAPTURE_FAILED" in captured.err
    assert "capture artifact integrity verification failed" in captured.err
    assert "SHELL_OUTPUT_CAPTURED" not in captured.out + captured.err
    assert captured.out == ""


def test_success_marker_emission_failure_closes_resources_without_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    observed_fds = _record_artifact_fds(monkeypatch)
    process = _FakeCaptureProcess(b"captured-output")

    def fail_success_marker(*_args, **_kwargs) -> None:
        raise RuntimeError("fault injection")

    monkeypatch.setattr(capture_artifacts, "_spawn_bash", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(capture_artifacts, "_emit_capture", fail_success_marker)

    assert run_capture("printf output", str(project), _CAPTURE_ID) == 1
    captured = capfd.readouterr()
    assert "CAPTURE_FAILED" in captured.err
    assert "SHELL_OUTPUT_CAPTURED" not in captured.out + captured.err
    assert process.stdout.closed
    assert process.wait_calls == 1
    assert _capture_dir(project).joinpath(f"shell_{_CAPTURE_ID}.log").read_bytes() == (
        b"captured-output"
    )
    assert observed_fds
    for fd in observed_fds:
        with pytest.raises(OSError):
            os.fstat(fd)


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
    monkeypatch.setattr(capture_artifacts, "_emit_capture", fail_marker)
    monkeypatch.setattr(capture_artifacts, "_emit_failure", fail_marker)

    assert run_capture("printf output", str(project), _CAPTURE_ID) == 1
    assert process.stdout.closed


def test_artifact_write_failure_emits_failure_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()

    def fail_write(fd, data):
        raise OSError("fault injection")

    monkeypatch.setattr(capture_artifacts, "_write_all", fail_write)

    assert run_capture("printf ran > command_ran; printf output", str(project), _CAPTURE_ID) == 1
    captured = capfd.readouterr()
    assert (project / "command_ran").read_text() == "ran"
    assert "CAPTURE_FAILED" in captured.err
    assert "SHELL_OUTPUT_CAPTURED" not in captured.out + captured.err


def test_marker_verification_failure_emits_failure_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    observed_fds = _record_runtime_fds(monkeypatch)

    def fail_verification(anchor, root, artifact):
        raise OSError("fault injection")

    monkeypatch.setattr(
        capture_artifacts,
        "current_artifact_path_if_bound",
        fail_verification,
    )

    assert run_capture("printf output", str(project), _CAPTURE_ID) == 1
    captured = capfd.readouterr()
    assert "CAPTURE_FAILED" in captured.err
    assert "SHELL_OUTPUT_CAPTURED" not in captured.out + captured.err
    assert len(observed_fds) == 6
    for fd in observed_fds:
        with pytest.raises(OSError):
            os.fstat(fd)
