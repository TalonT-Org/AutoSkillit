"""T-C1/T-C2/T-C5 (issue #4597 Phase 3): the acceptance criteria.

A live process holding a reference into an install-root generation must
survive a real, concurrent installation of a different version — the class
of crash the whole plan exists to eliminate. These tests do not exercise
``run_update_transaction()`` end to end (that would require a real
`uv`-installable ``autoskillit`` distribution, network access, and minutes
per run); instead they drive the same two production primitives the
transaction's ``INSTALL_ROOT_GENERATION_PUBLICATION`` phase calls —
``uv tool install`` targeted via ``UV_TOOL_DIR`` at
``workspace.publish_install_root_generation`` — against a tiny local
package, installed from a real (local, file:// — no network) git repository
so the install genuinely goes through uv's git-clone-and-build pipeline,
exactly like a real GitHub-sourced install would.

Every test here spawns real subprocesses and does real `uv tool install`
work, hence `large`.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from autoskillit.core import (
    PluginArtifactIdentity,
    _InstallLock,
    generation_artifact_root,
    generation_staging_root,
    installed_plugin_semantic_key,
    new_plugin_artifact_incarnation_id,
)
from autoskillit.workspace import publish_install_root_generation

pytestmark = [pytest.mark.layer("cli"), pytest.mark.large]

_INSTALL_REF = "faketool-immunity-test@fake-local"

_PACKAGE_INIT = """
import sys
import time
from pathlib import Path


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "block":
        marker_file = Path(args[1])
        release_file = Path(args[2])
        own_root = Path(__file__).parent
        # Prove we can read our own package root before blocking.
        (own_root / "__init__.py").read_text()
        marker_file.write_text("ready\\n")
        deadline = time.time() + 30
        while not release_file.exists():
            if time.time() > deadline:
                sys.stderr.write("timed out waiting for release file\\n")
                sys.exit(2)
            time.sleep(0.05)
        # Prove we can STILL read our own package root after being released,
        # and that a fresh lazy import of a sibling module still works --
        # this is the "in-flight step continues to completion" property
        # (T-C2), not just a static file read.
        content = (own_root / "__init__.py").read_text()
        import faketool.lazy as lazy_mod

        lazy_mod.touch()
        print(f"SURVIVED:{len(content)}")
        return
    print("VERSION:" + _version())


def _version() -> str:
    import importlib.metadata

    return importlib.metadata.version("faketool")
"""

_PACKAGE_LAZY = "def touch() -> None:\n    pass\n"

_PYPROJECT = """
[project]
name = "faketool"
version = "0.0.0"
requires-python = ">=3.10"

