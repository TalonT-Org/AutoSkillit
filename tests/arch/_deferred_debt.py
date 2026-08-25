"""Shared shape for architectural allowlists that defer a fix behind a tracking
issue rather than exempt it forever. See test_capability_consumption.py and
test_config_consumption.py for the two pre-existing hand-copies this replaces
for any *new* registry of this kind."""

from __future__ import annotations

import dataclasses
from datetime import date
from typing import Any

STALENESS_THRESHOLD_DAYS = 180  # public and unprefixed, unlike the two file-local
# `_STALENESS_THRESHOLD_DAYS` copies this replaces the pattern of — deliberate:
# this one is meant to be imported, theirs were never meant to leave their file.


@dataclasses.dataclass(frozen=True)
class TrackedDeferral:
    issue: int
    rationale: str
    added_date: date


def assert_not_stale(
    registry: dict[Any, TrackedDeferral],
    *,
    registry_name: str,
    threshold_days: int = STALENESS_THRESHOLD_DAYS,
) -> None:
    """Time-bomb: entries older than threshold_days require re-justification —
    either fix the underlying issue and remove the entry, or update added_date
    with a fresh tracking issue."""
    today = date.today()
    stale = [
        f"{key} (issue=#{entry.issue}, added={entry.added_date}, "
        f"age={(today - entry.added_date).days}d)"
        for key, entry in registry.items()
        if (today - entry.added_date).days > threshold_days
    ]
    assert not stale, (
        f"{registry_name} entries older than {threshold_days} days "
        f"(fix and remove, or renew added_date with a fresh tracking issue): {stale}"
    )


_MIN_RATIONALE_LENGTH = 20  # long enough to rule out a bare "TODO"/"fix later"
# placeholder, short enough not to force essay-writing — a sentence naming the
# concrete risk clears this easily; a one-word stub does not.


def assert_rationale_present(
    registry: dict[Any, TrackedDeferral],
    *,
    registry_name: str,
    min_length: int = _MIN_RATIONALE_LENGTH,
) -> None:
    """Reject a deferral whose rationale is empty, whitespace-only, or too short
    to describe the actual risk being deferred — a placeholder rationale defeats
    the reason this shape requires one at all."""
    vague = {
        str(key): entry.rationale
        for key, entry in registry.items()
        if len(entry.rationale.strip()) < min_length
    }
    assert not vague, (
        f"{registry_name} entries with a missing or too-short rationale "
        f"(must be >= {min_length} chars describing the concrete risk being deferred): {vague}"
    )
