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


def _plant_stale_projection(
    home: Path,
    semantic_key: str,
) -> tuple[Path, Path, Path, Path, Path]:
    from autoskillit.workspace._projection_cache import (
        projected_artifact_lease_path,
        projected_artifact_manifest_path,
        projected_plugin_artifact_digest,
    )

    projections_root = home / ".autoskillit" / "plugin-projections"
    projection = projections_root / semantic_key
    hooks_dir = projection / "hooks"
    hooks_dir.mkdir(parents=True)
    (hooks_dir / "_dispatch.py").write_text("# dispatcher\n")
    hooks_path = hooks_dir / "hooks.json"
    hooks_path.write_text(
        json.dumps(
            {
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
            },
            indent=2,
        )
        + "\n"
    )
    manifest_path = projected_artifact_manifest_path(projection)
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "artifact_kind": "projection",
                "projection_version": 2,
                "semantic_key": semantic_key,
                "incarnation_id": "test-incarnation",
                "artifact_digest": projected_plugin_artifact_digest(projection),
                "skills": {},
            },
            indent=2,
        )
        + "\n"
    )
    lease_path = projected_artifact_lease_path(projection)
    lease_path.parent.mkdir(parents=True, exist_ok=True)
    return projections_root, projection, hooks_path, manifest_path, lease_path


class TestValidateStagedPluginHooks:
    """T-B1 (unit level): validate_staged_plugin_hooks rejects broken commands."""

    @pytest.mark.parametrize(
        "hooks_data",
        [
            [],
            {"hooks": []},
            {"hooks": {"PreToolUse": {}}},
            {"hooks": {"PreToolUse": [[]]}},
            {"hooks": {"PreToolUse": [{"hooks": {}}]}},
            {"hooks": {"PreToolUse": [{"hooks": [[]]}]}},
        ],
    )
    def test_malformed_container_shapes_raise_typed_error(
        self, tmp_path: Path, hooks_data: object
    ) -> None:
        from autoskillit.workspace._projected_artifact._hook_repair import (
            ProjectedArtifactHooksInvalid,
            validate_staged_plugin_hooks,
        )

        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        (hooks_dir / "hooks.json").write_text(json.dumps(hooks_data))

        with pytest.raises(ProjectedArtifactHooksInvalid):
            validate_staged_plugin_hooks(tmp_path)

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

    def test_absolute_command_with_existing_target_is_rejected(self, tmp_path: Path) -> None:
        from autoskillit.workspace._projected_artifact._hook_repair import (
            ProjectedArtifactHooksInvalid,
            validate_staged_plugin_hooks,
        )

        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        dispatcher = hooks_dir / "_dispatch.py"
        dispatcher.write_text("# dispatcher\n")
        # Absolute command pointing at an EXISTING file — must still be rejected
        hooks_data = {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Read",
                        "hooks": [
                            {
                                "type": "command",
                                "command": f"python3 -B {dispatcher} guards/tool_guard",
                            }
                        ],
                    }
                ]
            }
        }
        (hooks_dir / "hooks.json").write_text(json.dumps(hooks_data))
        with pytest.raises(ProjectedArtifactHooksInvalid, match="not relocatable"):
            validate_staged_plugin_hooks(tmp_path)