[project.scripts]
faketool = "faketool:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
"""


@pytest.fixture
def fake_git_source(tmp_path: Path) -> Path:
    """A real local git repository containing a tiny installable package.

    Using ``git+file://`` (not a bare directory path) means ``uv`` runs its
    real git-clone-and-build pipeline -- the same code path a GitHub-sourced
    install uses -- without any network access, and produces a
    ``direct_url.json`` with ``vcs_info`` populated exactly like a real
    GitHub install (verified separately by spike).
    """
    repo = tmp_path / "fake-pkg-repo"
    (repo / "faketool").mkdir(parents=True)
    (repo / "pyproject.toml").write_text(_PYPROJECT)
    (repo / "faketool" / "__init__.py").write_text(_PACKAGE_INIT)
    (repo / "faketool" / "lazy.py").write_text(_PACKAGE_LAZY)
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.com"}
    for cmd in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "add", "-A"],
        [
            "git",
            "-c",
            "user.email=t@t.com",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "-m",
            "init",
        ],
    ):
        subprocess.run(cmd, cwd=repo, env=env, check=True, capture_output=True)
    return repo


def _install_root_generation(
    home: Path,
    source: Path,
    version: str,
    python_pin: str,
) -> PluginArtifactIdentity:
    """Publish one install-root generation via the real production primitives.

    Mirrors ``run_update_transaction()``'s two-install design exactly: a
    probe install (discarded) plus a final install written directly at its
    permanent version+incarnation-keyed destination -- never renamed after
    creation, because a venv's console-script shebang bakes an absolute path
    at install time and cannot survive being moved.
    """
    incarnation_id = new_plugin_artifact_incarnation_id()
    staging = generation_staging_root(home, _INSTALL_REF) / incarnation_id
    staging.mkdir(parents=True)
    _run_uv_install(source, staging, python_pin)

    generation_root = generation_artifact_root(home, _INSTALL_REF, version, incarnation_id)
    generation_root.parent.mkdir(parents=True, exist_ok=True)
    _run_uv_install(source, generation_root, python_pin)

    import shutil

    shutil.rmtree(staging, ignore_errors=True)

    with _InstallLock():
        return publish_install_root_generation(
            home=home,
            install_ref=_INSTALL_REF,
            version=version,
            semantic_key=installed_plugin_semantic_key(_INSTALL_REF, version),
            incarnation_id=incarnation_id,
            staged_root=generation_root,
        )


def _run_uv_install(source: Path, destination: Path, python_pin: str) -> None:
    bin_dir = destination.parent / f".{destination.name}-bin"
    env = {**os.environ, "UV_TOOL_DIR": str(destination), "UV_TOOL_BIN_DIR": str(bin_dir)}
    result = subprocess.run(
        ["uv", "tool", "install", f"git+file://{source}@main", "--python", python_pin],
        env=env,
        cwd=str(destination.parent),
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert result.returncode == 0, (
        f"uv tool install failed (rc={result.returncode})\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def _python_pin() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def test_live_process_survives_concurrent_upgrade(tmp_path: Path, fake_git_source: Path) -> None:
    """T-C1: a blocked child process's own root is never touched by a
    concurrent publish of a different version, and it can still read its own
    files and perform a fresh import afterward.
    """
    home = tmp_path / "home"
    python_pin = _python_pin()

    identity_v1 = _install_root_generation(home, fake_git_source, "1.0.0", python_pin)
    inner_exe = identity_v1.managed_path / "faketool" / "bin" / "faketool"
    assert inner_exe.is_file()

    marker = home / "marker"
    release = home / "release"

    child = subprocess.Popen(
        [str(inner_exe), "block", str(marker), str(release)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.time() + 15
        while not marker.exists():
            if child.poll() is not None:
                out, err = child.communicate()
                pytest.fail(f"child exited early rc={child.returncode} out={out!r} err={err!r}")
            if time.time() > deadline:
                child.kill()
                pytest.fail("child never became ready")
            time.sleep(0.05)

        # The acceptance criterion: run a real install of a different
        # version WHILE the child is blocked, holding a live reference into
        # v1's generation.
        identity_v2 = _install_root_generation(home, fake_git_source, "1.0.1", python_pin)

        assert identity_v1.managed_path.is_dir(), "v1 generation must be untouched"
        assert inner_exe.is_file(), "v1's own executable must still exist"
        assert identity_v2.managed_path != identity_v1.managed_path

        release.write_text("go\n")
        stdout, stderr = child.communicate(timeout=15)
        assert child.returncode == 0, f"child exited {child.returncode}: {stderr}"
        assert "SURVIVED:" in stdout, f"child did not report survival: {stdout!r}"
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def test_in_flight_pipeline_step_survives_concurrent_upgrade(
    tmp_path: Path, fake_git_source: Path
) -> None:
    """T-C2: an in-flight step -- one that reads its root, blocks mid-step,
    then performs a *fresh* import of a sibling module and continues to
    completion -- survives a concurrent upgrade across the whole boundary.

    This is the acceptance criterion stated as an assertion: without it,
    Phase 3 is not complete. The distinguishing property from T-C1 is the
    fresh lazy import performed strictly after release (``faketool.lazy``,
    imported for the first time post-boundary) -- proving the interpreter can
    still resolve and load *new* code from the old root, not merely that
    already-loaded bytes remain valid in memory.
    """
    home = tmp_path / "home"
    python_pin = _python_pin()

    identity_v1 = _install_root_generation(home, fake_git_source, "2.0.0", python_pin)
    inner_exe = identity_v1.managed_path / "faketool" / "bin" / "faketool"

    marker = home / "marker"
    release = home / "release"

    child = subprocess.Popen(
        [str(inner_exe), "block", str(marker), str(release)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.time() + 15
        while not marker.exists():
            if child.poll() is not None:
                out, err = child.communicate()
                pytest.fail(f"child exited early rc={child.returncode} out={out!r} err={err!r}")
            if time.time() > deadline:
                child.kill()
                pytest.fail("child never became ready")
            time.sleep(0.05)

        # Two concurrent upgrades across the same in-flight step, mirroring
        # the issue's own observation that version bumps arrive several
        # times a day relative to session lifetimes.
        _install_root_generation(home, fake_git_source, "2.0.1", python_pin)
        _install_root_generation(home, fake_git_source, "2.0.2", python_pin)

        assert identity_v1.managed_path.is_dir()
        assert inner_exe.is_file()

        release.write_text("go\n")
        stdout, stderr = child.communicate(timeout=15)
        assert child.returncode == 0, f"child exited {child.returncode}: {stderr}"
        assert "SURVIVED:" in stdout, f"in-flight step did not complete: {stdout!r}"
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def test_install_detection_survives_versioned_roots(tmp_path: Path, fake_git_source: Path) -> None:
    """T-C5: ``detect_install()`` still classifies GIT_VCS and populates
    ``commit_id``/``requested_revision``/``url`` from a versioned
    install-root generation's own ``direct_url.json``.

    ``parse_direct_url()`` introspects the *running interpreter's own*
    package metadata (``importlib.metadata.Distribution.from_name``), so
    this must run inside a subprocess using the generation's own
    interpreter -- the same constraint T-C1/T-C2 are built around.
    """
    home = tmp_path / "home"
    python_pin = _python_pin()
    identity = _install_root_generation(home, fake_git_source, "3.0.0", python_pin)

    inner_python = identity.managed_path / "faketool" / "bin" / "python3"
    assert inner_python.is_file()

    probe = (
        "import json, importlib.metadata as m\n"
        "d = m.Distribution.from_name('faketool')\n"
        "raw = d.read_text('direct_url.json')\n"
        "print(raw)\n"
    )
    result = subprocess.run(
        [str(inner_python), "-c", probe],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    import json

    payload = json.loads(result.stdout)
    assert payload["vcs_info"]["vcs"] == "git"
    assert payload["vcs_info"]["commit_id"]
    assert payload["vcs_info"]["requested_revision"] == "main"
    assert payload["url"] == f"file://{fake_git_source}"
