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
from pathlib import Path

# Declared precondition, not a default to silently fall back on: the runner
# must offer both minors, or this step fails loudly (no silent caps).
_REQUIRED_PYTHON_MINORS = ("3.11", "3.13")


def _find_source_root() -> Path:
    """Return the installable source root (the pyproject.toml directory)."""
    from autoskillit.core import pkg_root

    candidate = pkg_root().parent.parent
    if not (candidate / "pyproject.toml").is_file():
        raise RuntimeError(f"could not locate pyproject.toml above pkg_root(): {candidate}")
    return candidate


def _run(cmd: list[str], *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=600)


def _cache_incarnations(cache_root: Path) -> set[str]:
    if not cache_root.is_dir():
        return set()
    return {p.name for p in cache_root.iterdir() if p.is_dir() and not p.name.startswith(".")}


def _preserve_pre_upgrade_incarnation(cache_root: Path, source_name: str, minor: str) -> str:
    """Create a valid distinct prior-version artifact for retention testing."""
    from autoskillit.core import (
        _AUTOSKILLIT_PLUGIN_KEY,
        ArtifactLease,
        installed_plugin_artifact_lease_path,
        installed_plugin_semantic_key,
    )
    from autoskillit.workspace._projected_artifact._manifest_publication import (
        write_installed_plugin_artifact_manifest_locked,
    )

    retained_name = f"0.0.0+preupgrade.py{minor.replace('.', '')}"
    source_dir = cache_root / source_name
    retained_dir = cache_root / retained_name
    if retained_dir.exists():
        raise RuntimeError(f"pre-upgrade retention fixture already exists: {retained_dir}")
    shutil.copytree(source_dir, retained_dir)
    metadata_path = retained_dir / ".claude-plugin" / "plugin.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["version"] = retained_name
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    with ArtifactLease.acquire_exclusive(
        installed_plugin_artifact_lease_path(retained_dir),
        blocking=True,
    ):
        write_installed_plugin_artifact_manifest_locked(
            retained_dir,
            semantic_key=installed_plugin_semantic_key(
                _AUTOSKILLIT_PLUGIN_KEY,
                retained_name,
            ),
            action="publish",
        )
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


def run_cross_interpreter_upgrade_smoke(*, work_dir: str) -> bool:
    """Install on one Python minor, upgrade on another, verify hooks survive.

    In a scratch HOME: ``uv tool install`` the current source tree with
    ``--python <A>``, publish the plugin, then upgrade with
    ``--python <B>`` (a genuinely different minor). Asserts the new cache
    incarnation exists and that executing the retained incarnation's
    PreToolUse commands verbatim (``${CLAUDE_PLUGIN_ROOT}`` expanded against
    that incarnation's own directory) exits 0 — the live-session-safety
    property Phase A's relocatable commands exist to guarantee.
    """
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

    cache_root = (
        scratch_home / ".claude" / "plugins" / "cache" / "autoskillit-local" / "autoskillit"
    )
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

    republish = _run([entrypoint, "install", "--maintenance-update"], env=env)
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

    return True