class TestPublicationGateIntegration:
    """T-B1 integration: a regressed renderer cannot publish."""

    def test_regressed_renderer_refuses_publication(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.core import PluginArtifactPublicationError, PluginLoadMode
        from autoskillit.execution.backends.claude import ClaudeCodeBackend
        from autoskillit.workspace import project_default_plugin_authority
        from tests.contracts._projection_helpers import session_catalog

        monkeypatch.setattr(Path, "home", lambda: tmp_path)

        def broken_render():
            return (
                json.dumps(
                    {
                        "hooks": {
                            "PreToolUse": [
                                {
                                    "matcher": "Read",
                                    "hooks": [
                                        {
                                            "type": "command",
                                            "command": (
                                                "python3 /absolute/hooks/_dispatch.py foo"
                                            ),
                                        }
                                    ],
                                }
                            ]
                        },
                        "_autoskillit_registry_hash": "fake",
                    },
                    indent=2,
                )
                + "\n"
            )

        import autoskillit.workspace._projected_artifact.materialization as _mat

        monkeypatch.setattr(_mat, "render_hooks_json_text", broken_render)

        catalog = session_catalog()
        authority = project_default_plugin_authority(
            cwd=tmp_path,
            base_branch="main",
            catalog=catalog,
        )
        projections_root = tmp_path / ".autoskillit" / "plugin-projections"
        dirs_before = set(projections_root.iterdir()) if projections_root.is_dir() else set()
        with pytest.raises(PluginArtifactPublicationError):
            authority.acquire_launch_binding(
                backend=ClaudeCodeBackend(),
                load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR,
            )
        # No new projection directory was published
        dirs_after = set(projections_root.iterdir()) if projections_root.is_dir() else set()
        new_dirs = {d for d in dirs_after - dirs_before if not d.name.startswith(".")}
        assert not new_dirs, f"broken renderer published a new projection: {new_dirs}"

    def test_self_consistent_broken_reuse_is_refused(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Published hooks rewritten to absolute commands with manifest recomputed
        (broken but self-consistent) must be refused on reuse."""
        from autoskillit.core import PluginLoadMode
        from autoskillit.execution.backends.claude import ClaudeCodeBackend
        from autoskillit.hook_registry import PLUGIN_ROOT_TOKEN
        from autoskillit.workspace import project_default_plugin_authority
        from autoskillit.workspace._projection_cache import (
            projected_artifact_manifest_path,
            projected_plugin_artifact_digest,
        )
        from tests.contracts._projection_helpers import session_catalog

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        backend = ClaudeCodeBackend()
        catalog = session_catalog()

        # Publish a healthy projection
        first = project_default_plugin_authority(
            cwd=tmp_path,
            base_branch="main",
            catalog=catalog,
        ).acquire_launch_binding(backend=backend, load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR)
        first_dir = first.plugin_dir
        assert first_dir is not None
        first.close()

        # Rewrite hooks to absolute commands
        hooks_path = first_dir / "hooks" / "hooks.json"
        hooks = json.loads(hooks_path.read_text())
        for entries in hooks.get("hooks", {}).values():
            for entry in entries:
                for hook in entry.get("hooks", []):
                    hook["command"] = hook["command"].replace(
                        PLUGIN_ROOT_TOKEN, "/absolute/stale/path"
                    )
        hooks_path.write_text(json.dumps(hooks, indent=2) + "\n")

        # Recompute manifest so tree digest agrees (broken but self-consistent)
        manifest_path = projected_artifact_manifest_path(first_dir)
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text())
            manifest["artifact_digest"] = projected_plugin_artifact_digest(first_dir)
            manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

        # Second binding must refuse the broken incarnation and re-stage
        second = project_default_plugin_authority(
            cwd=tmp_path,
            base_branch="main",
            catalog=catalog,
        ).acquire_launch_binding(backend=backend, load_mode=PluginLoadMode.EXPLICIT_PLUGIN_DIR)
        try:
            assert second.plugin_dir is not None
            new_hooks = json.loads((second.plugin_dir / "hooks" / "hooks.json").read_text())
            for entries in new_hooks.get("hooks", {}).values():
                for entry in entries:
                    for hook in entry.get("hooks", []):
                        assert PLUGIN_ROOT_TOKEN in hook["command"]
        finally:
            second.close()


class TestProjectionRepair:
    """T-B2: Startup repair heals a stale projection."""

    def test_startup_repair_heals_a_stale_projection(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.workspace._projected_artifact._hook_repair import (
            PluginHookRepairStatus,
            repair_broken_projection_hooks,
        )
        from autoskillit.workspace._projection_cache import projected_plugin_artifact_digest

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        projections_root, projection, hooks_path, manifest_path, _ = _plant_stale_projection(
            tmp_path, "deadbeefcafe0123"
        )

        outcomes = repair_broken_projection_hooks(projections_root)

        assert len(outcomes) == 1
        assert outcomes[0].status is PluginHookRepairStatus.REPAIRED
        # Verify commands are now relocatable
        repaired = json.loads(hooks_path.read_text())
        for entries in repaired.get("hooks", {}).values():
            for entry in entries:
                for hook in entry.get("hooks", []):
                    assert PLUGIN_ROOT_TOKEN in hook["command"]
        # Verify manifest was updated so digest agrees
        updated_manifest = json.loads(manifest_path.read_text())
        new_digest = projected_plugin_artifact_digest(projection)
        assert updated_manifest["artifact_digest"] == new_digest

    def test_projection_repair_runs_without_cache_broken_or_obligation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T-B2 independence: repair runs even when the plugin cache is healthy
        and no obligation is pending."""
        from autoskillit.server._lifespan import run_startup_hook_health_check
        from autoskillit.workspace._projection_cache import (
            projected_artifact_lease_path,
            projected_artifact_manifest_path,
            projected_plugin_artifact_digest,
        )

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # NO stale cache, NO pending obligation — only a stale projection
        projections_root = tmp_path / ".autoskillit" / "plugin-projections"
        proj = projections_root / "independence-test"
        hooks_dir = proj / "hooks"
        hooks_dir.mkdir(parents=True)
        (hooks_dir / "_dispatch.py").write_text("# dispatcher\n")
        stale = {
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
        (hooks_dir / "hooks.json").write_text(json.dumps(stale, indent=2) + "\n")
        manifest_path = projected_artifact_manifest_path(proj)
        digest = projected_plugin_artifact_digest(proj)
        manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "artifact_kind": "projection",
                    "projection_version": 2,
                    "semantic_key": "independence-test",
                    "incarnation_id": "test",
                    "artifact_digest": digest,
                    "skills": {},
                },
                indent=2,
            )
            + "\n"
        )
        lease_path = projected_artifact_lease_path(proj)
        lease_path.parent.mkdir(parents=True, exist_ok=True)

        run_startup_hook_health_check()

        from autoskillit.hook_registry import PLUGIN_ROOT_TOKEN

        repaired = json.loads((hooks_dir / "hooks.json").read_text())
        for entries in repaired.get("hooks", {}).values():
            for entry in entries:
                for hook in entry.get("hooks", []):
                    assert PLUGIN_ROOT_TOKEN in hook["command"]

    def test_repair_skips_contended_lease(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T-B3: Repair respects the lease — skips without modifying."""
        from autoskillit.core import ArtifactLease
        from autoskillit.workspace._projected_artifact._hook_repair import (
            PluginHookRepairStatus,
            repair_broken_projection_hooks,
        )

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        projections_root, _, hooks_path, _, lease_path = _plant_stale_projection(
            tmp_path, "contended-key"
        )
        original_text = hooks_path.read_text()

        # Hold an exclusive lease to simulate contention
        with ArtifactLease.acquire_exclusive(lease_path, blocking=False):
            outcomes = repair_broken_projection_hooks(projections_root)

        assert len(outcomes) == 1
        assert outcomes[0].status is PluginHookRepairStatus.CONTENDED
        # File must be unchanged
        assert hooks_path.read_text() == original_text

    def test_repair_succeeds_after_contention_clears(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T-B3 post-contention: a subsequent uncontended run repairs it."""
        from autoskillit.core import ArtifactLease
        from autoskillit.hook_registry import PLUGIN_ROOT_TOKEN
        from autoskillit.workspace._projected_artifact._hook_repair import (
            PluginHookRepairStatus,
            repair_broken_projection_hooks,
        )

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        projections_root, _, hooks_path, _, lease_path = _plant_stale_projection(
            tmp_path, "post-contention"
        )

        # First run — contended
        with ArtifactLease.acquire_exclusive(lease_path, blocking=False):
            outcomes = repair_broken_projection_hooks(projections_root)
        assert outcomes[0].status is PluginHookRepairStatus.CONTENDED

        # Second run — uncontended, should repair
        outcomes = repair_broken_projection_hooks(projections_root)
        assert len(outcomes) == 1
        assert outcomes[0].status is PluginHookRepairStatus.REPAIRED
        repaired = json.loads(hooks_path.read_text())
        for entries in repaired.get("hooks", {}).values():
            for entry in entries:
                for hook in entry.get("hooks", []):
                    assert PLUGIN_ROOT_TOKEN in hook["command"]


class TestStaleGeneratorRefusal:
    """T-B4: Stale generator refuses to bind."""

    def test_deleted_pkg_root_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import autoskillit.workspace._projected_artifact.authority as _auth
        from autoskillit.workspace._projected_artifact.authority import (
            StaleGeneratorError,
            assert_generator_process_fresh,
        )

        # Monkeypatch pkg_root where assert_generator_process_fresh imports it
        monkeypatch.setattr(_auth, "pkg_root", lambda: tmp_path / "nonexistent")
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
        # Non-vacuity: the in-process version is NOT the mocked value,
        # proving the probe reads the filesystem, not an import-time cache.
        import autoskillit

        assert autoskillit.__version__ != "0.0.0-changed", (
            "in-process version equals the mock — probe cannot distinguish disk from cache"
        )
        with pytest.raises(StaleGeneratorError, match="upgraded under this process"):
            assert_generator_process_fresh()

    def test_fresh_generator_passes(self) -> None:
        from autoskillit.workspace._projected_artifact.authority import (
            assert_generator_process_fresh,
        )

        assert_generator_process_fresh()  # should not raise

    def test_installed_authority_also_refuses_stale_generator(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """T-B4 case 4: InstalledPluginArtifactAuthority refuses identically."""
        from typing import Any, cast

        import autoskillit.workspace._projected_artifact.authority as _auth
        from autoskillit.cli._plugin_artifact import InstalledPluginArtifactAuthority
        from autoskillit.workspace._projected_artifact.authority import StaleGeneratorError

        monkeypatch.setattr(_auth, "pkg_root", lambda: tmp_path / "nonexistent")
        authority = InstalledPluginArtifactAuthority(
            tmp_path / "fake-root", semantic_key="fake-semantic-key"
        )
        with pytest.raises(StaleGeneratorError, match="no longer exists"):
            authority.acquire_launch_binding(
                backend=cast(Any, None),
                load_mode=cast(Any, None),  # will fail before reaching load_mode use
            )


class TestClaudeCodeDoesNotDisableRepair:
    """T-B5: CLAUDECODE does not disable in-process repair."""

    def test_projection_repair_runs_under_claudecode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.workspace._projected_artifact._hook_repair import (
            PluginHookRepairStatus,
            repair_broken_projection_hooks,
        )

        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("CLAUDECODE", "1")
        projections_root, _, _, _, _ = _plant_stale_projection(tmp_path, "claudecode-test")

        outcomes = repair_broken_projection_hooks(projections_root)
        assert len(outcomes) == 1
        assert outcomes[0].status is PluginHookRepairStatus.REPAIRED
