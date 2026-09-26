"""The documented update failure exit code must match the enum, not a frozen literal."""

from __future__ import annotations

from pathlib import Path

import pytest

from autoskillit.cli.update._transaction import UpdateProcessStatus

pytestmark = [pytest.mark.layer("docs"), pytest.mark.small]

DOCS_ROOT = Path(__file__).resolve().parents[2] / "docs"


def test_update_checks_doc_states_the_enum_exit_code() -> None:
    text = (DOCS_ROOT / "update-checks.md").read_text(encoding="utf-8")
    assert f"exits with code {int(UpdateProcessStatus.FAILED_UPGRADE)}" in text
    assert "local-path" in text
