"""Publication gate and projection repair contract tests.

T-B1: Staged artifacts with broken hook commands refuse to publish.
T-B2: Startup repair heals a stale projection.
T-B3: Repair respects the lease.
T-B4: Stale generator refuses to bind.
T-B5: CLAUDECODE does not disable in-process repair.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoskillit.hook_registry import PLUGIN_ROOT_TOKEN

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]


class TestValidateStagedPluginHooks:
    """T-B1 (unit level): validate_staged_plugin_hooks rejects broken commands."""

    def test_relocatable_commands_with_live_dispatcher_pass(self, tmp_path: Path) -> None:
        from autoskillit.workspace._projected_artifact._hook_repair import (
            validate_staged_plugin_hooks,
        )

        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        dispatcher = hooks_dir / "_dispatch.py"
        dispatcher.write_text("# dispatcher\n")
        hooks_data = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Read",
                        "hooks": [
                            {
                                "type": "command",
                                "command": (
                                    f'python3 -B "{PLUGIN_ROOT_TOKEN}'
                                    f'/hooks/_dispatch.py" guards/tool_guard'
                                ),
                            }
                        ],
                    }
                ]
            }
        }
        (hooks_dir / "hooks.json").write_text(json.dumps(hooks_data))
        validate_staged_plugin_hooks(tmp_path)  # should not raise

    def test_absolute_commands_are_rejected(self, tmp_path: Path) -> None:
        from autoskillit.workspace._projected_artifact._hook_repair import (
            ProjectedArtifactHooksInvalid,
            validate_staged_plugin_hooks,
        )

        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        hooks_data = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Read",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "python3 /nonexistent/uv/hooks/_dispatch.py foo",
                            }
                        ],
                    }
                ]
            }
        }
        (hooks_dir / "hooks.json").write_text(json.dumps(hooks_data))
        with pytest.raises(ProjectedArtifactHooksInvalid, match="not relocatable"):
            validate_staged_plugin_hooks(tmp_path)

    def test_token_form_with_missing_dispatcher_is_rejected(self, tmp_path: Path) -> None:
        from autoskillit.workspace._projected_artifact._hook_repair import (
            ProjectedArtifactHooksInvalid,
            validate_staged_plugin_hooks,
        )

        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        # No _dispatch.py file
        hooks_data = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Read",
                        "hooks": [
                            {
                                "type": "command",
                                "command": (
                                    f'python3 -B "{PLUGIN_ROOT_TOKEN}'
                                    f'/hooks/_dispatch.py" guards/tool_guard'
                                ),
                            }
                        ],
                    }
                ]
            }
        }
        (hooks_dir / "hooks.json").write_text(json.dumps(hooks_data))
        with pytest.raises(ProjectedArtifactHooksInvalid, match="does not exist"):
            validate_staged_plugin_hooks(tmp_path)


class TestProjectionRepair:
    """T-B2: Startup repair heals a stale projection."""

    def test_startup_repair_heals_a_stale_projection(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.workspace._projected_artifact._hook_repair import (
            PluginHookRepairStatus,
            repair_broken_projection_hooks,
        )
        from autoskillit.workspace._projection_cache import (
            projected_artifact_lease_path,
            projected_artifact_manifest_path,
            projected_plugin_artifact_digest,
        )

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        projections_root = tmp_path / ".autoskillit" / "plugin-projections"
        proj = projections_root / "deadbeefcafe0123"
        hooks_dir = proj / "hooks"
        hooks_dir.mkdir(parents=True)
        # Plant the dispatcher script so repair targets exist
        dispatcher = hooks_dir / "_dispatch.py"
        dispatcher.write_text("# dispatcher\n")
        # Plant stale absolute commands
        stale_hooks = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Read",
                        "hooks": [
                            {
                                "type": "command",
                                "command": (
                                    "python3 /deleted/venv/hooks/_dispatch.py guards/tool_guard"
                                ),
                            }
                        ],
                    }
                ]
            }
        }
        (hooks_dir / "hooks.json").write_text(json.dumps(stale_hooks, indent=2) + "\n")
        # Write an initial manifest so repair can update it
        manifest_path = projected_artifact_manifest_path(proj)
        initial_digest = projected_plugin_artifact_digest(proj)
        manifest_data = {
            "schema_version": 2,
            "artifact_kind": "projection",
            "projection_version": 2,
            "semantic_key": "deadbeefcafe0123",
            "incarnation_id": "test-incarnation",
            "artifact_digest": initial_digest,
            "skills": {},
        }
        manifest_path.write_text(json.dumps(manifest_data, indent=2) + "\n")
        # Ensure lease path directory exists
        lease_path = projected_artifact_lease_path(proj)
        lease_path.parent.mkdir(parents=True, exist_ok=True)

        outcomes = repair_broken_projection_hooks(projections_root)

        assert len(outcomes) == 1
        assert outcomes[0].status is PluginHookRepairStatus.REPAIRED
        # Verify commands are now relocatable
        repaired = json.loads((hooks_dir / "hooks.json").read_text())
        for entries in repaired.get("hooks", {}).values():
            for entry in entries:
                for hook in entry.get("hooks", []):
                    assert PLUGIN_ROOT_TOKEN in hook["command"]
        # Verify manifest was updated so digest agrees
        updated_manifest = json.loads(manifest_path.read_text())
        new_digest = projected_plugin_artifact_digest(proj)
        assert updated_manifest["artifact_digest"] == new_digest

    def test_repair_skips_contended_lease(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T-B3: Repair respects the lease — skips without modifying."""
        from autoskillit.core import ArtifactLease
        from autoskillit.workspace._projected_artifact._hook_repair import (
            PluginHookRepairStatus,
            repair_broken_projection_hooks,
        )
        from autoskillit.workspace._projection_cache import (
            projected_artifact_lease_path,
        )

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        projections_root = tmp_path / ".autoskillit" / "plugin-projections"
        proj = projections_root / "contended-key"
        hooks_dir = proj / "hooks"
        hooks_dir.mkdir(parents=True)
        (hooks_dir / "_dispatch.py").write_text("# dispatcher\n")
        stale_hooks = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Read",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "python3 /deleted/hooks/_dispatch.py foo",
                            }
                        ],
                    }
                ]
            }
        }
        original_text = json.dumps(stale_hooks, indent=2) + "\n"
        (hooks_dir / "hooks.json").write_text(original_text)
        lease_path = projected_artifact_lease_path(proj)
        lease_path.parent.mkdir(parents=True, exist_ok=True)

        # Hold an exclusive lease to simulate contention
        with ArtifactLease.acquire_exclusive(lease_path, blocking=False):
            outcomes = repair_broken_projection_hooks(projections_root)

        assert len(outcomes) == 1
        assert outcomes[0].status is PluginHookRepairStatus.CONTENDED
        # File must be unchanged
        assert (hooks_dir / "hooks.json").read_text() == original_text


