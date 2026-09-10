"""Durable recovery-state codec for managed fixed-batch execution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path

from autoskillit.core import SkillContractError, read_versioned_json, write_versioned_json

_RECOVERY_SCHEMA_VERSION = 2


@dataclass(frozen=True, slots=True)
class _RecoveryDebt:
    owner: tuple[str, str, str]
    permit_id: str
    flag_dir: str
    request_session_id: str
    managed_parent_id: str
    batch_id: str
    assignment_id: str
    attempt_id: str
    run_id: str

    def __post_init__(self) -> None:
        if len(self.owner) != 3 or any(
            not isinstance(component, str) or not component for component in self.owner
        ):
            raise ValueError("managed recovery owner must contain three non-empty strings")
        _validate_strings(
            self,
            "managed recovery",
            (
                "permit_id",
                "flag_dir",
                "request_session_id",
                "managed_parent_id",
                "batch_id",
                "assignment_id",
                "attempt_id",
                "run_id",
            ),
        )


@dataclass(frozen=True, slots=True)
class _UnadmittedSettlementDebt:
    flag_dir: str
    batch_id: str
    assignment_id: str
    terminal_event_id: str
    terminal_payload_digest: str

    def __post_init__(self) -> None:
        _validate_strings(
            self,
            "managed unadmitted settlement",
            (
                "flag_dir",
                "batch_id",
                "assignment_id",
                "terminal_event_id",
                "terminal_payload_digest",
            ),
        )


def _validate_strings(value: object, subject: str, field_names: tuple[str, ...]) -> None:
    for field_name in field_names:
        field_value = getattr(value, field_name)
        if not isinstance(field_value, str) or not field_value:
            raise ValueError(f"{subject} {field_name} must be a non-empty string")


def read_managed_recovery_state(
    state_path: Path,
) -> tuple[dict[str, _RecoveryDebt], dict[str, _UnadmittedSettlementDebt]]:
    """Load and validate both kinds of managed recovery debt."""
    if not state_path.exists():
        return {}, {}
    try:
        payload = read_versioned_json(
            state_path,
            _RECOVERY_SCHEMA_VERSION,
            raise_io_errors=True,
        )
        if payload is None:
            raise ValueError("unsupported managed recovery schema")
        recovery_debt = {
            item["permit_id"]: _RecoveryDebt(
                owner=tuple(item["owner"]),
                permit_id=item["permit_id"],
                flag_dir=item["flag_dir"],
                request_session_id=item["request_session_id"],
                managed_parent_id=item["managed_parent_id"],
                batch_id=item["batch_id"],
                assignment_id=item["assignment_id"],
                attempt_id=item["attempt_id"],
                run_id=item["run_id"],
            )
            for item in payload.get("debt", [])
        }
        unadmitted_debt = {
            item["assignment_id"]: _UnadmittedSettlementDebt(
                flag_dir=item["flag_dir"],
                batch_id=item["batch_id"],
                assignment_id=item["assignment_id"],
                terminal_event_id=item["terminal_event_id"],
                terminal_payload_digest=item["terminal_payload_digest"],
            )
            for item in payload.get("unadmitted_settlement_debt", [])
        }
        return recovery_debt, unadmitted_debt
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SkillContractError(f"managed recovery state is unreadable: {exc}") from exc


def write_managed_recovery_state(
    state_path: Path,
    recovery_debt: Mapping[str, _RecoveryDebt],
    unadmitted_debt: Mapping[str, _UnadmittedSettlementDebt],
) -> None:
    """Atomically persist both kinds of managed recovery debt."""
    write_versioned_json(
        state_path,
        {
            "debt": [asdict(item) for item in recovery_debt.values()],
            "unadmitted_settlement_debt": [asdict(item) for item in unadmitted_debt.values()],
        },
        _RECOVERY_SCHEMA_VERSION,
    )
