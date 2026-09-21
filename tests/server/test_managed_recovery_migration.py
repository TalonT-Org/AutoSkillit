"""Migration boundary coverage for ``read_managed_recovery_state``.

The recovery schema was bumped from v2 to v3 to rename ``request_session_id``
to ``parent_session_id``. v2 debts are no longer silently readable; the
reader must surface an explicit failure so that operators know an
in-flight debt was dropped.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


_V2_RECOVERY = {
    "schema_version": 2,
    "debt": [
        {
            "owner": ("a", "b", "c"),
            "permit_id": "permit-1",
            "flag_dir": "/tmp/flag",
            "request_session_id": "request-1",
            "managed_parent_id": "managed-1",
            "batch_id": "batch-1",
            "assignment_id": "assign-1",
            "attempt_id": "attempt-1",
            "run_id": "run-1",
        }
    ],
    "unadmitted_settlement_debt": [],
}


def test_v2_recovery_state_is_rejected_at_read_boundary(tmp_path: Path) -> None:
    from autoskillit.core import SkillContractError
    from autoskillit.server.tools.tools_execution._managed_fixed_batch_recovery import (
        read_managed_recovery_state,
    )

    state_path = tmp_path / "managed_recovery.json"
    state_path.write_text(json.dumps(_V2_RECOVERY), encoding="utf-8")

    with pytest.raises(SkillContractError, match="managed recovery state is unreadable"):
        read_managed_recovery_state(state_path)


def test_v3_recovery_state_round_trips(tmp_path: Path) -> None:
    from autoskillit.server.tools.tools_execution._managed_fixed_batch_recovery import (
        _RecoveryDebt,
        _UnadmittedSettlementDebt,
        read_managed_recovery_state,
        write_managed_recovery_state,
    )

    state_path = tmp_path / "managed_recovery.json"
    debt = _RecoveryDebt(
        owner=("a", "b", "c"),
        permit_id="permit-1",
        flag_dir="/tmp/flag",
        parent_session_id="parent-1",
        managed_parent_id="managed-1",
        batch_id="batch-1",
        assignment_id="assign-1",
        attempt_id="attempt-1",
        run_id="run-1",
    )
    unadmitted = _UnadmittedSettlementDebt(
        flag_dir="/tmp/flag2",
        batch_id="batch-2",
        assignment_id="assign-2",
        terminal_event_id="event-2",
        terminal_payload_digest="d" * 64,
    )
    write_managed_recovery_state(
        state_path,
        {"permit-1": debt},
        {"assign-2": unadmitted},
    )

    recovery_debt, unadmitted_debt = read_managed_recovery_state(state_path)
    assert "permit-1" in recovery_debt
    assert recovery_debt["permit-1"].parent_session_id == "parent-1"
    assert "assign-2" in unadmitted_debt


def test_corrupt_recovery_state_raises_skill_contract_error(tmp_path: Path) -> None:
    from autoskillit.core import SkillContractError
    from autoskillit.server.tools.tools_execution._managed_fixed_batch_recovery import (
        read_managed_recovery_state,
    )

    state_path = tmp_path / "managed_recovery.json"
    state_path.write_text("{not valid json", encoding="utf-8")

    with pytest.raises(SkillContractError, match="managed recovery state is unreadable"):
        read_managed_recovery_state(state_path)


def test_missing_recovery_state_returns_empty_collections(tmp_path: Path) -> None:
    from autoskillit.server.tools.tools_execution._managed_fixed_batch_recovery import (
        read_managed_recovery_state,
    )

    state_path = tmp_path / "missing.json"

    recovery_debt, unadmitted_debt = read_managed_recovery_state(state_path)
    assert recovery_debt == {}
    assert unadmitted_debt == {}


def test_unsupported_v4_schema_is_rejected(tmp_path: Path) -> None:
    from autoskillit.core import SkillContractError
    from autoskillit.server.tools.tools_execution._managed_fixed_batch_recovery import (
        read_managed_recovery_state,
    )

    state_path = tmp_path / "managed_recovery.json"
    state_path.write_text(json.dumps({**_V2_RECOVERY, "schema_version": 4}), encoding="utf-8")

    with pytest.raises(SkillContractError, match="managed recovery state is unreadable"):
        read_managed_recovery_state(state_path)
