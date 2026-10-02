"""Projection hook relocatability and deployed-artifact executability.

T-A1: Projections are relocatable even when the bundled source is stale.
T-A3: Literal executability of the deployed artifact.
T-A5: Cache-hit reuse fails closed on divergent published hooks.
T-A6: Installed trees contain the scanned hook runtime import closure.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from autoskillit.core import pkg_root
from autoskillit.hook_registry import HOOK_REGISTRY_HASH, PLUGIN_ROOT_TOKEN
from tests._hook_import_closure import scan_shipped_import_closure, shipped_python_files
from tests.conftest import production_interpreter_env
from tests.contracts._relocatability_helpers import environment_pinned_path_segments

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]


_ROOT_DEPENDENCY_PROBE = (
    "import importlib.util, json, sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "def _origin(spec):\n"
    "    if spec is None:\n"
    "        return None\n"
    "    return spec.origin or next(iter(spec.submodule_search_locations or ()), None)\n"
    "print(json.dumps({n: _origin(importlib.util.find_spec(n)) for n in sys.argv[2:]}))\n"
)


@contextmanager
def _installed_tree(builder: str, home: Path) -> Iterator[Path]:
    from autoskillit.core import PluginLoadMode
    from autoskillit.execution.backends.claude import ClaudeCodeBackend
    from autoskillit.execution.backends.codex import CodexBackend
    from autoskillit.workspace import (
        SkillProjectionContext,
        materialize_sanitized_plugin_root,
        project_default_plugin_authority,
    )
    from tests.contracts._projection_helpers import session_catalog

    catalog = session_catalog()
    if builder == "sanitized-install-root":
        destination = home / "plugins" / "autoskillit"
        materialize_sanitized_plugin_root(
            pkg_root(),
            destination,
            catalog,
            SkillProjectionContext(cwd=home, catalog=catalog),
        )
        yield destination
        return

    authority = project_default_plugin_authority(cwd=home, base_branch="main", catalog=catalog)
    with authority.acquire_launch_binding(
        backend=ClaudeCodeBackend() if builder == "claude-projection" else CodexBackend(),
        load_mode=(
            PluginLoadMode.EXPLICIT_PLUGIN_DIR
            if builder == "claude-projection"
            else PluginLoadMode.PROJECTED_HOME
        ),
    ) as binding:
        assert binding.plugin_dir is not None
        yield binding.plugin_dir


class TestProjectedHooksAreRelocatable:
    """T-A1: Projections use relocatable commands regardless of source state."""

    def test_projection_uses_relocatable_commands_even_with_stale_source(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Plant the incident's exact stale shape in the source hooks.json,
        acquire a launch binding, and verify the projection commands are
        relocatable.
        """
        import autoskillit.core.paths as _paths
        from autoskillit.core import PluginLoadMode
        from autoskillit.execution.backends.claude import ClaudeCodeBackend
        from autoskillit.workspace import project_default_plugin_authority
        from tests.contracts._projection_helpers import session_catalog

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        # Build a fake source root from the real one
        fake_source = tmp_path / "fake-pkg"
        shutil.copytree(
            pkg_root(),
            fake_source,
            symlinks=False,
            ignore=shutil.ignore_patterns("__pycache__", "*.py[co]"),
        )
        monkeypatch.setattr(_paths, "pkg_root", lambda: fake_source)

        # Plant the incident's stale shape: valid structure, current hash,
        # but absolute interpreter-pinned commands
        stale_hooks = {
            "_autoskillit_registry_hash": HOOK_REGISTRY_HASH,
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Read",
                        "hooks": [
                            {
                                "type": "command",
                                "command": (
                                    "python3 /nonexistent/uv/tools/autoskillit/"
                                    "lib/python3.11/site-packages/autoskillit/"
                                    "hooks/_dispatch.py guards/tool_guard"
                                ),
                            }
                        ],
                    }
                ],
            },
        }
        hooks_json = fake_source / "hooks" / "hooks.json"
        hooks_json.write_text(json.dumps(stale_hooks, indent=2) + "\n")

        catalog = session_catalog()
        authority = project_default_plugin_authority(
            cwd=tmp_path,
            base_branch="main",
            catalog=catalog,
        )
        with authority.acquire_launch_binding(
            backend=ClaudeCodeBackend(),
            load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
        ) as binding:
            assert binding.plugin_dir is not None
            projected_hooks = json.loads((binding.plugin_dir / "hooks" / "hooks.json").read_text())
            for event_type, entries in projected_hooks.get("hooks", {}).items():
                for entry in entries:
                    for hook in entry.get("hooks", []):
                        cmd = hook["command"]
                        assert PLUGIN_ROOT_TOKEN in cmd, (
                            f"projected hook command lacks relocatable token: {cmd}"
                        )
                        for segment in environment_pinned_path_segments():
                            assert segment not in cmd, (
                                f"projected command contains forbidden segment {segment!r}: {cmd}"
                            )
                        # Verify the token-resolved target exists
                        resolved = cmd.replace(PLUGIN_ROOT_TOKEN, str(binding.plugin_dir))
                        parts = shlex.split(resolved)
                        dispatcher = Path(parts[2])
                        assert dispatcher.is_file(), (
                            f"projected dispatcher does not exist: {dispatcher}"
                        )


