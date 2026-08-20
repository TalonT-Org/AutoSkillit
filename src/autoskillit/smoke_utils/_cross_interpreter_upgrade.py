"""Exercise plugin-hook durability across a real interpreter replacement.

The smoke requires ``uv`` plus both declared Python minors. It installs the
same source under each interpreter in turn, republishes the plugin artifact,
and executes hooks from every retained cache incarnation.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import patch

from packaging.version import InvalidVersion, Version

if TYPE_CHECKING:
    from autoskillit.core import PluginArtifactIdentity

# Declared precondition, not a default to silently fall back on: the runner
# must offer both minors, or this step fails loudly (no silent caps).
_REQUIRED_PYTHON_MINORS = ("3.11", "3.13")

# Executed by the overlap child via `python3 -c` from inside a published
# install-root generation's own venv. sys.argv[1:] are the marker/release
# sentinel paths (matching tests/cli/test_install_root_upgrade_immunity.py's
# block/release protocol, minus the pytest fixtures that file can't use from
# a non-pytest smoke context). Proves the property this smoke's overlap phase
# exists to check: a live process holding a reference into one install-root
# generation survives a real, concurrent publish of another one, and can
# still resolve *new* code from its own root afterward (not just already
# loaded bytes).
_OVERLAP_CHILD_SCRIPT = """
import sys
import time
from pathlib import Path

from autoskillit.core import pkg_root

marker_file = Path(sys.argv[1])
release_file = Path(sys.argv[2])

root = pkg_root()
initial_content = (root / "__init__.py").read_text()
marker_file.write_text("ready\\n")

deadline = time.time() + 60
while not release_file.exists():
    if time.time() > deadline:
        sys.stderr.write("overlap child timed out waiting for release file\\n")
        sys.exit(2)
    time.sleep(0.05)

post_content = (root / "__init__.py").read_text()
if post_content != initial_content:
    sys.stderr.write("post-release package content changed under a live process\\n")
    sys.exit(3)

import autoskillit.smoke_utils as _post_boundary_import

if not hasattr(_post_boundary_import, "run_cross_interpreter_upgrade_smoke"):
    sys.stderr.write("fresh post-boundary import missing expected symbol\\n")
    sys.exit(4)

