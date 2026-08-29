"""Stdlib-only authority for cumulative quota constraints."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
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
    """Read and validate the observed-constraints JSON store.

    Returns ``[]`` when the file is absent or unreadable (this is a normal
    first-run / cache-miss state, not an error). Schema mismatches, malformed
    entries, and other integrity problems raise ``ValueError`` so callers can
    distinguish "no data" from "data we cannot trust". Use
    :func:`safe_decode_observed_constraints` in hook and fail-open paths.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(decoded, dict):
        raise ValueError("observed quota constraint store must be a JSON object")
    if decoded.get("schema_version") != OBSERVED_CONSTRAINT_SCHEMA_VERSION:
        raise ValueError("observed quota constraint store schema mismatch")
    items = decoded.get("constraints")
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


def safe_decode_observed_constraints(path: Path) -> list[QuotaConstraint]:
    """Variant of :func:`decode_observed_constraints` that fails-open to ``[]``.

    Treats any schema mismatch, malformed entry, or unexpected exception as an
    empty store. Used by hook paths (which must not crash on a corrupt cache)
    and by the live quota path (which must continue past a stale observed store).
    """
    try:
        return decode_observed_constraints(path)
    except (ValueError, OSError):
        return []


def fold_poll_and_observed_constraints(
    cache_path: str | Path,
    *,
    account_scope: str,
    read_cache: Callable[[str, int], dict | None],
    cache_max_age: int,
    now_epoch: int,
) -> tuple[list[QuotaConstraint], dict[str, object]]:
    """Compute observed constraints plus poll-display metadata.

    Shared between the pre-tool (quota_guard) and post-tool (quota_post_hook)
    hook decision functions, which previously duplicated this 40-line block
    verbatim. Returns ``(constraints, metadata)`` where ``constraints`` is the
    list that should be passed to :func:`effective_quota_block` and
    ``metadata`` carries the poll-cache display fields (utilization, threshold,
    window name, unknown_reset_block flag, cache_state).
    """
    path = observed_constraint_path(cache_path)
    constraints = safe_decode_observed_constraints(path)
    metadata: dict[str, object] = {
        "utilization": 0.0,
        "effective_threshold": 0.0,
        "window_name": "unknown",
        "unknown_reset_block": False,
        "cache_state": "miss",
    }
    cache = read_cache(str(Path(cache_path).expanduser()), cache_max_age)
    if cache is None:
        return constraints, metadata
    binding = cache.get("binding")
    if not isinstance(binding, dict):
        metadata["cache_state"] = "parse_error"
        return constraints, metadata
    try:
        metadata = {
            "utilization": float(binding["utilization"]),
            "effective_threshold": float(binding.get("effective_threshold", 0.0)),
            "window_name": str(binding.get("window_name", "unknown")),
            "unknown_reset_block": False,
            "cache_state": "valid",
        }
        resets_at = binding.get("resets_at")
        if bool(binding.get("should_block", False)):
            if resets_at:
                # ``datetime.fromisoformat`` returns a naive datetime when the
                # input has no timezone — calling ``.timestamp()`` on it then
                # interprets the value in the local timezone, which would
                # shift the computed ``blocked_until_epoch`` by the local UTC
                # offset. Treat naive values as UTC explicitly so the deadline
                # is interpreted consistently regardless of the host timezone.
                parsed_reset = datetime.fromisoformat(str(resets_at))
                if parsed_reset.tzinfo is None:
                    parsed_reset = parsed_reset.replace(tzinfo=UTC)
                constraints.append(
                    QuotaConstraint(
                        source=QuotaEvidenceSource.PROVIDER_POLL,
                        scope=account_scope,
                        blocked_until_epoch=int(parsed_reset.timestamp()),
                        observed_at_epoch=now_epoch,
                        limit_type=str(metadata["window_name"]),
                    )
                )
            else:
                metadata["unknown_reset_block"] = True
    except (KeyError, TypeError, ValueError):
        metadata["cache_state"] = "parse_error"
    return constraints, metadata


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


def decide_quota_block(
    cache_path: str | Path,
    *,
    account_scope: str,
    read_cache: Callable[[str, int], dict | None],
    cache_max_age: int,
    now_epoch: int,
) -> tuple[QuotaConstraint | None, dict[str, object]]:
    """Shared decision body for the pre-tool and post-tool quota hooks.

    Both ``hooks/guards/quota_guard.py`` and ``hooks/quota_post_hook.py`` wrap
    this function so the call site only differs in its deny / warning message
    formatting. ``read_cache`` and the lifetime / scope settings come from the
    caller so the helper stays decoupled from the hook-settings module.
    """
    constraints, metadata = fold_poll_and_observed_constraints(
        cache_path,
        account_scope=account_scope,
        read_cache=read_cache,
        cache_max_age=cache_max_age,
        now_epoch=now_epoch,
    )
    winner = effective_quota_block(
        constraints,
        account_scope=account_scope,
        now_epoch=now_epoch,
    )
    return winner, metadata
