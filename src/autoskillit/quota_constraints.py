"""Stdlib-only authority for cumulative quota constraints."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from pathlib import Path

OBSERVED_CONSTRAINT_SCHEMA_VERSION = 1


class QuotaEvidenceSource(StrEnum):
    PROVIDER_POLL = "provider_poll"
    OBSERVED_TERMINAL = "observed_terminal"


@dataclass(frozen=True, slots=True)
class QuotaConstraint:
    source: QuotaEvidenceSource
    scope: str
    blocked_until_epoch: int
    observed_at_epoch: int
    limit_type: str = ""

    def to_dict(self) -> dict[str, str | int]:
        return {
            "source": self.source.value,
            "scope": self.scope,
            "blocked_until_epoch": self.blocked_until_epoch,
            "observed_at_epoch": self.observed_at_epoch,
            "limit_type": self.limit_type,
        }


def quota_scope(provider: str, credentials_path: Path) -> str:
    digest = sha256(str(credentials_path).encode()).hexdigest()[:16]
    return f"{provider}:{digest}"


def observed_constraint_path(cache_path: str | Path) -> Path:
    path = Path(cache_path).expanduser()
    return path.with_name(f"{path.name}.observed-constraints.json")


def decode_observed_constraints(path: Path) -> list[QuotaConstraint]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    if not isinstance(raw, dict):
        raise ValueError("observed quota constraint store must be a JSON object")
    if raw.get("schema_version") != OBSERVED_CONSTRAINT_SCHEMA_VERSION:
        raise ValueError("observed quota constraint store schema mismatch")
    items = raw.get("constraints")
    if not isinstance(items, list):
        raise ValueError("observed quota constraint store requires constraints list")
    constraints: list[QuotaConstraint] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("observed quota constraint entry must be an object")
        try:
            constraint = QuotaConstraint(
                source=QuotaEvidenceSource(item["source"]),
                scope=str(item["scope"]),
                blocked_until_epoch=int(item["blocked_until_epoch"]),
                observed_at_epoch=int(item["observed_at_epoch"]),
                limit_type=str(item.get("limit_type", "")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid observed quota constraint entry") from exc
        constraints.append(constraint)
    return constraints


def effective_quota_block(
    constraints: Iterable[QuotaConstraint], *, account_scope: str, now_epoch: int
) -> QuotaConstraint | None:
    """Return the live account constraint with the latest reset, or None."""
    live = (
        constraint
        for constraint in constraints
        if constraint.scope == account_scope and constraint.blocked_until_epoch > now_epoch
    )
    return max(live, key=lambda constraint: constraint.blocked_until_epoch, default=None)
