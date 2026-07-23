"""Tests for descriptor-anchored shell-capture authority."""

from __future__ import annotations

import base64
import json
import os
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

import autoskillit.hooks._capture_artifacts as capture_artifacts
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
    sweep_stale_captures,
)

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

    def record_artifact(root, capture_id):
        artifact = real_create(root, capture_id)
        observed_fds.append(artifact.fd)
        return artifact

    monkeypatch.setattr(capture_artifacts, "create_capture_artifact", record_artifact)
    return observed_fds


def test_settle_failed_capture_escalates_after_terminate_timeout() -> None:
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

    assert capture_artifacts._settle_failed_capture(process) == 137
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

    def record_artifact(root, capture_id):
        artifact = real_create(root, capture_id)
        observed_fds.append(artifact.fd)
        return artifact

    monkeypatch.setattr(capture_artifacts, "open_project_anchor", record_anchor)
    monkeypatch.setattr(capture_artifacts, "open_capture_root", record_root)
    monkeypatch.setattr(capture_artifacts, "create_capture_artifact", record_artifact)
    return observed_fds


def test_project_anchor_accepts_symlink_cwd(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    supplied_cwd = tmp_path / "project-link"
    supplied_cwd.symlink_to(project, target_is_directory=True)

    anchor, root = _open_authority(supplied_cwd)
    artifact = create_capture_artifact(root, _CAPTURE_ID)
    try:
        assert anchor.physical_path == project.resolve()
        assert _capture_dir(project).is_dir()
        assert current_artifact_path_if_bound(anchor, root, artifact) == str(
            _capture_dir(project) / artifact.name
        )
    finally:
        artifact.close()
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

    monkeypatch.setattr(capture_artifacts, "_require_capabilities", lambda: None)
    monkeypatch.setattr(capture_artifacts.os, "open", track_open)
    monkeypatch.setattr(capture_artifacts.os.path, "realpath", track_realpath)

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
    supported = set(capture_artifacts.os.supports_dir_fd)
    supported.discard(capture_artifacts.os.stat)
    monkeypatch.setattr(capture_artifacts.os, "supports_dir_fd", supported)

    with pytest.raises(CaptureSetupError, match="filesystem primitives unavailable"):
        capture_artifacts._require_capabilities()


def test_capability_probe_requires_exclusive_creation_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(capture_artifacts.os, "O_EXCL", 0)

    with pytest.raises(CaptureSetupError, match="filesystem primitives unavailable"):
        capture_artifacts._require_capabilities()


def test_cleanup_capability_probe_selects_safe_retention_without_dir_fd_unlink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supported = set(capture_artifacts.os.supports_dir_fd)
    supported.discard(capture_artifacts.os.unlink)
    monkeypatch.setattr(capture_artifacts.os, "supports_dir_fd", supported)

    mode = capture_artifacts._probe_cleanup_deletion_mode()

    assert mode is capture_artifacts._CleanupDeletionMode.SAFE_RETENTION_WITHOUT_DIR_FD_UNLINK


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


def test_verified_policy_merges_overlay_and_bounds_inline_bytes(tmp_path: Path) -> None:
    project = tmp_path / "project"
    temp_dir = project.joinpath(*CAPTURE_PATH_COMPONENTS[:2])
    temp_dir.mkdir(parents=True)
    (temp_dir / ".hook_config.json").write_text(
        json.dumps(
            {
                "output_budget_policy": {
                    "disabled": False,
                    "shell_max_inline_bytes": 31,
                }
            }
        )
    )
    (temp_dir / ".hook_config_overlay.json").write_text(
        json.dumps({"output_budget_policy": {"disabled": True}})
    )

    anchor = open_project_anchor(str(project))
    try:
        assert read_capture_policy(anchor) == CapturePolicy(disabled=True, inline_bytes=31)
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
            create_capture_artifact(root, _CAPTURE_ID)

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
    artifact = create_capture_artifact(root, _CAPTURE_ID)
    capture_dir = _capture_dir(project)
    displaced = capture_dir.with_name("shell_capture-displaced")
    try:
        capture_dir.rename(displaced)
        capture_dir.mkdir()
        assert current_artifact_path_if_bound(anchor, root, artifact) is None
    finally:
        artifact.close()
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
    artifact = create_capture_artifact(root, _CAPTURE_ID)
    try:
        supplied_cwd.unlink()
        supplied_cwd.symlink_to(replacement, target_is_directory=True)

        assert current_artifact_path_if_bound(anchor, root, artifact) is None
    finally:
        artifact.close()
        root.close()
        anchor.close()


def test_capture_marker_encodes_path_control_characters(
    capfd: pytest.CaptureFixture[str],
) -> None:
    result = capture_artifacts._DrainResult(
        total_bytes=2,
        sha256="a" * 64,
        inline=b"",
        head=b"h",
        tail=b"t",
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


def test_stale_capture_is_safely_retained_without_identity_unlink(tmp_path: Path) -> None:
    project = tmp_path / "project"
    capture_dir = _capture_dir(project)
    capture_dir.mkdir(parents=True)
    stale = capture_dir / f"shell_{_CAPTURE_ID}.log"
    stale.write_bytes(b"retained")
    old = time.time() - 7200
    os.utime(stale, (old, old))

    assert sweep_stale_captures(project, max_age_seconds=3600) == 0
    assert stale.read_bytes() == b"retained"


def test_name_matching_fifo_does_not_block_stale_cleanup(tmp_path: Path) -> None:
    project = tmp_path / "project"
    capture_dir = _capture_dir(project)
    capture_dir.mkdir(parents=True)
    fifo = capture_dir / f"shell_{_CAPTURE_ID}.log"
    try:
        os.mkfifo(fifo)
    except OSError as exc:
        pytest.skip(f"FIFO unavailable: {exc}")
    code = (
        "import sys\n"
        "from autoskillit.hooks._capture_artifacts import sweep_stale_captures\n"
        "raise SystemExit(sweep_stale_captures(sys.argv[1], max_age_seconds=0))\n"
    )

    completed = subprocess.run(
        [sys.executable, "-c", code, str(project)],
        capture_output=True,
        text=True,
        timeout=2,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert fifo.is_fifo()


def test_cleanup_candidate_classification_rejects_unsafe_entries(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    anchor, root = _open_authority(project)
    capture_dir = _capture_dir(project)
    valid = capture_dir / "shell_1111111111111111.log"
    valid.write_text("valid")
    world_writable = capture_dir / "shell_2222222222222222.log"
    world_writable.write_text("unsafe")
    os.chmod(world_writable, 0o666)
    hardlink_source = tmp_path / "hardlink-source"
    hardlink_source.write_text("unsafe")
    hardlink = capture_dir / "shell_3333333333333333.log"
    try:
        os.link(hardlink_source, hardlink)
    except OSError:
        root.close()
        anchor.close()
        pytest.skip("hardlinks unavailable")

    try:
        threshold = time.time() - 100
        assert (
            capture_artifacts._open_stale_candidate(
                root,
                valid.name,
                mtime_threshold=threshold,
            )
            is None
        )
        os.utime(valid, (threshold - 100, threshold - 100))
        candidate = capture_artifacts._open_stale_candidate(
            root,
            valid.name,
            mtime_threshold=threshold,
        )
        assert candidate is not None
        assert candidate.inode == valid.stat().st_ino
        candidate.close()
        assert (
            capture_artifacts._open_stale_candidate(
                root,
                world_writable.name,
                mtime_threshold=time.time() + 1,
            )
            is None
        )
        assert (
            capture_artifacts._open_stale_candidate(
                root,
                hardlink.name,
                mtime_threshold=time.time() + 1,
            )
            is None
        )
    finally:
        root.close()
        anchor.close()


def test_cleanup_rejects_symlinked_capture_root(tmp_path: Path) -> None:
    project = tmp_path / "project"
    temp_dir = project.joinpath(*CAPTURE_PATH_COMPONENTS[:2])
    temp_dir.mkdir(parents=True)
    external = tmp_path / "external"
    external.mkdir()
    stale = external / f"shell_{_CAPTURE_ID}.log"
    stale.write_bytes(b"must-survive")
    old = time.time() - 7200
    os.utime(stale, (old, old))
    (temp_dir / CAPTURE_PATH_COMPONENTS[2]).symlink_to(external, target_is_directory=True)

    assert sweep_stale_captures(project, max_age_seconds=0) == 0
    assert stale.read_bytes() == b"must-survive"


def test_cleanup_retains_replacement_raced_after_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    capture_dir = _capture_dir(project)
    capture_dir.mkdir(parents=True)
    stale = capture_dir / f"shell_{_CAPTURE_ID}.log"
    stale.write_bytes(b"validated")
    os.utime(stale, (0, 0))
    displaced = capture_dir / "validated-inode"
    real_open = capture_artifacts._open_stale_candidate
    swapped = False

    def swap_after_validation(root, name, *, mtime_threshold):
        nonlocal swapped
        candidate = real_open(root, name, mtime_threshold=mtime_threshold)
        if candidate is not None and not swapped:
            stale.rename(displaced)
            stale.write_bytes(b"replacement")
            swapped = True
        return candidate

    monkeypatch.setattr(
        capture_artifacts,
        "_open_stale_candidate",
        swap_after_validation,
    )

    assert sweep_stale_captures(project, max_age_seconds=0) == 0
    assert stale.read_bytes() == b"replacement"
    assert displaced.read_bytes() == b"validated"


def test_cleanup_failure_for_one_entry_does_not_skip_later_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    capture_dir = _capture_dir(project)
    capture_dir.mkdir(parents=True)
    stale_paths = [
        capture_dir / "shell_1111111111111111.log",
        capture_dir / "shell_2222222222222222.log",
    ]
    for path in stale_paths:
        path.write_text(path.name)
        os.utime(path, (0, 0))
    calls: list[str] = []
    real_open = capture_artifacts._open_stale_candidate

    def fail_first_entry(root, name, *, mtime_threshold):
        calls.append(name)
        if len(calls) == 1:
            raise OSError("fault injection")
        return real_open(root, name, mtime_threshold=mtime_threshold)

    monkeypatch.setattr(
        capture_artifacts,
        "_open_stale_candidate",
        fail_first_entry,
    )

    assert sweep_stale_captures(project, max_age_seconds=0) == 0
    assert len(calls) == 2
    assert {path.name for path in stale_paths} == set(calls)
    assert all(path.exists() for path in stale_paths)


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
    assert capture_artifacts._main(["reject", capture_id]) == 1
    captured = capfd.readouterr()
    assert "invalid capture id" in captured.err
    assert "capture request rejected before command execution" not in captured.err


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
    assert len(observed_fds) == 5
    for fd in observed_fds:
        with pytest.raises(OSError):
            os.fstat(fd)


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
    assert "OSError: fault injection during readback" in captured.err
    assert "SHELL_OUTPUT_CAPTURED" not in captured.out + captured.err
    assert not (project / "command_ran").exists()
    assert artifact_path.read_bytes() == b""
    assert len(observed_fds) == 5
    assert len(duplicated_fds) == 1
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

    def record_artifact(root, capture_id):
        artifact = real_create(root, capture_id)
        observed_fds.append(artifact.fd)
        return artifact

    monkeypatch.setattr(capture_artifacts, "create_capture_artifact", record_artifact)
    monkeypatch.setattr(capture_artifacts, "_spawn_bash", lambda *_args, **_kwargs: process)

    assert run_capture("printf output", str(project), _CAPTURE_ID) == 0
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

    assert run_capture("printf output", str(project), _CAPTURE_ID) == 0

    captured = capfd.readouterr()
    artifact_path = _capture_dir(project) / f"shell_{_CAPTURE_ID}.log"
    assert "CAPTURE_FAILED" in captured.err
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
    monkeypatch.setattr(capture_artifacts.hashlib, "sha256", BrokenDigest)

    assert run_capture("printf output", str(project), _CAPTURE_ID) == 0
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

    assert run_capture("printf output", str(project), _CAPTURE_ID) == 0
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

    assert run_capture("printf ran > command_ran; printf output", str(project), _CAPTURE_ID) == 0
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

    assert run_capture("printf output", str(project), _CAPTURE_ID) == 0
    captured = capfd.readouterr()
    assert "CAPTURE_FAILED" in captured.err
    assert "SHELL_OUTPUT_CAPTURED" not in captured.out + captured.err
    assert len(observed_fds) == 6
    for fd in observed_fds:
        with pytest.raises(OSError):
            os.fstat(fd)