class TestDeployedArtifactExecutability:
    """T-A3: Literal executability of projected hook commands."""

    @pytest.mark.skipif(shutil.which("python3") is None, reason="python3 not on PATH")
    def test_projected_hook_commands_execute_without_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every literal projected hook command must run to completion with exit 0."""
        from autoskillit.core import PluginLoadMode
        from autoskillit.execution.backends.claude import ClaudeCodeBackend
        from autoskillit.workspace import project_default_plugin_authority
        from tests.contracts._projection_helpers import session_catalog

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        hook_cwd = tmp_path / "projected-hook-cwd"
        hook_cwd.mkdir()
        env = production_interpreter_env()
        env.pop("PYTHONPATH", None)

        catalog = session_catalog()
        authority = project_default_plugin_authority(
            cwd=tmp_path,
            base_branch="main",
            catalog=catalog,
        )
        with authority.acquire_launch_binding(
            backend=ClaudeCodeBackend(),
            load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
        ) as binding:
            assert binding.plugin_dir is not None
            projected_hooks = json.loads((binding.plugin_dir / "hooks" / "hooks.json").read_text())
            payload = json.dumps(
                {
                    "tool_name": "Read",
                    "tool_input": {},
                    "session_id": "projection-executability",
                    "cwd": str(hook_cwd),
                }
            )
            failures: list[str] = []
            for event_type, entries in projected_hooks.get("hooks", {}).items():
                for entry in entries:
                    for hook in entry.get("hooks", []):
                        cmd = hook["command"]
                        resolved = cmd.replace(PLUGIN_ROOT_TOKEN, str(binding.plugin_dir))
                        parts = shlex.split(resolved)
                        # Use python3 from PATH, not sys.executable
                        dispatcher = Path(parts[2])
                        assert dispatcher.is_file(), (
                            f"dispatcher target does not exist: {dispatcher}"
                        )
                        result = subprocess.run(
                            parts,
                            input=payload,
                            capture_output=True,
                            text=True,
                            env=env,
                            cwd=hook_cwd,
                            timeout=10,
                        )
                        if result.returncode != 0:
                            failures.append(
                                f"{event_type} {cmd}\n  resolved: {resolved}\n"
                                f"  exit: {result.returncode}\n"
                                f"  stderr tail: {result.stderr[-1500:]}"
                            )
            assert not failures, (
                "projected hook commands must run to completion in a real installed tree "
                "(exit 0); a nonzero exit here is a hook that crashes for every user of "
                "this projection\n" + "\n".join(failures)
            )


class TestInstalledTreeImportClosure:
    """T-A6: every installed tree ships the scanned Python, and hook root dependencies
    resolve inside it.
    """

    @pytest.mark.parametrize(
        "builder",
        ("claude-projection", "codex-projection", "sanitized-install-root"),
        ids=("claude-projection", "codex-projection", "sanitized-install-root"),
    )
    def test_every_installed_tree_ships_the_scanned_python_set(
        self, builder: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        expected = {p.relative_to(pkg_root()).as_posix() for p in shipped_python_files(pkg_root())}
        assert "hooks/_dispatch.py" in expected

        with _installed_tree(builder, tmp_path) as tree:
            actual = {
                p.relative_to(tree).as_posix()
                for p in tree.rglob("*.py")
                if "__pycache__" not in p.parts
            }
            assert actual == expected, (
                f"{builder} Python files differ from the scanned set: "
                f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
            )

    def test_hook_root_dependencies_resolve_inside_generated_projection(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.core import PluginLoadMode
        from autoskillit.execution.backends.claude import ClaudeCodeBackend
        from autoskillit.workspace import project_default_plugin_authority
        from tests.contracts._projection_helpers import session_catalog

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        report = scan_shipped_import_closure(pkg_root())
        modules = sorted(name.removesuffix(".py") for name in report.root_dependencies)
        assert modules, "vacuous probe: no hook root dependencies were discovered"

        authority = project_default_plugin_authority(
            cwd=tmp_path, base_branch="main", catalog=session_catalog()
        )
        with authority.acquire_launch_binding(
            backend=ClaudeCodeBackend(),
            load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
        ) as binding:
            assert binding.plugin_dir is not None
            plugin_root = binding.plugin_dir.resolve()
            probe_cwd = tmp_path / "root-dependency-probe"
            probe_cwd.mkdir()
            env = production_interpreter_env()
            env.pop("PYTHONPATH", None)
            result = subprocess.run(
                [
                    sys.executable,
                    "-E",
                    "-s",
                    "-S",
                    "-B",
                    "-c",
                    _ROOT_DEPENDENCY_PROBE,
                    str(plugin_root),
                    *modules,
                ],
                capture_output=True,
                text=True,
                cwd=probe_cwd,
                env=env,
                timeout=30,
            )
            assert result.returncode == 0, result.stderr
            origins: dict[str, str | None] = json.loads(result.stdout)
            misplaced = {
                module: origin
                for module, origin in origins.items()
                if origin is None or not Path(origin).resolve().is_relative_to(plugin_root)
            }
            assert not misplaced, (
                "hook root dependencies must resolve inside the generated projection; "
                "None means a module is absent, and an outside origin means the stdlib "
                f"or site-packages satisfied it: {misplaced}"
            )
        assert binding.closed


class TestCacheHitReuseSafety:
    """T-A5: Tampered published hooks are rejected on cache-hit reuse."""

    def test_tampered_published_hooks_trigger_restaging(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A published incarnation whose hooks.json has been altered out-of-band
        must not be reused — the tree digest compare must reject it.
        """
        from autoskillit.core import PluginLoadMode
        from autoskillit.execution.backends.claude import ClaudeCodeBackend
        from autoskillit.workspace import project_default_plugin_authority
        from tests.contracts._projection_helpers import session_catalog

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        backend = ClaudeCodeBackend()
        catalog = session_catalog()

        authority = project_default_plugin_authority(
            cwd=tmp_path,
            base_branch="main",
            catalog=catalog,
        )

        # First binding publishes a healthy projection
        first = authority.acquire_launch_binding(
            backend=backend,
            load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
        )
        first_dir = first.plugin_dir
        assert first_dir is not None
        first.close()
        assert first.closed

        # Tamper with the published hooks.json WITHOUT touching the manifest
        hooks_path = first_dir / "hooks" / "hooks.json"
        original = hooks_path.read_text()
        hooks_path.write_text(original + "\n/* tampered */\n")

        # Second binding — same cache key (cache-hit path).
        # The tampered incarnation must be rejected and restaged.
        authority2 = project_default_plugin_authority(
            cwd=tmp_path,
            base_branch="main",
            catalog=catalog,
        )
        second = authority2.acquire_launch_binding(
            backend=backend,
            load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
        )
        try:
            assert second.plugin_dir is not None
            # The restaged hooks.json must be freshly rendered and relocatable
            new_hooks = json.loads((second.plugin_dir / "hooks" / "hooks.json").read_text())
            for entries in new_hooks.get("hooks", {}).values():
                for entry in entries:
                    for hook in entry.get("hooks", []):
                        assert PLUGIN_ROOT_TOKEN in hook["command"]
        finally:
            second.close()
        assert second.closed


