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

pytestmark = pytest.mark.small


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
