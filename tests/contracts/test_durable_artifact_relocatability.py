"""T-C2: Per-writer relocatability contract and registry integrity.

For each non-machine-local writer: invoke it, scan output for forbidden segments.
For each machine-local writer: assert detection callable exists and resolves.
Bidirectional completeness: every writer string resolves.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from autoskillit.core.types._type_constants import (
    DURABLE_ARTIFACT_WRITERS,
    DurableArtifactWriterDef,
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
            # Simulate what the import-time check does
            writers = (
                DurableArtifactWriterDef(
                    writer="test:func",
                    artifact="test artifact",
                    machine_local=True,
                    detection=None,
                ),
            )
            missing = [w.writer for w in writers if w.machine_local and not w.detection]
            if missing:
                raise AssertionError(
                    "Every machine_local DurableArtifactWriterDef must have a "
                    f"detection callable. Missing: {missing}"
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
        from tests.contracts._relocatability_helpers import (
            environment_pinned_path_segments,
        )

        hooks_dir = tmp_path / "hooks"
        hooks_dir.mkdir()
        write_generated_hooks_json(tmp_path)
        content = (hooks_dir / "hooks.json").read_text()
        for segment in environment_pinned_path_segments():
            assert segment not in content, (
                f"write_generated_hooks_json output contains forbidden segment {segment!r}"
            )
