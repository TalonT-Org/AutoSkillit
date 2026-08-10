"""T-C2: Per-writer relocatability contract and registry integrity.

For each non-machine-local writer: invoke it, scan output for forbidden segments.
For each machine-local writer: assert detection callable exists and resolves.
Bidirectional completeness: every writer string resolves.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from autoskillit.core.types._type_constants import (
    DURABLE_ARTIFACT_WRITERS,
    DurableArtifactWriterDef,
    _validate_durable_artifact_writer_defs,
)

pytestmark = [pytest.mark.layer("contracts"), pytest.mark.medium]


def _resolve(dotted: str) -> object:
    """Resolve ``module:qualname`` to the actual object."""
    module_path, qualname = dotted.rsplit(":", 1)
    mod = importlib.import_module(module_path)
    obj = mod
    for attr in qualname.split("."):
        obj = getattr(obj, attr)
    return obj


def _assert_relocatable(content: str) -> None:
    from tests.contracts._relocatability_helpers import environment_pinned_path_segments

    for segment in environment_pinned_path_segments():
        assert segment not in content, f"durable output contains forbidden segment {segment!r}"


class TestRegistryIntegrity:
    """Every registry entry resolves and obeys its machine-local contract."""

    def test_every_writer_string_resolves(self) -> None:
        for entry in DURABLE_ARTIFACT_WRITERS:
            try:
                _resolve(entry.writer)
            except (ImportError, AttributeError) as exc:
                pytest.fail(
                    f"DURABLE_ARTIFACT_WRITERS entry {entry.writer!r} does not resolve: {exc}"
                )

    def test_every_machine_local_writer_has_resolvable_detection(self) -> None:
        for entry in DURABLE_ARTIFACT_WRITERS:
            if not entry.machine_local:
                continue
            assert entry.detection is not None, (
                f"machine_local writer {entry.writer!r} has no detection callable"
            )
            try:
                obj = _resolve(entry.detection)
            except (ImportError, AttributeError) as exc:
                pytest.fail(
                    f"detection {entry.detection!r} for writer "
                    f"{entry.writer!r} does not resolve: {exc}"
                )
            assert callable(obj), (
                f"detection {entry.detection!r} resolves to {type(obj).__name__}, not a callable"
            )

    def test_import_time_assertion_rejects_machine_local_without_detection(
        self,
    ) -> None:
        """The uncircumventable layer works even under test filtering."""
        with pytest.raises(AssertionError, match="machine_local"):
            _validate_durable_artifact_writer_defs(
                (
                    DurableArtifactWriterDef(
                        writer="test:func",
                        artifact="test artifact",
                        machine_local=True,
                        detection=None,
                    ),
                )
            )

    def test_no_duplicate_writer_strings(self) -> None:
        writers = [w.writer for w in DURABLE_ARTIFACT_WRITERS]
        assert len(writers) == len(set(writers)), (
            "DURABLE_ARTIFACT_WRITERS contains duplicate writer strings"
        )


class TestNonMachineLocalWritersAreRelocatable:
    """Non-machine-local writers must produce output free of environment-pinned segments."""

    def test_write_generated_hooks_json_output_is_relocatable(self, tmp_path: Path) -> None:
        from autoskillit.workspace._projected_artifact.materialization import (
            write_generated_hooks_json,
        )

        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        write_generated_hooks_json(tmp_path)
        _assert_relocatable((hooks_dir / "hooks.json").read_text())

    def test_startup_drift_check_output_is_relocatable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.server import _lifespan

        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        monkeypatch.setattr(_lifespan._core_paths, "pkg_root", lambda: tmp_path)

        _lifespan.run_startup_drift_check()

        _assert_relocatable((hooks_dir / "hooks.json").read_text())

    def test_plugin_cache_repair_output_is_relocatable(self, tmp_path: Path) -> None:
        from autoskillit.core import _AUTOSKILLIT_PLUGIN_KEY, installed_plugin_semantic_key
        from autoskillit.workspace._installed_artifact import (
            write_installed_plugin_artifact_manifest_locked,
        )
        from autoskillit.workspace._projected_artifact._hook_repair import (
            PluginHookRepairStatus,
            repair_broken_plugin_cache_hooks,
        )

        cache_dir = tmp_path / "cache"
        incarnation = cache_dir / "1.2.3"
        hooks_dir = incarnation / "hooks"
        hooks_dir.mkdir(parents=True)
        (hooks_dir / "_dispatch.py").write_text("# dispatcher\n")
        (hooks_dir / "hooks.json").write_text(
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
                                            "python3 /deleted/venv/hooks/_dispatch.py "
                                            "guards/tool_guard"
                                        ),
                                    }
                                ],
                            }
                        ]
                    }
                }
            )
        )
        write_installed_plugin_artifact_manifest_locked(
            incarnation,
            semantic_key=installed_plugin_semantic_key(_AUTOSKILLIT_PLUGIN_KEY, "1.2.3"),
            action="test",
        )

        outcomes = repair_broken_plugin_cache_hooks(cache_dir)

        assert outcomes[0].status is PluginHookRepairStatus.REPAIRED
        _assert_relocatable((hooks_dir / "hooks.json").read_text())

    def test_projection_repair_outputs_are_relocatable(self, tmp_path: Path) -> None:
        from autoskillit.workspace._projected_artifact._hook_repair import (
            PluginHookRepairStatus,
            repair_broken_projection_hooks,
        )
        from autoskillit.workspace._projection_cache import (
            projected_artifact_manifest_path,
            projected_plugin_artifact_digest,
        )

        projections_root = tmp_path / "projections"
        projection = projections_root / "deadbeefcafe0123"
        hooks_dir = projection / "hooks"
        hooks_dir.mkdir(parents=True)
        (hooks_dir / "_dispatch.py").write_text("# dispatcher\n")
        (hooks_dir / "hooks.json").write_text(
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
                                            "python3 /deleted/venv/hooks/_dispatch.py "
                                            "guards/tool_guard"
                                        ),
                                    }
                                ],
                            }
                        ]
                    }
                }
            )
        )
        manifest_path = projected_artifact_manifest_path(projection)
        manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "artifact_kind": "projection",
                    "projection_version": 2,
                    "semantic_key": projection.name,
                    "incarnation_id": "test-incarnation",
                    "artifact_digest": projected_plugin_artifact_digest(projection),
                    "skills": {},
                }
            )
        )

        outcomes = repair_broken_projection_hooks(projections_root)

        assert outcomes[0].status is PluginHookRepairStatus.REPAIRED
        _assert_relocatable((hooks_dir / "hooks.json").read_text())
        _assert_relocatable(manifest_path.read_text())
