"""Tests for descriptor-anchored shell-capture authority."""

from __future__ import annotations

import json
import os
import subprocess
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


def test_setup_failure_prevents_user_command(tmp_path: Path) -> None:
    project = tmp_path / "project"
    temp_dir = project.joinpath(*CAPTURE_PATH_COMPONENTS[:2])
    temp_dir.mkdir(parents=True)
    (temp_dir / CAPTURE_PATH_COMPONENTS[2]).write_text("blocking file")

    with pytest.raises(CaptureSetupError):
        run_capture("printf ran > command_ran", str(project), _CAPTURE_ID)
    assert not (project / "command_ran").exists()


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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    observed_fds: list[int] = []
    real_create = capture_artifacts.create_capture_artifact

    def record_artifact(root, capture_id):
        artifact = real_create(root, capture_id)
        observed_fds.append(artifact.fd)
        return artifact

    def fail_spawn(*args, **kwargs):
        raise OSError("fault injection")

    monkeypatch.setattr(capture_artifacts, "create_capture_artifact", record_artifact)
    monkeypatch.setattr(subprocess, "Popen", fail_spawn)

    with pytest.raises(CaptureSetupError):
        run_capture("printf never", str(project), _CAPTURE_ID)

    assert observed_fds
    for fd in observed_fds:
        with pytest.raises(OSError):
            os.fstat(fd)


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