class TestStaleGeneratorRefusal:
    """T-B4: Stale generator refuses to bind."""

    def test_deleted_pkg_root_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import autoskillit.core.paths as _paths
        from autoskillit.workspace._projected_artifact.authority import (
            StaleGeneratorError,
            assert_generator_process_fresh,
        )

        monkeypatch.setattr(_paths, "pkg_root", lambda: tmp_path / "nonexistent")
        with pytest.raises(StaleGeneratorError, match="no longer exists"):
            assert_generator_process_fresh()

    def test_version_mismatch_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.workspace._projected_artifact.authority import (
            StaleGeneratorError,
            assert_generator_process_fresh,
        )

        # Use real pkg_root so the directory and dispatcher exist
        monkeypatch.setattr(
            "importlib.metadata.version",
            lambda name: "0.0.0-changed" if name == "autoskillit" else name,
        )
        with pytest.raises(StaleGeneratorError, match="upgraded under this process"):
            assert_generator_process_fresh()

    def test_fresh_generator_passes(self) -> None:
        from autoskillit.workspace._projected_artifact.authority import (
            assert_generator_process_fresh,
        )

        assert_generator_process_fresh()  # should not raise


class TestClaudeCodeDoesNotDisableRepair:
    """T-B5: CLAUDECODE does not disable in-process repair."""

    def test_projection_repair_runs_under_claudecode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.workspace._projected_artifact._hook_repair import (
            PluginHookRepairStatus,
            repair_broken_projection_hooks,
        )
        from autoskillit.workspace._projection_cache import (
            projected_artifact_lease_path,
            projected_artifact_manifest_path,
            projected_plugin_artifact_digest,
        )

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("CLAUDECODE", "1")
        projections_root = tmp_path / ".autoskillit" / "plugin-projections"
        proj = projections_root / "claudecode-test"
        hooks_dir = proj / "hooks"
        hooks_dir.mkdir(parents=True)
        (hooks_dir / "_dispatch.py").write_text("# dispatcher\n")
        stale_hooks = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Read",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "python3 /deleted/hooks/_dispatch.py foo",
                            }
                        ],
                    }
                ]
            }
        }
        (hooks_dir / "hooks.json").write_text(json.dumps(stale_hooks, indent=2) + "\n")
        manifest_path = projected_artifact_manifest_path(proj)
        initial_digest = projected_plugin_artifact_digest(proj)
        manifest_data = {
            "schema_version": 2,
            "artifact_kind": "projection",
            "projection_version": 2,
            "semantic_key": "claudecode-test",
            "incarnation_id": "test",
            "artifact_digest": initial_digest,
            "skills": {},
        }
        manifest_path.write_text(json.dumps(manifest_data, indent=2) + "\n")
        lease_path = projected_artifact_lease_path(proj)
        lease_path.parent.mkdir(parents=True, exist_ok=True)

        outcomes = repair_broken_projection_hooks(projections_root)
        assert len(outcomes) == 1
        assert outcomes[0].status is PluginHookRepairStatus.REPAIRED