# ── REQ-HOOKS-004: dispatcher bootstrap must expose hooks/_runtime/ ────────────


def test_dispatch_py_sys_path_includes_runtime_subdir() -> None:
    """REQ-HOOKS-004: `hooks/_dispatch.py` sys.path bootstrap prepends `hooks/_runtime/`.

    After #4672's decomposition, hook-script utilities live at
    `hooks/_runtime/`. The dispatcher subprocess spawns child hook scripts
    via `subprocess.run([sys.executable, "-B", str(target)], ...)` with a
    fresh process that does NOT inherit `_dispatch.py`'s sys.path. Each
    child script then runs `from _<x> import …` (bare-name) which requires
    `hooks/_runtime/` on its sys.path. The dispatcher must therefore add
    BOTH `hooks/` and `hooks/_runtime/` to its bootstrap.

    This test reads `hooks/_dispatch.py` source and asserts the runtime
    subdir is referenced by the bootstrap block (regardless of insertion
    order). It does NOT execute the dispatcher (subprocess bootstrap is
    verified by `TestProjectedHooksAreRelocatable`).
    """
    from autoskillit.core import pkg_root

    dispatch_path = pkg_root() / "hooks" / "_dispatch.py"
    assert dispatch_path.is_file(), f"dispatcher missing: {dispatch_path}"
    source = dispatch_path.read_text(encoding="utf-8")

    # The bootstrap block must reference the _runtime subdir explicitly (see docstring).
    assert "_runtime" in source, (
        "hooks/_dispatch.py sys.path bootstrap does not reference "
        "`hooks/_runtime/` — hook subprocesses would fail to import "
        "moved utilities like `_hook_settings` (REQ-HOOKS-004)."
    )

    # A _RUNTIME_DIR variable should resolve to hooks/_runtime (see docstring).
    assert "_RUNTIME_DIR" in source, (
        "hooks/_dispatch.py is missing the `_RUNTIME_DIR = ... / `_runtime`` "
        "bootstrap entry required for bare-name imports after #4672."
    )
