"""T1: hook dispatch must not self-mutate the installed artifact tree.

Every real hook script Claude Code invokes runs on the interpreter it finds
via ``sys.executable`` — the exact interpreter, no flags added — spawned as
a subprocess from ``_dispatch.py``. Unless that spawn (and every other real
spawn site in the dispatch pipeline, e.g. the native-shell capture runner)
explicitly disables bytecode writing, CPython silently drops
``__pycache__/*.pyc`` next to the source files it imports on every single
hook invocation, mutating the installed plugin artifact tree the moment a
tool call fires.

``task test-check`` masks this defect entirely: the Taskfile sets
``PYTHONDONTWRITEBYTECODE=1`` for the whole pytest process, and that
variable is inherited by every child ``subprocess.run`` call by default —
so bytecode is never written in-suite regardless of whether the dispatch
code defends itself. ``production_interpreter_env()`` strips that mask
(and ``PYTHONPYCACHEPREFIX``, its cache-redirection cousin) so the child
interpreter runs exactly as it does under a real Claude Code hook
invocation. The preliminary probe below proves the mask is actually
defeated before the main assertions place any trust in it.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from autoskillit.core import directory_tree_digest
from autoskillit.hook_registry import HOOKS_DIR
from autoskillit.hooks._capture_contract import CAPTURE_REQUEST_PROTOCOL_VERSION, CaptureRequest
from autoskillit.hooks.shell_capture_hook import _runner_argv

pytestmark = [pytest.mark.integration, pytest.mark.medium]

_DISPATCH_SCRIPT = HOOKS_DIR / "_dispatch.py"
_HOOK_SETTINGS_SCRIPT = HOOKS_DIR / "_hook_settings.py"
_QUOTA_GUARD_SCRIPT = HOOKS_DIR / "guards" / "quota_guard.py"


def production_interpreter_env() -> dict[str, str]:
    """Build an env dict that strips test-harness bytecode suppression."""
    env = dict(os.environ)
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPYCACHEPREFIX", None)
    return env


def _build_hooks_tree(dest_root: Path) -> Path:
    """Build a production-shaped ``hooks/`` tree under ``dest_root``.

    Copies the real ``_dispatch.py``, a real registered guard script
    (``quota_guard.py``), and the stdlib-only sibling module it imports
    (``_hook_settings.py``) — the same shape the installed plugin artifact
    ships. A synthetic stub hook wouldn't exercise the sibling-import path,
    which is exactly where a second, easy-to-miss bytecode write happens.
    """
    hooks_dir = dest_root / "hooks"
    guards_dir = hooks_dir / "guards"
    guards_dir.mkdir(parents=True)

    (hooks_dir / "_dispatch.py").write_text(_DISPATCH_SCRIPT.read_text())
    (hooks_dir / "_hook_settings.py").write_text(_HOOK_SETTINGS_SCRIPT.read_text())
    (guards_dir / "quota_guard.py").write_text(_QUOTA_GUARD_SCRIPT.read_text())

    return hooks_dir


def _find_bytecode_artifacts(root: Path) -> list[Path]:
    """Return every ``__pycache__`` dir or ``*.pyc`` file under ``root``."""
    return sorted({*root.rglob("__pycache__"), *root.rglob("*.pyc")})


def test_production_interpreter_env_defeats_bytecode_suppression() -> None:
    """Preliminary probe: confirm the harness's own mask is actually lifted.

    If this failed, the inertness test below would pass vacuously — the
    child interpreter would inherit ``-B``-equivalent suppression from the
    pytest harness itself rather than from ``_dispatch.py``'s own defenses.
    """
    probe = textwrap.dedent(
        """
        import sys
        print(int(sys.flags.dont_write_bytecode))
        print(sys.pycache_prefix)
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        env=production_interpreter_env(),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    dont_write_bytecode_flag, pycache_prefix_line = result.stdout.splitlines()
    assert dont_write_bytecode_flag == "0", (
        "production_interpreter_env() failed to lift PYTHONDONTWRITEBYTECODE — "
        "the child interpreter still has bytecode writing suppressed by the "
        "test harness, which would make the inertness assertions vacuous"
    )
    assert pycache_prefix_line == "None", (
        "production_interpreter_env() failed to lift PYTHONPYCACHEPREFIX — "
        f"child interpreter still has a cache prefix set: {pycache_prefix_line!r}"
    )


class TestDispatchArtifactInertness:
    def test_dispatch_does_not_mutate_artifact_tree(self, tmp_path: Path) -> None:
        hooks_dir = _build_hooks_tree(tmp_path)
        before_digest = directory_tree_digest(hooks_dir)
        before_entries = {p.relative_to(hooks_dir).as_posix() for p in hooks_dir.rglob("*")}

        env = production_interpreter_env()
        env["AUTOSKILLIT_LOG_DIR"] = str(tmp_path / "logs")

        result = subprocess.run(
            [sys.executable, str(hooks_dir / "_dispatch.py"), "guards/quota_guard"],
            input=b"{}",
            env=env,
            capture_output=True,
            timeout=30,
        )

        assert result.returncode == 0, (
            f"dispatch exited {result.returncode}, stderr={result.stderr!r}"
        )

        stray_bytecode = _find_bytecode_artifacts(hooks_dir)
        assert not stray_bytecode, (
            f"dispatch wrote bytecode artifacts into the artifact tree: {stray_bytecode}"
        )

        after_entries = {p.relative_to(hooks_dir).as_posix() for p in hooks_dir.rglob("*")}
        assert after_entries == before_entries, (
            f"dispatch changed the artifact tree's file set — "
            f"added={after_entries - before_entries}, removed={before_entries - after_entries}"
        )

        after_digest = directory_tree_digest(hooks_dir)
        assert after_digest == before_digest, (
            "dispatch mutated the artifact tree (digest mismatch after execution) — "
            "some entry's kind, mode, or content changed even though the file set matched"
        )


class TestRunnerSpawnArtifactInertness:
    """shell_capture_hook is the second real spawn site in the dispatch
    pipeline (Codex native-shell capture). It shares the same defect class
    as ``_dispatch.py``'s internal subprocess spawn: unless the runner argv
    disables bytecode writing, the isolated runner script (and whatever it
    imports) gets a stray ``__pycache__`` written next to it on every
    captured shell command. Asserting on the built argv rather than
    executing the runner keeps this test focused on the spawn-site defect
    itself and avoids duplicating T3's coverage of ``_capture_artifacts.py``.
    """

    def test_runner_argv_disables_bytecode_writing(self) -> None:
        request = CaptureRequest(
            protocol_version=CAPTURE_REQUEST_PROTOCOL_VERSION,
            action="run",
            mode="capture",
            attempt_id=None,
            lineage_ref=None,
            cwd="/abs/project",
            capture_id="0123456789abcdef",
            command="printf ok",
        )

        argv = _runner_argv(request)

        assert "-B" in argv, f"runner spawn argv missing -B flag: {argv}"
