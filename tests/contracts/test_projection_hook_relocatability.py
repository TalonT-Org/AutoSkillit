"""Projection hook relocatability and deployed-artifact executability.

T-A1: Projections are relocatable even when the bundled source is stale.
T-A3: Literal executability of the deployed artifact.
T-A5: Cache-hit reuse fails closed on divergent published hooks.
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from autoskillit.core import pkg_root
from autoskillit.hook_registry import HOOK_REGISTRY_HASH, PLUGIN_ROOT_TOKEN

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]


def _forbidden_segments() -> tuple[str, ...]:
    return (
        "site-packages",
        "/lib/python",
        "uv/tools",
        str(pkg_root()),
        sys.prefix,
    )


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
        shutil.copytree(pkg_root(), fake_source, symlinks=False)
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
                        for segment in _forbidden_segments():
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
        from autoskillit.core import PluginLoadMode
        from autoskillit.execution.backends.claude import ClaudeCodeBackend
        from autoskillit.workspace import project_default_plugin_authority
        from tests.contracts._projection_helpers import session_catalog

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

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
            payload = json.dumps({"tool_name": "Read", "tool_input": {}})
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
                            timeout=10,
                        )
                        assert result.returncode != 2, (
                            f"hook command exited 2 (can't open file) — "
                            f"the literal command Claude Code would execute "
                            f"fails:\n  command: {cmd}\n  resolved: {resolved}\n"
                            f"  stderr: {result.stderr[:500]}"
                        )


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
