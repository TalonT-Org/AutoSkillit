from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.conftest import production_interpreter_env

pytestmark = [pytest.mark.medium]


@pytest.mark.parametrize(
    ("version_returncode", "version_stdout", "projection_error", "error_match"),
    [
        (0, "1.1.0\n", False, None),
        (1, "1.1.0\n", False, "version probe failed"),
        (0, "not-a-version\n", False, "invalid post-upgrade version"),
        (0, "1.1.0\n", True, "projected assertion failed"),
    ],
)
def test_cross_interpreter_upgrade_smoke_validates_version_before_republish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    version_returncode: int,
    version_stdout: str,
    projection_error: bool,
    error_match: str | None,
) -> None:
    """The real smoke path must validate the probe before constructing republish argv."""
    from autoskillit import core
    from autoskillit.smoke_utils import _cross_interpreter_upgrade as smoke

    entrypoint = tmp_path / "bin" / "autoskillit"
    cache_root = tmp_path / "cache"
    run_calls: list[list[str]] = []

    def fake_subprocess_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if cmd[:3] == ["uv", "python", "find"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        assert cmd == [str(entrypoint), "--version"]
        return subprocess.CompletedProcess(
            cmd,
            version_returncode,
            stdout=version_stdout,
            stderr="probe error",
        )

    def fake_run(cmd: list[str], *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
        del env
        run_calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    incarnations = iter([{"current"}, {"current", "retained"}])
    monkeypatch.setattr(smoke.subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(smoke, "_run", fake_run)
    monkeypatch.setattr(smoke, "_find_source_root", lambda: tmp_path / "source")
    monkeypatch.setattr(smoke.shutil, "which", lambda _name, path: str(entrypoint))
    monkeypatch.setattr(core, "installed_plugin_cache_dir", lambda _home, _name: cache_root)
    monkeypatch.setattr(smoke, "_cache_incarnations", lambda _root: next(incarnations))
    monkeypatch.setattr(
        smoke,
        "_preserve_pre_upgrade_incarnation",
        lambda _root, _source, _minor: "retained",
    )
    monkeypatch.setattr(smoke, "_assert_incarnation_hooks_execute", lambda _path: None)

    def assert_projection(_home: Path) -> None:
        if projection_error:
            raise RuntimeError("projected assertion failed")

    monkeypatch.setattr(smoke, "_assert_projected_artifact_relocatable", assert_projection)
    monkeypatch.setattr(smoke, "_assert_overlapping_install_survives", lambda **_kwargs: None)

    if error_match is not None:
        with pytest.raises(RuntimeError, match=error_match):
            smoke.run_cross_interpreter_upgrade_smoke(work_dir=str(tmp_path))
        assert any("--maintenance-update" in cmd for cmd in run_calls) is projection_error
        return

    assert smoke.run_cross_interpreter_upgrade_smoke(work_dir=str(tmp_path)) is True
    republish = next(cmd for cmd in run_calls if "--maintenance-update" in cmd)
    assert republish == [
        str(entrypoint),
        "install",
        "--maintenance-update",
        "--expected-version",
        "1.1.0",
    ]


def test_overlap_child_script_survives_release(tmp_path: Path) -> None:
    """The overlap child's own block/release/fresh-import protocol works.

    Runs ``_OVERLAP_CHILD_SCRIPT`` directly via the CURRENT interpreter (which
    already has autoskillit importable in this test environment) rather than
    through a real install-root generation venv — this isolates the script's
    own correctness from ``uv``/dual-interpreter availability, mirroring how
    ``tests/cli/test_install_root_upgrade_immunity.py`` block/release-tests a
    throwaway package without needing a second interpreter for the script
    logic itself.
    """
    from autoskillit.smoke_utils import _cross_interpreter_upgrade as smoke

    marker = tmp_path / "marker"
    release = tmp_path / "release"

    child = subprocess.Popen(
        [sys.executable, "-c", smoke._OVERLAP_CHILD_SCRIPT, str(marker), str(release)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=production_interpreter_env(),
    )
    try:
        deadline = time.time() + 10
        while not marker.exists():
            if child.poll() is not None:
                out, err = child.communicate()
                pytest.fail(f"child exited early rc={child.returncode} out={out!r} err={err!r}")
            if time.time() > deadline:
                child.kill()
                pytest.fail("child never became ready")
            time.sleep(0.05)

        release.write_text("go\n")
        stdout, stderr = child.communicate(timeout=10)
        assert child.returncode == 0, f"child exited {child.returncode}: {stderr}"
        assert "SURVIVED:" in stdout, f"child did not report survival: {stdout!r}"
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def _fake_generation_identity(managed_path: Path) -> object:
    """A minimal stand-in for ``PluginArtifactIdentity`` -- the orchestration
    logic under test only ever reads ``.managed_path`` off the return value.
    """
    from types import SimpleNamespace

    return SimpleNamespace(managed_path=managed_path)


def _fake_generation_with_python3(tmp_path: Path, name: str) -> Path:
    """Build a fake ``<managed_path>/autoskillit/bin/python3`` this process can
    actually run and import ``autoskillit`` from.

    Symlinks the ``autoskillit`` directory component itself (not just the
    ``python3`` file) to the CURRENT venv root, so CPython's own venv/
    ``pyvenv.cfg`` resolution -- which walks up from the executable's fully
    resolved real path -- transparently resolves through the directory
    symlink to the real venv. Symlinking only the executable file breaks
    this: ``pyvenv.cfg`` would not be found relative to the fake path, and
    the child would fall back to the system interpreter, which does not have
    autoskillit importable.
    """
    generation_root = tmp_path / name
    generation_root.mkdir(parents=True)
    (generation_root / "autoskillit").symlink_to(Path(sys.executable).parent.parent)
    return generation_root


def test_assert_overlapping_install_survives_happy_path(tmp_path: Path) -> None:
    """The overlap orchestration blocks a real child, overlaps a second real
    publish while it is blocked, then releases it and requires survival.

    ``_publish_real_package_generation`` (the real ``uv tool install`` work)
    is mocked out; everything downstream of it -- the blocking child process,
    the marker/release protocol, and the post-release survival checks -- is
    real.
    """
    from autoskillit.smoke_utils import _cross_interpreter_upgrade as smoke

    generation_a = _fake_generation_with_python3(tmp_path, "gen-a")

    identities = iter(
        [
            _fake_generation_identity(generation_a),
            _fake_generation_identity(tmp_path / "gen-b"),
        ]
    )
    calls: list[dict[str, object]] = []

    def fake_publish(**kwargs: object) -> object:
        calls.append(kwargs)
        return next(identities)

    with patch.object(smoke, "_publish_real_package_generation", fake_publish):
        smoke._assert_overlapping_install_survives(
            scratch_home=tmp_path,
            source_root=tmp_path / "source",
            env={**os.environ, "HOME": str(tmp_path)},
            minor_a="3.11",
            minor_b="3.13",
            version="9.9.9",
        )

    assert [call["python_pin"] for call in calls] == ["3.11", "3.13"]
    assert all(call["version"] == "9.9.9" for call in calls)


def test_assert_overlapping_install_survives_detects_same_path_collision(
    tmp_path: Path,
) -> None:
    """A concurrent publish that lands at the SAME generation path as the
    still-live one must fail loudly, not silently succeed.
    """
    from autoskillit.smoke_utils import _cross_interpreter_upgrade as smoke

    generation_a = _fake_generation_with_python3(tmp_path, "gen-a")

    same_identity = _fake_generation_identity(generation_a)

    def fake_publish(**_kwargs: object) -> object:
        return same_identity

    with patch.object(smoke, "_publish_real_package_generation", fake_publish):
        with pytest.raises(RuntimeError, match="SAME generation path"):
            smoke._assert_overlapping_install_survives(
                scratch_home=tmp_path,
                source_root=tmp_path / "source",
                env={**os.environ, "HOME": str(tmp_path)},
                minor_a="3.11",
                minor_b="3.13",
                version="9.9.9",
            )
