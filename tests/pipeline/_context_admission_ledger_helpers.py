"""Shared helpers for the context-admission ledger test split (issue #4606).

This module is intentionally named with a leading underscore so pytest does
not collect it as a test module. Each split test file imports `_authority`
and `_GOLDEN_JOURNAL` from here.
"""

from __future__ import annotations

import os
from pathlib import Path

from autoskillit.core import ContextAdmissionStoreAuthority

_GOLDEN_JOURNAL = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "context_admission_journals"
    / "protocol_v1_encoding_v1.json"
)


def _authority(tmp_path: Path) -> ContextAdmissionStoreAuthority:
    """Build a ContextAdmissionStoreAuthority rooted under tmp_path."""
    return ContextAdmissionStoreAuthority(
        database_path=tmp_path / "context-admission" / "ledger.sqlite3",
        expected_owner_id=os.getuid(),
    )
