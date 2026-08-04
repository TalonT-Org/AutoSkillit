"""Constructor-agnostic deterministic primitives for exploration orchestration.

The public exploration contract intentionally owns domain data construction.  This
module only decides ordering, canonical serialization, and closed-world checks so
the graph, router, pagination, and completeness adapters cannot accidentally
create a competing public representation.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Generic, Protocol, TypeVar

KeyT = TypeVar("KeyT")
ItemT = TypeVar("ItemT")
ValueT = TypeVar("ValueT")
OrderKeyT = TypeVar("OrderKeyT", bound="SupportsLessThan")


class SupportsLessThan(Protocol):
    """A value whose instances can serve as deterministic sort keys."""

    def __lt__(self, other: Any, /) -> bool: ...


class DeterministicGraphError(ValueError):
    """Raised when a graph cannot be scheduled under its closed-world contract."""


class CursorValidationError(ValueError):
    """Raised for an invalid, stale, or non-canonical continuation cursor."""


@dataclass(frozen=True, slots=True)
class ScheduledWave(Generic[KeyT]):
    """One deterministic concurrent wave of independent work."""

    items: tuple[KeyT, ...]


def canonical_json(value: object) -> str:
    """Return the unique JSON representation used at identity boundaries."""

    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("value is not canonically serializable") from exc


def stable_digest(value: object) -> str:
    """Digest canonical content rather than process-local object representation."""

    return hashlib.sha256(canonical_json(value).encode("ascii")).hexdigest()


def stable_kahn_waves(
    items: Iterable[ItemT],
    *,
    key: Callable[[ItemT], KeyT],
    dependencies: Callable[[ItemT], Iterable[KeyT]],
    scope: Callable[[ItemT], Iterable[str]],
) -> tuple[ScheduledWave[KeyT], ...]:
    """Schedule a closed dependency graph in stable, scope-disjoint Kahn waves.

    Every listed dependency must be a member of ``items``.  A ready item whose
    scope overlaps an earlier item in the same wave is deferred to the next wave;
    therefore concurrency is both deterministic and safe without treating scope
    conflict as an ordering edge.
    """

    by_key: dict[KeyT, ItemT] = {}
    for item in items:
        item_key = key(item)
        if item_key in by_key:
            raise DeterministicGraphError(f"duplicate graph key: {item_key!r}")
        by_key[item_key] = item

    dependency_sets: dict[KeyT, set[KeyT]] = {}
    dependents: dict[KeyT, set[KeyT]] = defaultdict(set)
    for item_key, item in by_key.items():
        item_dependencies = set(dependencies(item))
        missing = item_dependencies.difference(by_key)
        if missing:
            raise DeterministicGraphError(
                f"unknown dependencies for {item_key!r}: {sorted(missing, key=repr)!r}"
            )
        if item_key in item_dependencies:
            raise DeterministicGraphError(f"self dependency: {item_key!r}")
        dependency_sets[item_key] = item_dependencies
        for dependency in item_dependencies:
            dependents[dependency].add(item_key)

    remaining = {
        item_key: len(item_dependencies) for item_key, item_dependencies in dependency_sets.items()
    }
    ready = {item_key for item_key, count in remaining.items() if count == 0}
    waves: list[ScheduledWave[KeyT]] = []
    scheduled_count = 0

    while ready:
        occupied_scopes: set[str] = set()
        wave: list[KeyT] = []
        for item_key in sorted(ready, key=repr):
            item_scopes = frozenset(scope(by_key[item_key]))
            if occupied_scopes.isdisjoint(item_scopes):
                wave.append(item_key)
                occupied_scopes.update(item_scopes)
        if (
            not wave
        ):  # Defensive: empty scopes must always make at least one ready item schedulable.
            raise DeterministicGraphError("ready graph items cannot be assigned a wave")
        ready.difference_update(wave)
        waves.append(ScheduledWave(tuple(wave)))
        scheduled_count += len(wave)
        for completed in wave:
            for dependent in sorted(dependents[completed], key=repr):
                remaining[dependent] -= 1
                if remaining[dependent] == 0:
                    ready.add(dependent)

    if scheduled_count != len(by_key):
        unresolved = sorted(
            (item_key for item_key, count in remaining.items() if count > 0), key=repr
        )
        raise DeterministicGraphError(f"dependency cycle: {unresolved!r}")
    return tuple(waves)


def stable_group(
    values: Iterable[ValueT],
    *,
    identity: Callable[[ValueT], KeyT],
    order: Callable[[ValueT], OrderKeyT],
) -> tuple[tuple[KeyT, tuple[ValueT, ...]], ...]:
    """Group observations by durable identity without collapsing their evidence."""

    groups: dict[KeyT, list[ValueT]] = defaultdict(list)
    for value in values:
        groups[identity(value)].append(value)
    return tuple(
        (group_key, tuple(sorted(group_values, key=order)))
        for group_key, group_values in sorted(groups.items(), key=lambda pair: repr(pair[0]))
    )


def make_cursor_payload(*, digest: str, offset: int, page_size: int) -> dict[str, object]:
    """Build the complete, digest-bound payload before an adapter encodes it."""

    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise CursorValidationError("cursor digest must be a lowercase SHA-256 hex digest")
    if offset < 0 or page_size <= 0:
        raise CursorValidationError("cursor offset and page size are invalid")
    return {"digest": digest, "offset": offset, "page_size": page_size}


def validate_cursor_payload(
    payload: Mapping[str, object],
    *,
    expected_digest: str,
    expected_page_size: int,
) -> int:
    """Fail closed unless the entire cursor matches this exact result set."""

    expected = {"digest", "offset", "page_size"}
    if set(payload) != expected:
        raise CursorValidationError("cursor fields are not closed-world")
    digest = payload["digest"]
    offset = payload["offset"]
    page_size = payload["page_size"]
    if not isinstance(digest, str) or not isinstance(offset, int) or isinstance(offset, bool):
        raise CursorValidationError("cursor field types are invalid")
    if not isinstance(page_size, int) or isinstance(page_size, bool):
        raise CursorValidationError("cursor field types are invalid")
    if digest != expected_digest or page_size != expected_page_size:
        raise CursorValidationError("cursor is stale for this result set")
    make_cursor_payload(digest=digest, offset=offset, page_size=page_size)
    return offset


def page_slice(
    values: Sequence[ValueT], *, offset: int, page_size: int
) -> tuple[tuple[ValueT, ...], int | None]:
    """Return a page and only the next deterministic offset, if any."""

    if offset < 0 or page_size <= 0 or offset > len(values):
        raise CursorValidationError("page bounds are invalid")
    page = tuple(values[offset : offset + page_size])
    next_offset = offset + len(page)
    return page, next_offset if next_offset < len(values) else None
