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

**Why criterion (c) ("acquire a plugin launch binding") is exercised from
this test process, not the child.** The plan's literal T-C1/T-C2 wording
assumes the live process IS ``autoskillit`` itself, so it can call
``ProjectedPluginArtifactAuthority.acquire_launch_binding()`` directly. The
``faketool`` substitution above means the child's own venv has no
``autoskillit`` import available (adding it as a dependency would reintroduce
the network/minutes cost this substitution exists to avoid). Instead, this
test process — which does have ``autoskillit`` imported — exercises the
literal underlying primitive ``acquire_launch_binding()`` itself depends on:
acquiring a reader ``ArtifactLease`` against the generation's own lease path
(``authority.py``'s ``acquire_launch_binding()`` acquires exactly this lease,
see its ``reader = ArtifactLease.acquire_shared(plan.lease_path)`` calls;
here the lease is already published by ``publish_install_root_generation()``,
so ``acquire_existing_shared()`` is the accurate call, matching production's
own self-lease acquisition in ``core/_install_binding.py``), applied
directly to the child's own (superseded but retained) generation.

Every test here spawns real subprocesses and does real `uv tool install`
work, hence `large`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from autoskillit.core import (
    ArtifactLease,
    ArtifactLeaseContention,
    PluginArtifactIdentity,
    _InstallLock,
    generation_artifact_root,
    generation_staging_root,
    installed_plugin_artifact_lease_path,
    installed_plugin_semantic_key,
    new_plugin_artifact_incarnation_id,
)
from autoskillit.workspace import publish_install_root_generation

pytestmark = [pytest.mark.layer("cli"), pytest.mark.large]

_INSTALL_REF = "faketool-immunity-test@fake-local"

_PACKAGE_INIT = """
import json
import sys
import time
from pathlib import Path


def main() -> None:
    args = sys.argv[1:]
    if args and args[0] == "block":
        marker_file = Path(args[1])
        release_file = Path(args[2])
        checkpoint_file = Path(args[3]) if len(args) > 3 else None
        own_root = Path(__file__).parent
        # Prove we can read our own package root before blocking.
        content = (own_root / "__init__.py").read_text()
        if checkpoint_file is not None:
            # Phase 1 of an in-flight step: checkpoint state to disk before
            # blocking. Phase 2 (after release) must read this back and
            # build on it -- that dependency is what makes the continuation
            # real, rather than two independent no-op checks either side of
            # the boundary could pass on their own.
            checkpoint_file.write_text(json.dumps({"phase": 1, "content_len": len(content)}))
        marker_file.write_text("ready\\n")
        deadline = time.time() + 30
        while not release_file.exists():
            if time.time() > deadline:
                sys.stderr.write("timed out waiting for release file\\n")
                sys.exit(2)
            time.sleep(0.05)
        # Prove we can STILL read our own package root after being released,
        # and that a fresh lazy import of a sibling module still works --
        # never touched before this point, so this is genuinely new code
        # resolution from the old root, not already-loaded bytes.
        content = (own_root / "__init__.py").read_text()
        import faketool.lazy as lazy_mod

        fresh_value = lazy_mod.touch()
        combined = ""
        if checkpoint_file is not None:
            checkpoint = json.loads(checkpoint_file.read_text())
            assert checkpoint["phase"] == 1, checkpoint
            combined_value = checkpoint["content_len"] + fresh_value
            checkpoint_file.write_text(json.dumps({"phase": 2, "combined": combined_value}))
            combined = f":{combined_value}"
        print(f"SURVIVED:{len(content)}:{_version()}{combined}")
        return
    print("VERSION:" + _version())


def _version() -> str:
    import importlib.metadata

    return importlib.metadata.version("faketool")
"""

_PACKAGE_LAZY = "def touch() -> int:\n    return 41\n"

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


