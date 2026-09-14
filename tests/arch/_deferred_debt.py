"""Shared shape for architectural allowlists that defer a fix behind a tracking
issue rather than exempt it forever. See test_capability_consumption.py and
test_config_consumption.py for the two pre-existing hand-copies this replaces
for any *new* registry of this kind."""

from __future__ import annotations

import ast
import dataclasses
import functools
from collections.abc import Collection
from datetime import date
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).parent.parent.parent

STALENESS_THRESHOLD_DAYS = 180  # public and unprefixed, unlike the two file-local
# `_STALENESS_THRESHOLD_DAYS` copies this replaces the pattern of — deliberate:
# this one is meant to be imported, theirs were never meant to leave their file.


@dataclasses.dataclass(frozen=True)
class TrackedDeferral:
    issue: int
    rationale: str
    added_date: date
    regression_test: str


def assert_entries_still_apply(
    registry: dict[Any, TrackedDeferral],
    *,
    registry_name: str,
    live_keys: Collection[Any],
) -> None:
    """Reject registry rows that no longer exempt a live violation."""
    stale = sorted(set(registry) - set(live_keys), key=str)
    details = [f"{key} (issue=#{registry[key].issue})" for key in stale]
    assert not details, (
        f"{registry_name} entries that no longer match a live violation "
        f"(delete these rows): {details}"
    )


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


def _definition_paths(body: list[ast.stmt], prefix: tuple[str, ...] = ()) -> set[tuple[str, ...]]:
    """Every qualified def path in a module body, e.g. {("TestFoo", "test_bar")}."""
    paths: set[tuple[str, ...]] = set()
    for node in body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            paths.add((*prefix, node.name))
        elif isinstance(node, ast.ClassDef):
            qualified = (*prefix, node.name)
            paths.add(qualified)
            paths |= _definition_paths(node.body, qualified)
    return paths


@functools.cache
def _module_definition_paths(relative_path: str) -> frozenset[tuple[str, ...]] | None:
    """Parsed def paths for a test module, or None when the file does not exist."""
    target = _REPO_ROOT / relative_path
    if not target.is_file():
        return None
    return frozenset(_definition_paths(ast.parse(target.read_text(encoding="utf-8")).body))


def _regression_test_resolves(node_id: str) -> bool:
    file_part, separator, qualname = node_id.partition("::")
    if not separator or not qualname:
        return False
    defined = _module_definition_paths(file_part)
    if defined is None:
        return False
    # test_x[case] names the same function as test_x — parametrisation ids are
    # generated at collection time and never appear in the source.
    return tuple(part.partition("[")[0] for part in qualname.split("::")) in defined


def assert_deferrals_have_regression_tests(
    registry: dict[Any, TrackedDeferral],
    *,
    registry_name: str,
) -> None:
    """Require each deferral to name the regression evidence that keeps it honest.

    Resolution is static: the named test must exist on disk. Resolving against the
    running session's collected node ids instead breaks whenever the named test is
    not co-selected, which conservative path filtering and CI sharding both do.
    """
    unresolved = {
        str(key): entry.regression_test
        for key, entry in registry.items()
        if not entry.regression_test or not _regression_test_resolves(entry.regression_test)
    }
    assert not unresolved, (
        f"{registry_name} entries with an empty or unresolvable regression_test: {unresolved}"
    )
