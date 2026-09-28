"""T-C2: Per-writer relocatability contract and registry integrity.

For each non-machine-local writer: invoke it, scan output for forbidden segments.
For each machine-local writer: assert detection callable exists and resolves.
Bidirectional completeness: every writer string resolves.
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Callable
from pathlib import Path

import pytest

import autoskillit.workspace.session_skills._catalog as _patch_workspace_session_skill_catalog
from autoskillit.core.types.constants._type_constants_durable_writers import (
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

    def test_session_archive_writer_has_exact_machine_local_contract(self) -> None:
        writer = "autoskillit.execution.session_log.session_log:_append_session_archive_rows"
        entries = [entry for entry in DURABLE_ARTIFACT_WRITERS if entry.writer == writer]
        assert len(entries) == 1
        entry = entries[0]
        assert entry.machine_local is True
        assert entry.detection == (
            "autoskillit.execution.session_log.session_index:find_stale_session_archive_references"
        )
        assert callable(_resolve(entry.writer))
        assert callable(_resolve(entry.detection))

    def test_execution_candidate_manifest_writer_is_relocatable(self, tmp_path: Path) -> None:
        from autoskillit.core.types.results._type_results_execution import ExecutionSelection
        from autoskillit.execution.session_log.session_log import (
            write_execution_candidate_manifest,
        )

        writer = (
            "autoskillit.execution.session_log._session_log_retention:"
            "write_execution_candidate_manifest_at_root"
        )
        entries = [entry for entry in DURABLE_ARTIFACT_WRITERS if entry.writer == writer]
        assert len(entries) == 1
        assert entries[0].machine_local is False
        assert entries[0].detection is None

        selection = ExecutionSelection(selection_id="selection-1")
        assert (
            write_execution_candidate_manifest(selection, str(tmp_path)) == selection.manifest_ref
        )
        content = (tmp_path / selection.manifest_ref).read_text(encoding="utf-8")
        assert json.loads(content) == selection.to_payload()
        _assert_relocatable(content)

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

    def test_report_index_writer_is_relocatable(self, tmp_path: Path) -> None:
        from autoskillit.execution import update_report_index

        writer = "autoskillit.execution.report_index:_RowAppender.commit"
        entries = [entry for entry in DURABLE_ARTIFACT_WRITERS if entry.writer == writer]
        assert len(entries) == 1
        assert callable(_resolve(entries[0].writer))
        assert entries[0].machine_local is False
        assert entries[0].detection is None

        log_root = tmp_path / "logs"
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text(
            json.dumps(
                {
                    "type": "assistant",
                    "requestId": "turn-1",
                    "message": {"content": [{"type": "tool_use", "name": "Read"}]},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        session = {
            "dir_name": "session-1",
            "session_id": "sid-1",
            "backend": "claude-code",
            "provider_used": "anthropic",
            "timestamp": "2020-01-01T00:00:00Z",
            "cwd": str(tmp_path),
            "claude_code_log": str(transcript),
        }
        log_root.mkdir(parents=True, exist_ok=True)
        (log_root / "sessions.jsonl").write_text(json.dumps(session) + "\n", encoding="utf-8")
        index_dir = tmp_path / "report-index"

        update_report_index(log_root, index_dir)

        _assert_relocatable((index_dir / "rows.jsonl").read_text(encoding="utf-8"))
        _assert_relocatable((index_dir / "state.json").read_text(encoding="utf-8"))

    def test_skill_unavailability_metadata_output_is_relocatable(
        self,
        tmp_path: Path,
    ) -> None:
        from autoskillit.core import SkillUnavailabilityPayload
        from autoskillit.workspace import write_skill_unavailability_metadata

        payload: SkillUnavailabilityPayload = {
            "backend": "codex",
            "unavailable": (
                {
                    "skill": "join-dependent",
                    "backend": "codex",
                    "operation": "required_join",
                    "diagnostic": "fixed join unavailable",
                },
            ),
        }
        add_dir = tmp_path / "add-dir"
        add_dir.mkdir()

        write_skill_unavailability_metadata(add_dir, unavailability_payload=payload)

        content = (add_dir / "skill-unavailability.json").read_text(encoding="utf-8")
        assert set(payload) == {"backend", "unavailable"}
        assert json.loads(content) == {
            "backend": "codex",
            "schema_version": 1,
            "unavailable": [
                {
                    "backend": "codex",
                    "diagnostic": "fixed join unavailable",
                    "operation": "required_join",
                    "skill": "join-dependent",
                }
            ],
        }
        _assert_relocatable(content)

    def test_skill_unavailability_writer_preserves_canonical_payload_identity(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from autoskillit.core import SkillUnavailabilityPayload
        from autoskillit.workspace import write_skill_unavailability_metadata

        payload: SkillUnavailabilityPayload = {
            "backend": "codex",
            "unavailable": (),
        }
        captured: dict[str, object] = {}

        def capture_versioned_json(
            path: Path,
            content: object,
            *,
            schema_version: int,
        ) -> None:
            captured.update(path=path, content=content, schema_version=schema_version)

        monkeypatch.setattr(
            _patch_workspace_session_skill_catalog,
            "write_versioned_json",
            capture_versioned_json,
        )

        write_skill_unavailability_metadata(
            tmp_path,
            unavailability_payload=payload,
        )

        assert captured["content"] is payload
        assert captured["schema_version"] == 1

    def test_codex_reconciliation_audit_output_is_relocatable(self, tmp_path: Path) -> None:
        from autoskillit.execution.backends._codex_fs_atomic import (
            _write_reconciliation_audit,
        )

        audit_root = tmp_path / "audits"
        audit_root.mkdir()
        audit_path = audit_root / "0123456789abcdef-1.json"
        _write_reconciliation_audit(
            audit_path,
            {
                "schema_version": 1,
                "view_id": "0123456789abcdef-1",
                "recorded_at": "2026-08-11T00:00:00+00:00",
                "reason": "operator reviewed",
                "manifest_sha256": "0" * 64,
            },
        )

        original = audit_path.read_bytes()
        _assert_relocatable(original.decode())
        with pytest.raises(FileExistsError, match="already exists"):
            _write_reconciliation_audit(audit_path, {"replacement": True})
        assert audit_path.read_bytes() == original

    def test_startup_drift_check_output_is_relocatable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from autoskillit.server.lifecycle import _lifespan

        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        monkeypatch.setattr(_lifespan._core_paths, "pkg_root", lambda: tmp_path)

        _lifespan.run_startup_drift_check()

        _assert_relocatable((hooks_dir / "hooks.json").read_text())

    def test_plugin_cache_repair_output_is_relocatable(self, tmp_path: Path) -> None:
        from autoskillit.core import (
            _AUTOSKILLIT_PLUGIN_KEY,
            installed_plugin_semantic_key,
            managed_home_for,
        )
        from autoskillit.workspace._installed._artifact import (
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
            action="repair",
        )

        outcomes = repair_broken_plugin_cache_hooks(cache_dir, home=managed_home_for(tmp_path))

        assert outcomes[0].status is PluginHookRepairStatus.REPAIRED
        _assert_relocatable((hooks_dir / "hooks.json").read_text())

    def test_projection_repair_outputs_are_relocatable(self, tmp_path: Path) -> None:
        from autoskillit.core import managed_home_for
        from autoskillit.workspace._installed._projection_cache import (
            PROJECTION_ARTIFACT_MANIFEST_SCHEMA_VERSION,
            projected_artifact_manifest_path,
            projected_plugin_artifact_digest,
        )
        from autoskillit.workspace._projected_artifact._hook_repair import (
            PluginHookRepairStatus,
            repair_broken_projection_hooks,
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
                    "schema_version": PROJECTION_ARTIFACT_MANIFEST_SCHEMA_VERSION,
                    "artifact_kind": "projection",
                    "projection_version": 2,
                    "semantic_key": projection.name,
                    "incarnation_id": "test-incarnation",
                    "artifact_digest": projected_plugin_artifact_digest(projection),
                    "skills": {},
                }
            )
        )

        outcomes = repair_broken_projection_hooks(
            projections_root, home=managed_home_for(tmp_path)
        )

        assert outcomes[0].status is PluginHookRepairStatus.REPAIRED
        _assert_relocatable((hooks_dir / "hooks.json").read_text())
        _assert_relocatable(manifest_path.read_text())


def _proof_codex_hooks(tmp_path: Path) -> None:
    from autoskillit.execution.backends._codex_hooks import (
        find_broken_codex_hook_commands,
        sync_managed_codex_hooks_to_config,
    )
    from tests.fixtures.hook_topology import projection_shaped_hook_root

    home = tmp_path / "codex-hooks-proof-home"
    home.mkdir()
    root = projection_shaped_hook_root(home)
    config_path = tmp_path / "codex-hooks-proof-config.toml"
    sync_managed_codex_hooks_to_config(config_path, route="leaf", plugin_dir=root.plugin_dir)

    dispatcher = root.hooks_dir / "_dispatch.py"
    dispatcher.unlink()

    broken = find_broken_codex_hook_commands(config_path)
    assert broken
    assert all(str(dispatcher) in command for command in broken)


def _proof_claude_hooks(tmp_path: Path) -> None:
    import shutil

    import autoskillit.cli._hooks as _hooks_mod
    import autoskillit.cli._init_helpers as init_helpers
    from autoskillit.core import pkg_root
    from autoskillit.hook_registry import find_broken_hook_scripts

    copied_root = tmp_path / "claude-hooks-proof-pkg"
    shutil.copytree(
        pkg_root(),
        copied_root,
        symlinks=False,
        ignore=shutil.ignore_patterns("__pycache__", "*.py[co]"),
    )
    settings_path = tmp_path / "claude-hooks-proof-settings.json"

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(_hooks_mod, "pkg_root", lambda: copied_root)
        mp.setattr(init_helpers, "_is_plugin_installed", lambda **kwargs: False)
        _hooks_mod.sync_hooks_to_settings(settings_path)

    (copied_root / "hooks" / "_dispatch.py").unlink()

    assert find_broken_hook_scripts(settings_path)


def _proof_session_archive(tmp_path: Path) -> None:
    from autoskillit.execution.session_log import session_log as _session_log_mod
    from autoskillit.execution.session_log.session_index import (
        find_stale_session_archive_references,
    )

    archive_root = tmp_path / "session-archive-proof"
    archive_root.mkdir()
    referenced = archive_root / "vanished-cwd"
    referenced.mkdir()
    archive_path = archive_root / "sessions-archive.jsonl"

    _session_log_mod._append_session_archive_rows(archive_path, [{"cwd": str(referenced)}])
    referenced.rmdir()

    assert find_stale_session_archive_references(archive_root) == [str(referenced)]


def _proof_workspace_outcomes(tmp_path: Path) -> None:
    from autoskillit.core import WorkspaceOutcomeKind, WorkspaceOutcomeRecord
    from autoskillit.pipeline.workspace_outcomes._ledger import (
        DefaultWorkspaceOutcomeLedger,
        find_stale_workspace_outcome_shards,
    )

    workspace = tmp_path / "workspace-outcome-proof"
    workspace.mkdir()
    ledger = DefaultWorkspaceOutcomeLedger(tmp_path / "workspace-outcome-ledger")
    ledger.record(
        WorkspaceOutcomeRecord(
            workspace=str(workspace),
            recorded_at="2026-09-16T10:00:00+00:00",
            kind=WorkspaceOutcomeKind.TEST_RUN,
            succeeded=True,
        )
    )
    _, shard_path, _ = ledger._paths(str(workspace))

    workspace.rmdir()

    assert find_stale_workspace_outcome_shards(ledger.root) == (shard_path,)


_DETECTION_PROOFS: dict[str, Callable[[Path], None]] = {
    "autoskillit.execution.backends._codex_hooks:find_broken_codex_hook_commands": (
        _proof_codex_hooks
    ),
    "autoskillit.hook_registry:find_broken_hook_scripts": _proof_claude_hooks,
    "autoskillit.execution.session_log.session_index:find_stale_session_archive_references": (
        _proof_session_archive
    ),
    "autoskillit.pipeline.workspace_outcomes._ledger:find_stale_workspace_outcome_shards": (
        _proof_workspace_outcomes
    ),
}


def test_every_declared_detection_detects_its_writers_breakage(tmp_path: Path) -> None:
    """Every distinct non-None detection actually detects its writers' breakage.

    ``test_every_machine_local_writer_has_resolvable_detection`` above only
    asserts import-time resolvability; this proves each detection functions.
    """
    declared_detections = {
        entry.detection for entry in DURABLE_ARTIFACT_WRITERS if entry.detection is not None
    }
    assert declared_detections == set(_DETECTION_PROOFS), (
        "every distinct non-None DurableArtifactWriterDef.detection needs a proof "
        "in _DETECTION_PROOFS"
    )
    for detection, proof in _DETECTION_PROOFS.items():
        assert _resolve(detection) is not None
        proof(tmp_path)