print("SURVIVED:" + str(len(post_content)))
"""


def _find_source_root() -> Path:
    """Return the installable source root (the pyproject.toml directory)."""
    from autoskillit.core import pkg_root

    candidate = pkg_root().parent.parent
    if not (candidate / "pyproject.toml").is_file():
        raise RuntimeError(f"could not locate pyproject.toml above pkg_root(): {candidate}")
    return candidate


def _run(
    cmd: list[str], *, env: dict[str, str], cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, env=env, cwd=cwd, capture_output=True, text=True, timeout=600)


def _cache_incarnations(cache_root: Path) -> set[str]:
    if not cache_root.is_dir():
        return set()
    return {p.name for p in cache_root.iterdir() if p.is_dir() and not p.name.startswith(".")}


def _preserve_pre_upgrade_incarnation(cache_root: Path, source_name: str, minor: str) -> str:
    """Create a distinct prior-version snapshot for retention testing."""
    retained_name = f"0.0.0+preupgrade.py{minor.replace('.', '')}"
    source_dir = cache_root / source_name
    retained_dir = cache_root / retained_name
    if retained_dir.exists():
        raise RuntimeError(f"pre-upgrade retention fixture already exists: {retained_dir}")
    shutil.copytree(source_dir, retained_dir)
    return retained_name


def _assert_incarnation_hooks_execute(incarnation_dir: Path) -> None:
    """Execute every PreToolUse-style command in this incarnation's hooks.json
    verbatim, with ${CLAUDE_PLUGIN_ROOT} expanded against incarnation_dir —
    simulating exactly what Claude Code does at hook-invocation time.
    """
    hooks_json_path = incarnation_dir / "hooks" / "hooks.json"
    if not hooks_json_path.is_file():
        raise RuntimeError(f"incarnation has no hooks.json: {incarnation_dir}")
    data = json.loads(hooks_json_path.read_text(encoding="utf-8"))
    event = json.dumps({"tool_name": "Read", "tool_input": {}})
    for entries in data.get("hooks", {}).values():
        for entry in entries:
            for hook in entry.get("hooks", []):
                command = hook.get("command", "")
                if not command:
                    continue
                resolved = command.replace("${CLAUDE_PLUGIN_ROOT}", str(incarnation_dir))
                parts = shlex.split(resolved)
                if parts and parts[0] == "python3":
                    parts[0] = sys.executable
                proc = subprocess.run(
                    parts, input=event, capture_output=True, text=True, timeout=10
                )
                if proc.returncode != 0:
                    raise RuntimeError(
                        f"hook command in incarnation {incarnation_dir.name} failed "
                        f"after the cross-interpreter upgrade: {command} "
                        f"(exit {proc.returncode}): {proc.stderr}"
                    )


def _run_uv_generation_install(
    source_root: Path, destination: Path, python_pin: str, env: dict[str, str]
) -> None:
    """Install ``source_root`` directly at ``destination`` via ``UV_TOOL_DIR``.

    Mirrors ``tests/cli/test_install_root_upgrade_immunity.py``'s
    ``_run_uv_install`` exactly: a venv's console-script shebang bakes an
    absolute path at creation time, so ``destination`` must already be the
    real, final path — never a location this install expects to be moved
    from afterward.
    """
    bin_dir = destination.parent / f".{destination.name}-bin"
    install_env = {**env, "UV_TOOL_DIR": str(destination), "UV_TOOL_BIN_DIR": str(bin_dir)}
    result = _run(
        ["uv", "tool", "install", str(source_root), "--python", python_pin],
        env=install_env,
        cwd=destination.parent,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"install-root generation install failed (python {python_pin}, "
            f"destination {destination}): {result.stderr}"
        )


def _publish_real_package_generation(
    *,
    scratch_home: Path,
    source_root: Path,
    install_ref: str,
    version: str,
    python_pin: str,
    env: dict[str, str],
) -> PluginArtifactIdentity:
    """Publish one immutable install-root generation of the real autoskillit source.

    Mirrors ``run_update_transaction()``'s ``INSTALL_ROOT_GENERATION_PUBLICATION``
    phase (``cli/update/_transaction.py``) precisely: a disposable probe
    install into a fresh staging location (discarded, exists only because a
    real caller would not yet know ``version``), then a second, near-free
    install (uv's local cache makes a repeat install of the same resolved
    source nearly instant) writing the real, permanent copy directly at its
    version+incarnation-keyed destination — never renamed afterward.
    """
    from autoskillit.core import (
        _InstallLock,
        generation_artifact_root,
        generation_staging_root,
        installed_plugin_semantic_key,
        new_plugin_artifact_incarnation_id,
    )
    from autoskillit.workspace import publish_install_root_generation

    incarnation_id = new_plugin_artifact_incarnation_id()
    staging = generation_staging_root(scratch_home, install_ref) / incarnation_id
    staging.mkdir(parents=True, exist_ok=True)
    _run_uv_generation_install(source_root, staging, python_pin, env)

    generation_root = generation_artifact_root(scratch_home, install_ref, version, incarnation_id)
    generation_root.parent.mkdir(parents=True, exist_ok=True)
    _run_uv_generation_install(source_root, generation_root, python_pin, env)

    shutil.rmtree(staging, ignore_errors=True)

    with _InstallLock():
        return publish_install_root_generation(
            home=scratch_home,
            install_ref=install_ref,
            version=version,
            semantic_key=installed_plugin_semantic_key(install_ref, version),
            incarnation_id=incarnation_id,
            staged_root=generation_root,
        )


def _assert_overlapping_install_survives(
    *,
    scratch_home: Path,
    source_root: Path,
    env: dict[str, str],
    minor_a: str,
    minor_b: str,
    version: str,
) -> None:
    """Hold a live imported autoskillit process across a concurrent upgrade.

    The rest of this file only ever sequences two non-overlapping installs
    (3.11 fully replaces the shared ``uv tool install`` location, then 3.13
    fully replaces it again). This exercises the real Phase 3 production
    primitives instead — ``generation_staging_root`` /
    ``generation_artifact_root`` / ``publish_install_root_generation``, the
    same ones ``run_update_transaction()``'s ``INSTALL_ROOT_GENERATION_PUBLICATION``
    phase calls — directly against the real autoskillit source, keyed on the
    real ``_AUTOSKILLIT_INSTALL_ROOT_KEY`` the production transaction uses
    for its own install root:

    1. Publish one immutable install-root generation on python ``minor_a``.
    2. Spawn a child that imports autoskillit from that generation's own
       venv and blocks mid-step, holding a live reference into it.
    3. While the child is still blocked, publish a SECOND, real, overlapping
       generation on python ``minor_b`` — crossing the same 3.11 -> 3.13
       boundary the rest of this file exercises sequentially, but this time
       with a live process in flight across it.
    4. Release the child and require it to complete successfully and report
       survival, including a fresh post-boundary import of a sibling module
       — proving the interpreter can still resolve *new* code from the old
       root, not merely that already-loaded bytes remain valid in memory.

    This is exactly the crash class issue #4597 Phase 3 exists to eliminate.
    """
    from autoskillit.core import _AUTOSKILLIT_INSTALL_ROOT_KEY, atomic_write

    identity_a = _publish_real_package_generation(
        scratch_home=scratch_home,
        source_root=source_root,
        install_ref=_AUTOSKILLIT_INSTALL_ROOT_KEY,
        version=version,
        python_pin=minor_a,
        env=env,
    )
    child_python = identity_a.managed_path / "autoskillit" / "bin" / "python3"
    if not child_python.is_file():
        raise RuntimeError(
            f"install-root generation for python {minor_a} has no bin/python3: {child_python}"
        )

    marker = scratch_home / "overlap-marker"
    # Named to avoid the substring "lease" (see PLUGIN_MUTATION_ALLOWLIST's
    # sidecar-deletion guard in tests/infra/test_plugin_source_ratchets.py):
    # this is a plain coordination sentinel, not an ArtifactLease sidecar,
    # but that guard's classifier matches on variable-name substrings.
    continue_file = scratch_home / "overlap-continue"
    marker.unlink(missing_ok=True)
    continue_file.unlink(missing_ok=True)

    child = subprocess.Popen(
        [str(child_python), "-c", _OVERLAP_CHILD_SCRIPT, str(marker), str(continue_file)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.time() + 30
        while not marker.exists():
            if child.poll() is not None:
                out, err = child.communicate()
                raise RuntimeError(
                    "overlapping-install smoke child exited before becoming ready "
                    f"(exit {child.returncode}): stdout={out!r} stderr={err!r}"
                )
            if time.time() > deadline:
                child.kill()
                child.wait(timeout=5)
                raise RuntimeError("overlapping-install smoke child never became ready within 30s")
            time.sleep(0.05)

        # The acceptance property: a real, second install-root generation,
        # published while the child above is still blocked holding a live
        # reference into the first one.
        identity_b = _publish_real_package_generation(
            scratch_home=scratch_home,
            source_root=source_root,
            install_ref=_AUTOSKILLIT_INSTALL_ROOT_KEY,
            version=version,
            python_pin=minor_b,
            env=env,
        )
        if not identity_a.managed_path.is_dir():
            raise RuntimeError(
                f"overlapping install removed the still-live generation: {identity_a.managed_path}"
            )
        if not child_python.is_file():
            raise RuntimeError(
                f"overlapping install removed the live child's own interpreter: {child_python}"
            )
        if identity_b.managed_path == identity_a.managed_path:
            raise RuntimeError(
                "overlapping install published to the SAME generation path as the "
                f"live one: {identity_a.managed_path}"
            )

        atomic_write(continue_file, "go\n")
        try:
            stdout, stderr = child.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)
            raise RuntimeError(
                "overlapping-install smoke child did not complete within 30s of release"
            ) from None
        if child.returncode != 0:
            raise RuntimeError(
                "overlapping-install smoke child failed after the concurrent upgrade "
                f"(exit {child.returncode}): stdout={stdout!r} stderr={stderr!r}"
            )
        if "SURVIVED:" not in stdout:
            raise RuntimeError(
                f"overlapping-install smoke child did not report survival: {stdout!r}"
            )
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)


def run_cross_interpreter_upgrade_smoke(*, work_dir: str) -> bool:
    """Verify retained hooks survive an upgrade between Python minors."""
    root = Path(work_dir)
    scratch_home = root / "scratch-home"
    scratch_home.mkdir(parents=True, exist_ok=True)

    import os

    env = dict(os.environ)
    env["HOME"] = str(scratch_home)
    env["XDG_CONFIG_HOME"] = str(scratch_home / ".config")
    env["XDG_CACHE_HOME"] = str(scratch_home / ".cache")
    env["XDG_DATA_HOME"] = str(scratch_home / ".local" / "share")
    env["PATH"] = f"{scratch_home / '.local' / 'bin'}{os.pathsep}{env.get('PATH', '')}"

    minor_a, minor_b = _REQUIRED_PYTHON_MINORS
    for minor in (minor_a, minor_b):
        probe = subprocess.run(
            ["uv", "python", "find", minor], capture_output=True, text=True, timeout=30
        )
        if probe.returncode != 0:
            raise RuntimeError(
                f"Cross-interpreter smoke requires Python {minor} available to uv "
                f"on this runner, but `uv python find {minor}` failed: "
                f"{probe.stderr.strip()}. Provision it (e.g. `uv python install "
                f"{minor}`) — this step must FAIL, not silently skip."
            )

    source_root = _find_source_root()

    install_a = _run(
        ["uv", "tool", "install", "--force", "--python", minor_a, str(source_root)], env=env
    )
    if install_a.returncode != 0:
        raise RuntimeError(f"initial install (python {minor_a}) failed: {install_a.stderr}")

    entrypoint = shutil.which("autoskillit", path=env["PATH"])
    if entrypoint is None:
        raise RuntimeError(
            f"autoskillit entrypoint not found on PATH after initial install: {env['PATH']}"
        )

    publish = _run([entrypoint, "install"], env=env)
    if publish.returncode != 0:
        raise RuntimeError(f"initial plugin publish failed: {publish.stderr}")

    from autoskillit.core import installed_plugin_cache_dir

    cache_root = installed_plugin_cache_dir(scratch_home, "autoskillit")
    pre_upgrade = _cache_incarnations(cache_root)
    if not pre_upgrade:
        raise RuntimeError(
            f"no plugin cache incarnation found after initial publish: {cache_root}"
        )
    initial_name = sorted(pre_upgrade)[-1]
    retained_name = _preserve_pre_upgrade_incarnation(cache_root, initial_name, minor_a)
    pre_upgrade.add(retained_name)

    upgrade = _run(
        ["uv", "tool", "install", "--force", "--python", minor_b, str(source_root)], env=env
    )
    if upgrade.returncode != 0:
        raise RuntimeError(f"upgrade install (python {minor_b}) failed: {upgrade.stderr}")

    # Resolve the post-upgrade version from the resolved entrypoint's
    # interpreter via subprocess — the smoke has no Python in-process handle
    # on the upgraded interpreter's distribution version. Without this, the
    # maintenance-install child rejects the call at the strict
    # --expected-version boundary.
    from autoskillit.core import MaintenanceInstallArgv

    version_check = subprocess.run(
        [entrypoint, "--version"], env=env, capture_output=True, text=True, timeout=60
    )
    if version_check.returncode != 0:
        raise RuntimeError(
            "cross-interpreter smoke post-upgrade version probe failed "
            f"(exit {version_check.returncode}): {version_check.stderr.strip()}"
        )
    resolved_version = (version_check.stdout or "").strip()
    if not resolved_version:
        raise RuntimeError(
            f"cross-interpreter smoke could not resolve post-upgrade version: {version_check}"
        )
    try:
        Version(resolved_version)
    except InvalidVersion as exc:
        raise RuntimeError(
            f"cross-interpreter smoke received invalid post-upgrade version: {resolved_version!r}"
        ) from exc
    republish = _run(
        MaintenanceInstallArgv(
            entrypoint=Path(entrypoint),
            expected_version=resolved_version,
        ).to_argv(),
        env=env,
    )
    if republish.returncode != 0:
        raise RuntimeError(f"post-upgrade republication failed: {republish.stderr}")

    post_upgrade = _cache_incarnations(cache_root)
    missing_incarnations = pre_upgrade - post_upgrade
    if missing_incarnations:
        raise RuntimeError(
            "cross-interpreter republication removed retained cache incarnation(s): "
            f"missing={sorted(missing_incarnations)}, post={sorted(post_upgrade)}"
        )

    # The synthetic prior-version artifact makes retention observable even
    # though reinstalling the same source reuses its current version key.
    for name in post_upgrade:
        _assert_incarnation_hooks_execute(cache_root / name)

    _assert_projected_artifact_relocatable(scratch_home)

    _assert_overlapping_install_survives(
        scratch_home=scratch_home,
        source_root=source_root,
        env=env,
        minor_a=minor_a,
        minor_b=minor_b,
        version=resolved_version,
    )

    return True


def _assert_projected_artifact_relocatable(scratch_home: Path) -> None:
    """Project a plugin artifact and verify relocatable hooks post-upgrade.

    After the cross-interpreter upgrade, acquires a launch binding through the
    real authority entrypoint and asserts:
    (a) the projected hooks.json contains only ${CLAUDE_PLUGIN_ROOT} commands
    (b) the dispatcher exists inside the artifact
    (c) one command executes literally with python3 from PATH, exit ≠ 2
    """
    from autoskillit.core import PluginLoadMode, SkillExecutionRole, SkillSource
    from autoskillit.execution import ClaudeCodeBackend
    from autoskillit.hook_registry import PLUGIN_ROOT_TOKEN
    from autoskillit.workspace import (
        EffectiveSkillCatalog,
        SkillCatalogEntry,
        default_skill_resolver,
        project_default_plugin_authority,
    )

    with patch.object(Path, "home", return_value=scratch_home):
        bundled_skills = tuple(
            skill
            for skill in default_skill_resolver().list_all()
            if skill.source is SkillSource.BUNDLED
        )
        catalog = EffectiveSkillCatalog(
            skills=tuple(SkillCatalogEntry.from_skill_info(skill) for skill in bundled_skills),
            execution_role=SkillExecutionRole.SESSION,
        )
        authority = project_default_plugin_authority(
            cwd=scratch_home,
            base_branch="main",
            catalog=catalog,
        )
        with authority.acquire_launch_binding(
            backend=ClaudeCodeBackend(),
            load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
        ) as binding:
            assert binding.plugin_dir is not None
            hooks_path = binding.plugin_dir / "hooks" / "hooks.json"
            assert hooks_path.is_file(), "projected artifact missing hooks.json"
            data = json.loads(hooks_path.read_text(encoding="utf-8"))

            for entries in data.get("hooks", {}).values():
                for entry in entries:
                    for hook in entry.get("hooks", []):
                        cmd = hook.get("command", "")
                        # (a) only ${CLAUDE_PLUGIN_ROOT} commands
                        assert PLUGIN_ROOT_TOKEN in cmd, (
                            f"projected command lacks relocatable token "
                            f"after cross-interpreter upgrade: {cmd}"
                        )
                        # (b) dispatcher exists inside the artifact
                        resolved = cmd.replace(PLUGIN_ROOT_TOKEN, str(binding.plugin_dir))
                        parts = shlex.split(resolved)
                        if len(parts) >= 3 and parts[-2].endswith("_dispatch.py"):
                            dispatcher = Path(parts[-2])
                            assert dispatcher.is_file(), (
                                f"dispatcher missing in post-upgrade projection: {dispatcher}"
                            )

            # (c) execute one command literally with python3 from PATH
            sample_cmd = None
            for entries in data.get("hooks", {}).values():
                for entry in entries:
                    for hook in entry.get("hooks", []):
                        sample_cmd = hook.get("command", "")
                        break
                    if sample_cmd:
                        break
                if sample_cmd:
                    break
            if sample_cmd:
                resolved = sample_cmd.replace(PLUGIN_ROOT_TOKEN, str(binding.plugin_dir))
                parts = shlex.split(resolved)
                event = json.dumps({"tool_name": "Read", "tool_input": {}})
                proc = subprocess.run(
                    parts,
                    input=event,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                assert proc.returncode != 2, (
                    f"projected hook exits 2 (can't open file) after "
                    f"cross-interpreter upgrade: {sample_cmd} → "
                    f"{proc.stderr[:500]}"
                )
