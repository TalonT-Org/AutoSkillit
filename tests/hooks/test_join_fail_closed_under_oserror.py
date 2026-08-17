"""Fail-closed under OSError translation.

Per Plan § Step 7 (REQ-B36), lock-acquisition failure, write failure, and
malformed existing ledger entries must FAIL CLOSED. The write path is
``_join_ledger.py::claim_assignment`` and ``settle_assignment``; they
must translate ``_flock`` / ``_atomic_write_locked`` / ``_read_locked``
failures into ``JoinLedgerError`` so the guard scripts catch them and
exit non-zero with a structured deny payload.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from autoskillit.hooks._join_ledger import (
    JoinLedgerError,
    _CorruptedLedger,
    claim_assignment,
    declare_batch,
    settle_assignment,
)

pytestmark = [pytest.mark.layer("hooks"), pytest.mark.small]


def test_claim_translates_oserror_to_joinledgererror(tmp_path: Path) -> None:
    """A flock OSError during claim_assignment becomes JoinLedgerError."""
    flag_dir = tmp_path
    declare_batch(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        skill_name="skill",
        artifact_digest="abc",
        assignments=("a1", "a2"),
    )
    with patch(
        "autoskillit.hooks._join_ledger.fcntl.flock",
        side_effect=OSError("synthetic contention"),
    ):
        with pytest.raises(JoinLedgerError, match="IO error"):
            claim_assignment(
                flag_dir,
                session_id="s1",
                top_level_parent="p1",
                tool_use_id="t1",
            )


def test_settle_translates_oserror_to_joinledgererror(tmp_path: Path) -> None:
    """A write OSError during settle_assignment becomes JoinLedgerError."""
    flag_dir = tmp_path
    declare_batch(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        skill_name="skill",
        artifact_digest="abc",
        assignments=("a1",),
    )
    claim_assignment(flag_dir, session_id="s1", top_level_parent="p1", tool_use_id="t1")
    with patch(
        "autoskillit.hooks._join_ledger.os.replace",
        side_effect=OSError("synthetic disk failure"),
    ):
        with pytest.raises(JoinLedgerError, match="IO error"):
            settle_assignment(
                flag_dir,
                session_id="s1",
                top_level_parent="p1",
                tool_use_id="t1",
                outcome="success",
            )


def test_claim_translates_corrupted_ledger_to_joinledgererror(tmp_path: Path) -> None:
    """A corrupted existing ledger entry fails closed via JoinLedgerError."""
    flag_dir = tmp_path
    # Pre-create a malformed ledger file.
    ledger_path = flag_dir / "join_ledger.json"
    ledger_path.write_text("not valid json", encoding="utf-8")
    # Reading the ledger raises _CorruptedLedger inside the flock context;
    # the wrapper must translate to JoinLedgerError.
    with pytest.raises(JoinLedgerError, match="unreadable"):
        claim_assignment(
            flag_dir,
            session_id="s1",
            top_level_parent="p1",
            tool_use_id="t1",
        )


def test_settle_translates_corrupted_ledger_to_joinledgererror(tmp_path: Path) -> None:
    """A corrupted existing ledger entry fails closed on settle too."""
    flag_dir = tmp_path
    declare_batch(
        flag_dir,
        session_id="s1",
        top_level_parent="p1",
        skill_name="skill",
        artifact_digest="abc",
        assignments=("a1",),
    )
    claim_assignment(flag_dir, session_id="s1", top_level_parent="p1", tool_use_id="t1")
    # Corrupt the ledger between claim and settle.
    ledger_path = flag_dir / "join_ledger.json"
    ledger_path.write_text("{not valid", encoding="utf-8")
    with pytest.raises(JoinLedgerError, match="unreadable"):
        settle_assignment(
            flag_dir,
            session_id="s1",
            top_level_parent="p1",
            tool_use_id="t1",
            outcome="success",
        )


def test_active_batch_remains_safe_under_corruption(tmp_path: Path) -> None:
    """The read path remains safe — active_batch returns a _corrupted envelope."""
    flag_dir = tmp_path
    ledger_path = flag_dir / "join_ledger.json"
    ledger_path.write_text("garbage", encoding="utf-8")
    from autoskillit.hooks._join_ledger import active_batch

    batch = active_batch(flag_dir, session_id="s1", top_level_parent="p1")
    assert batch is not None
    assert batch.get("_corrupted") is True
    assert "not valid" in (batch.get("error") or "")


def test_ledger_unreadable_propagates_as_corrupted_envelope(tmp_path: Path) -> None:
    """Sanity: the _CorruptedLedger exception class is what the wrappers translate."""
    flag_dir = tmp_path
    ledger_path = flag_dir / "join_ledger.json"
    ledger_path.write_text(json.dumps({"sessions": "not-a-dict"}), encoding="utf-8")
    with pytest.raises(_CorruptedLedger):
        from autoskillit.hooks._join_ledger import _read_locked

        _read_locked(ledger_path)
