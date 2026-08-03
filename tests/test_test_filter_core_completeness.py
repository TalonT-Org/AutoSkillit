"""REQ-FILT-007: every core module is classified by the test-filter cascade."""

from pathlib import Path

import pytest

from tests._test_filter import _CORE_UNIVERSAL_MODULES, MODULE_CASCADE_CORE

pytestmark = [pytest.mark.medium]


def test_all_core_stems_classified() -> None:
    core_root = Path("src/autoskillit/core")
    actual_stems = {p.stem for p in core_root.rglob("*.py") if p.stem != "__init__"}
    assert actual_stems, (
        f"No .py files found under {core_root} — is pytest running from the project root?"
    )
    classified = set(_CORE_UNIVERSAL_MODULES) | set(MODULE_CASCADE_CORE)
    unclassified = actual_stems - classified
    assert not unclassified, (
        "Unclassified core stems (will fall through to full 18-dir cascade): "
        f"{sorted(unclassified)}"
    )


def test_launch_projection_cascade_matches_launch_authority() -> None:
    launch_cascade = MODULE_CASCADE_CORE["_type_launch"]
    assert MODULE_CASCADE_CORE["_type_launch_projection"] == launch_cascade
