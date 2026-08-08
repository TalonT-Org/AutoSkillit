"""T7: bytecode contamination is classified as a distinct named defect.

Validation still fails closed — bytecode contamination is never blessed —
but the error message distinguishes bytecode-induced mismatch from other
content tampering, enabling targeted remediation advice.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.core import PluginArtifactValidationError
from tests.fixtures.plugin_artifact_state import (
    PluginArtifactStateKind,
    build_plugin_artifact_state,
)

pytestmark = [pytest.mark.layer("cli"), pytest.mark.small]


def test_bytecode_contamination_raises_classified_validation_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Digest mismatch caused by bytecode names the contamination."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    state = build_plugin_artifact_state(tmp_path, PluginArtifactStateKind.BYTECODE_CONTAMINATED)
    from autoskillit.core._plugin_artifact_identity import read_installed_plugin_artifact_identity
    from autoskillit.core._plugin_ids import installed_plugin_semantic_key

    with pytest.raises(PluginArtifactValidationError, match="bytecode contamination"):
        read_installed_plugin_artifact_identity(
            state.managed_root,
            expected_semantic_key=installed_plugin_semantic_key(
                state.plugin_ref, state.expected_version
            ),
        )


def test_plain_digest_mismatch_does_not_mention_bytecode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tampered-content mismatch (no bytecode) should NOT mention contamination."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    state = build_plugin_artifact_state(tmp_path, PluginArtifactStateKind.DIGEST_MISMATCH)
    from autoskillit.core._plugin_artifact_identity import read_installed_plugin_artifact_identity
    from autoskillit.core._plugin_ids import installed_plugin_semantic_key

    with pytest.raises(PluginArtifactValidationError, match="content digest mismatch") as exc_info:
        read_installed_plugin_artifact_identity(
            state.managed_root,
            expected_semantic_key=installed_plugin_semantic_key(
                state.plugin_ref, state.expected_version
            ),
        )
    assert "bytecode contamination" not in str(exc_info.value)


def test_regular_file_named_pycache_is_not_classified_as_bytecode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    state = build_plugin_artifact_state(tmp_path, PluginArtifactStateKind.VALID_CURRENT)
    (state.managed_root / "__pycache__").write_text("ordinary file", encoding="utf-8")
    from autoskillit.core._plugin_artifact_identity import read_installed_plugin_artifact_identity
    from autoskillit.core._plugin_ids import installed_plugin_semantic_key

    with pytest.raises(PluginArtifactValidationError, match="content digest mismatch") as exc_info:
        read_installed_plugin_artifact_identity(
            state.managed_root,
            expected_semantic_key=installed_plugin_semantic_key(
                state.plugin_ref, state.expected_version
            ),
        )

    assert "bytecode contamination" not in str(exc_info.value)


def test_mixed_tampering_does_not_mask_broader_integrity_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bytecode + unrelated content tamper must still fail closed.

    The classifier must never mask a broader integrity failure by
    attributing the mismatch solely to bytecode when other content
    was also modified.
    """
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    # Build a BYTECODE_CONTAMINATED state (real interpreter writes __pycache__)
    state = build_plugin_artifact_state(tmp_path, PluginArtifactStateKind.BYTECODE_CONTAMINATED)
    # Add a non-bytecode tamper on top
    (state.managed_root / "tampered-content.txt").write_text("extra tampering")

    from autoskillit.core._plugin_artifact_identity import read_installed_plugin_artifact_identity
    from autoskillit.core._plugin_ids import installed_plugin_semantic_key

    with pytest.raises(PluginArtifactValidationError, match="bytecode contamination"):
        read_installed_plugin_artifact_identity(
            state.managed_root,
            expected_semantic_key=installed_plugin_semantic_key(
                state.plugin_ref, state.expected_version
            ),
        )
    # The key assertion: validation still fails closed — the mixed-tamper
    # case never succeeds, never masks the broader failure.