def _bump_source_version(source: Path, version: str) -> None:
    """Rewrite the fake package's own ``pyproject.toml`` version and commit it.

    ``importlib.metadata.version("faketool")`` reads the *installed
    package's own* metadata, not the generation-store version label passed
    to ``_install_root_generation`` -- those are otherwise two unrelated
    strings. Without this, every install reads the ``pyproject.toml``
    literal's static ``"0.0.0"`` regardless of which generation it landed
    in, and a version-unchanged assertion could never distinguish correct
    behavior from the pre-Phase-3 bug it exists to catch. Bumping and
    committing before each install makes the two genuinely track each
    other, so a resolved version really would drift if a later publish's
    root leaked into an earlier one's process.
    """
    (source / "pyproject.toml").write_text(
        _PYPROJECT.replace('version = "0.0.0"', f'version = "{version}"')
    )
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t.com"}
    subprocess.run(["git", "add", "-A"], cwd=source, env=env, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t.com",
            "-c",
            "user.name=t",
            "commit",
            "-q",
            "-m",
            f"v{version}",
        ],
        cwd=source,
        env=env,
        check=True,
        capture_output=True,
    )


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
    _bump_source_version(source, version)
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
            generation_root=generation_root,
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
    files, perform a fresh import, and (per criterion c, from this process --
    see the module docstring) acquire a launch-binding-shaped reader lease
    afterward -- and its resolved version has not silently drifted to v2's.
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

        # Criterion (c): acquiring a plugin-launch-binding-shaped reader
        # lease against v1's (now superseded, still retained) generation
        # must still succeed post-boundary -- see the module docstring for
        # why this runs here rather than inside the child.
        lease = ArtifactLease.acquire_existing_shared(
            installed_plugin_artifact_lease_path(identity_v1.managed_path)
        )
        lease.close()

        stdout, stderr = child.communicate(timeout=15)
        assert child.returncode == 0, f"child exited {child.returncode}: {stderr}"
        assert "SURVIVED:" in stdout, f"child did not report survival: {stdout!r}"
        version = stdout.strip().split(":")[2]
        assert version == "1.0.0", (
            f"child's resolved version drifted to {version!r} after the concurrent "
            "v1.0.1 publish -- its own site-packages resolution must stay anchored "
            "to the generation it started in, not silently pick up the new one"
        )
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def test_in_flight_pipeline_step_survives_concurrent_upgrade(
    tmp_path: Path, fake_git_source: Path
) -> None:
    """T-C2: an in-flight *step* -- not a bare import -- survives a
    concurrent upgrade across the whole boundary, and continues to
    completion. This is the acceptance criterion stated as an assertion:
    without it, Phase 3 is not complete.

    Two properties distinguish this from T-C1, both load-bearing:

    1. **Checkpointed continuation, not two independent no-ops.** The child
       writes a phase-1 checkpoint to disk before blocking, then after
       release reads it back and combines it with a value from a *fresh*
       post-boundary import (``faketool.lazy``, never touched before this
       point) to produce a phase-2 result. The test independently captures
       the phase-1 value (once the marker file appears) and the phase-2
       value (once the child exits) and asserts the arithmetic relationship
       between them holds. A child that merely survived without genuinely
       resuming the same step -- e.g. one reading stale or default state --
       cannot produce the correct combined value.
    2. **Reclamation is provably deferred for the whole step, not merely
       absent by accident.** This test process acquires a real shared
       ``ArtifactLease`` on v1's generation before the child even starts,
       representing the in-flight step's own launch binding (see the module
       docstring on why this runs here rather than inside the child), and
       proves a concurrent exclusive (reclaiming) lease attempt is refused
       while it is held, then succeeds the instant it is released.
    """
    home = tmp_path / "home"
    python_pin = _python_pin()

    identity_v1 = _install_root_generation(home, fake_git_source, "2.0.0", python_pin)
    inner_exe = identity_v1.managed_path / "faketool" / "bin" / "faketool"
    lease_path = installed_plugin_artifact_lease_path(identity_v1.managed_path)

    marker = home / "marker"
    release = home / "release"
    checkpoint = home / "checkpoint.json"

    # The in-flight step's own launch binding, held for the step's entire
    # duration -- acquired before the child even starts, released only after
    # it reports completion.
    step_lease = ArtifactLease.acquire_existing_shared(lease_path)
    try:
        child = subprocess.Popen(
            [str(inner_exe), "block", str(marker), str(release), str(checkpoint)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            deadline = time.time() + 15
            while not marker.exists():
                if child.poll() is not None:
                    out, err = child.communicate()
                    pytest.fail(
                        f"child exited early rc={child.returncode} out={out!r} err={err!r}"
                    )
                if time.time() > deadline:
                    child.kill()
                    pytest.fail("child never became ready")
                time.sleep(0.05)

            phase1 = json.loads(checkpoint.read_text())
            assert phase1["phase"] == 1

            # While the step is in flight, a concurrent reclaim attempt on
            # its own generation must be refused -- this is the concrete
            # guarantee behind "an in-flight step is never destroyed out
            # from under it": the same lease its launch binding holds is
            # what the retirement engine's try_reclaim() checks.
            with pytest.raises(ArtifactLeaseContention):
                ArtifactLease.acquire_exclusive(lease_path, blocking=False)

            # Two concurrent upgrades across the same in-flight step,
            # mirroring the issue's own observation that version bumps
            # arrive several times a day relative to session lifetimes.
            _install_root_generation(home, fake_git_source, "2.0.1", python_pin)
            _install_root_generation(home, fake_git_source, "2.0.2", python_pin)

            assert identity_v1.managed_path.is_dir()
            assert inner_exe.is_file()

            release.write_text("go\n")
            stdout, stderr = child.communicate(timeout=15)
            assert child.returncode == 0, f"child exited {child.returncode}: {stderr}"
            assert "SURVIVED:" in stdout, f"in-flight step did not complete: {stdout!r}"
            parts = stdout.strip().split(":")
            version, combined_str = parts[2], parts[3]
            assert version == "2.0.0", f"resolved version drifted mid-step: {version!r}"

            phase2 = json.loads(checkpoint.read_text())
            assert phase2["phase"] == 2, "the step never resumed past phase 1"
            expected_combined = phase1["content_len"] + 41
            assert phase2["combined"] == expected_combined == int(combined_str), (
                "phase 2 did not genuinely resume from phase 1's checkpointed "
                f"state: expected {expected_combined}, checkpoint says "
                f"{phase2['combined']}, child reported {combined_str}"
            )
        finally:
            if child.poll() is None:
                child.kill()
                child.wait(timeout=5)
    finally:
        step_lease.close()

    # Only after the step's own launch binding is released does reclaiming
    # v1's generation become possible -- proving the earlier refusal was
    # scoped to the lease's lifetime, not a permanent or accidental block.
    reclaim_lease = ArtifactLease.acquire_exclusive(lease_path, blocking=False)
    reclaim_lease.close()


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
        "real_from_name = m.Distribution.from_name\n"
        "m.Distribution.from_name = lambda name: real_from_name('faketool') "
        "if name == 'autoskillit' else real_from_name(name)\n"
        "from autoskillit.cli.install._install_info import detect_install\n"
        "info = detect_install()\n"
        "print(json.dumps({'install_type': info.install_type.value, "
        "'commit_id': info.commit_id, 'requested_revision': info.requested_revision, "
        "'url': info.url}))\n"
    )
    result = subprocess.run(
        [str(inner_python), "-c", probe],
        capture_output=True,
        text=True,
        timeout=15,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[2] / "src")},
    )
    assert result.returncode == 0, result.stderr
    import json

    payload = json.loads(result.stdout)
    assert payload["install_type"] == "git-vcs"
    assert payload["commit_id"]
    assert payload["requested_revision"] == "main"
    assert payload["url"] == f"file://{fake_git_source}"
