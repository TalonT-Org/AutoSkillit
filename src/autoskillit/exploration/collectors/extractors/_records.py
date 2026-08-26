"""Dataclasses and version constant for the collector registry.

Decomposed from the original ``collectors/extractors.py`` per #4836. Owns
``_COLLECTOR_VERSION`` as a runtime dataclass default for ``CollectorProfile``;
this is the dependency-free core of the registry.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypeAlias

from autoskillit.core import (
    CollectorReport,
    RepositoryProfileId,
)

from .._bounded import CollectorLimits

__all__ = [
    "CollectorInvocation",
    "CollectorProfile",
    "_PerScopeCollector",
]

# Type aliases colocated with ``CollectorInvocation`` because the dataclass field
# annotation and ``_per_scope_invocation``/``_search_invocation``/``_unsupported_invocation``
# all reference them; keeping the aliases in the same shard as the dataclass
# avoids import cycles at field-evaluation time.
_InvocationReports: TypeAlias = tuple[tuple[tuple[str, ...], CollectorReport], ...]
_InvocationAdapter: TypeAlias = Callable[
    [Path, str, str, tuple[str, ...], CollectorLimits], _InvocationReports
]
_PerScopeCollector: TypeAlias = Callable[[Path, str, str, CollectorLimits], CollectorReport]

_COLLECTOR_VERSION: Final = "autoskillit.collector-extractors.v3"


@dataclass(frozen=True, slots=True)
class CollectorInvocation:
    """Registry-owned adapter from one query to typed, scope-labelled reports."""

    collector_id: str
    adapter_id: str
    adapter: _InvocationAdapter

    def __call__(
        self,
        root: Path,
        snapshot_digest: str,
        query: str,
        scopes: tuple[str, ...],
        limits: CollectorLimits,
    ) -> _InvocationReports:
        reports = self.adapter(root, snapshot_digest, query, scopes, limits)
        if not reports:
            raise ValueError(f"collector {self.collector_id} returned no invocation reports")
        for searched_scope, report in reports:
            if not searched_scope or any(not scope for scope in searched_scope):
                raise ValueError(f"collector {self.collector_id} returned an empty searched scope")
            if report.collector_id != self.collector_id:
                raise ValueError(
                    f"collector {self.collector_id} returned report for {report.collector_id}"
                )
            if report.snapshot_digest != snapshot_digest:
                raise ValueError(
                    f"collector {self.collector_id} returned report for another snapshot"
                )
        return reports


@dataclass(frozen=True, slots=True)
class CollectorProfile:
    collector_id: str
    method: str
    invocation: CollectorInvocation
    profile: RepositoryProfileId
    version: str = _COLLECTOR_VERSION
    required_by_default: bool = False

    def __post_init__(self) -> None:
        if self.invocation.collector_id != self.collector_id:
            raise ValueError("collector profile and invocation identifiers must match")
