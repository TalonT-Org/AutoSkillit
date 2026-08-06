"""Immutable, deterministic contracts for read-only repository exploration."""

from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, Generic, Protocol, TypeVar, runtime_checkable

_T = TypeVar("_T")


def _canonical_digest(domain: str, value: object) -> str:
    """Hash canonical data with a domain separator to prevent type confusion."""

    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{domain} contains non-canonical data") from exc
    return hashlib.sha256(domain.encode("ascii") + b"\0" + encoded).hexdigest()


class RepositoryProfileId(StrEnum):
    """Built-in capability profiles used by the deterministic router."""

    AUTO = "auto"
    LANGUAGE_NEUTRAL = "language-neutral"
    GENERIC_PYTHON = "generic-python"
    AUTOSKILLIT = "autoskillit"


class ExplorationApplicability(StrEnum):
    """Whether a profile may answer a specific query under closed-world rules."""

    APPLICABLE = "applicable"
    NOT_APPLICABLE = "not-applicable"
    UNKNOWN = "unknown"


class RelationshipKind(StrEnum):
    """Relationship vocabulary; callers must not invent unregistered values."""

    DECLARES = "declares"
    DEFINES = "defines"
    IMPORTS = "imports"
    CALLS = "calls"
    REFERENCES = "references"
    AFFECTS = "affects"
    CONFLICTS_WITH = "conflicts-with"


class MethodProvenance(StrEnum):
    """How an evidence observation was obtained."""

    COLLECTOR = "collector"
    EXPLORER = "explorer"
    CROSS_LEAF_HANDOFF = "cross-leaf-handoff"


class CollectorStatus(StrEnum):
    """Explicit terminal states used by completeness evaluation."""

    SUCCEEDED = "succeeded"
    EMPTY = "empty"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"
    TRUNCATED = "truncated"


class CapabilityResolutionStatus(StrEnum):
    """The non-sensitive reason a broker capability did not resolve."""

    OK = "ok"
    INVALID = "invalid"
    EXPIRED = "expired"
    OWNER_MISMATCH = "owner_mismatch"
    ROLE_MISMATCH = "role_mismatch"
    SESSION_MISMATCH = "session_mismatch"


@dataclass(frozen=True, slots=True)
class CapabilityResolution(Generic[_T]):
    """A capability lookup result that never returns another caller's state."""

    status: CapabilityResolutionStatus
    value: _T | None = None


@runtime_checkable
class ExplorationContextStoreProtocol(Protocol[_T]):
    """Owner-bound lifecycle authority for brokered exploration state."""

    @property
    def trusted_root(self) -> Path: ...

    def issue(
        self,
        *,
        owner_id: str,
        role: str,
        session_id: str,
        value: _T,
        ttl_seconds: float | None = None,
    ) -> str:
        """Issue a capability or raise when binding, TTL, capacity, or state is invalid."""
        ...

    def resolve(
        self,
        *,
        capability: str,
        owner_id: str,
        role: str,
        session_id: str,
    ) -> CapabilityResolution[_T]:
        """Return owner-safe status for capability mismatch; invalid bindings raise."""
        ...

    def discard(self, capability: str) -> None: ...

    def bind_launches(
        self,
        *,
        owner_id: str,
        session_id: str,
        cwd: Path,
        repository_root: Path,
        source_identities: Mapping[str, str],
        authority_home: Path,
    ) -> Mapping[str, Mapping[str, str]]:
        """Issue launch bindings or raise when trusted authority cannot be established."""
        ...

    def submit_from_launch_environment(
        self,
        *,
        query: ExplorationQuerySpec,
        page_size: int,
    ) -> tuple[CapabilityResolutionStatus, EvidencePage | None]:
        """Submit from launch authority, failing closed to a status and no page."""
        ...

    def get_page_from_launch_environment(
        self,
        *,
        page_size: int,
        cursor: ContinuationCursor | None = None,
    ) -> tuple[CapabilityResolutionStatus, EvidencePage | None]:
        """Fail closed on launch authority; page or cursor validation errors may raise."""
        ...

    def cleanup_session(self, session_id: str) -> None: ...

    def cleanup_expired(self) -> int: ...

    def close(self) -> None: ...

    def submit(
        self,
        *,
        owner_id: str,
        role: str,
        session_id: str,
        query: ExplorationQuerySpec,
        page_size: int,
    ) -> tuple[str, EvidencePage]:
        """Collect and issue a capability, raising when collection or issuance fails."""
        ...

    def get_page(
        self,
        *,
        capability: str,
        owner_id: str,
        role: str,
        session_id: str,
        page_size: int,
        cursor: ContinuationCursor | None = None,
    ) -> tuple[CapabilityResolutionStatus, EvidencePage | None]:
        """Return owner-safe resolution status; page or cursor validation may raise."""
        ...


@dataclass(frozen=True, slots=True)
class RepositoryIdentity:
    repository: str
    revision: str
    host: str = ""
    owner: str = ""
    repo: str = ""
    common_git_dir: str = ""
    worktree_path: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.repository, str) or not self.repository:
            raise ValueError("repository must be a non-empty string")
        if not isinstance(self.revision, str) or not self.revision:
            raise ValueError("revision must be a non-empty string")

    @property
    def digest(self) -> str:
        return _canonical_digest(
            "repository-identity/v2",
            [
                self.repository,
                self.revision,
                self.host,
                self.owner,
                self.repo,
                self.common_git_dir,
                self.worktree_path,
            ],
        )


@dataclass(frozen=True, slots=True)
class RepositorySnapshot:
    identity: RepositoryIdentity
    tree_digest: str
    collector_manifest_digest: str
    head_sha: str = ""
    index_digest: str = ""
    tracked_records: tuple[tuple[str, str], ...] = ()
    untracked_records: tuple[tuple[str, str], ...] = ()
    ignored_records: tuple[tuple[str, str], ...] = ()
    missing_records: tuple[tuple[str, str], ...] = ()
    mode_records: tuple[tuple[str, str], ...] = ()
    symlink_records: tuple[tuple[str, str], ...] = ()
    profile_versions: tuple[tuple[str, str], ...] = ()
    profile_activation_digest: str = ""
    schema_version: str = ""
    ignore_policy_digest: str = ""
    pagination_identity: str = ""
    state: str = ""
    stale: bool = False
    truncated: bool = False
    truncation_reason: str | None = None

    def __post_init__(self) -> None:
        if self.stale and self.truncated:
            raise ValueError("a snapshot cannot be stale and truncated simultaneously")
        if self.truncated != (self.truncation_reason is not None):
            raise ValueError("truncation state requires an exact truncation reason")

    @property
    def digest(self) -> str:
        return _canonical_digest(
            "repository-snapshot/v2",
            {
                "identity": self.identity.digest,
                "tree": self.tree_digest,
                "collector_manifest": self.collector_manifest_digest,
                "head": self.head_sha,
                "index": self.index_digest,
                "tracked": self.tracked_records,
                "untracked": self.untracked_records,
                "ignored": self.ignored_records,
                "missing": self.missing_records,
                "mode": self.mode_records,
                "symlink": self.symlink_records,
                "profile_versions": self.profile_versions,
                "profile_activation": self.profile_activation_digest,
                "schema_version": self.schema_version,
                "ignore_policy": self.ignore_policy_digest,
                "pagination_identity": self.pagination_identity,
                "state": self.state,
                "stale": self.stale,
                "truncated": self.truncated,
                "truncation_reason": self.truncation_reason,
            },
        )


@dataclass(frozen=True, slots=True)
class ProfileActivation:
    profile: RepositoryProfileId
    applicability: ExplorationApplicability
    reason: str


@dataclass(frozen=True, slots=True)
class ExplorationQuerySpec:
    query: str
    required_profiles: tuple[RepositoryProfileId, ...] = ()
    scope: tuple[str, ...] = ()
    max_results: int = 100

    def __post_init__(self) -> None:
        if not self.query.strip() or self.max_results <= 0:
            raise ValueError("query and max_results must be non-empty and positive")
        if len(set(self.required_profiles)) != len(self.required_profiles):
            raise ValueError("required profiles must be unique")

    @property
    def digest(self) -> str:
        return _canonical_digest(
            "exploration-query/v1",
            [self.query, list(self.required_profiles), list(self.scope), self.max_results],
        )


@dataclass(frozen=True, slots=True)
class FrontierItem:
    item_id: str
    query: ExplorationQuerySpec
    profile: RepositoryProfileId
    depends_on: tuple[str, ...] = ()
    scope: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExplorationTaskSpec:
    task_id: str
    frontier_item_id: str
    profile: RepositoryProfileId
    depends_on: tuple[str, ...] = ()
    scope: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExplorationRouterPlan:
    snapshot: RepositorySnapshot | None
    tasks: tuple[ExplorationTaskSpec, ...]
    activations: tuple[ProfileActivation, ...]

    @property
    def digest(self) -> str:
        return _canonical_digest(
            "exploration-router-plan/v1",
            {
                "snapshot": self.snapshot.digest if self.snapshot is not None else None,
                "tasks": [
                    [
                        task.task_id,
                        task.frontier_item_id,
                        task.profile,
                        list(task.depends_on),
                        list(task.scope),
                    ]
                    for task in self.tasks
                ],
                "activations": [
                    [activation.profile, activation.applicability, activation.reason]
                    for activation in self.activations
                ],
            },
        )


@dataclass(frozen=True, slots=True, order=True)
class NodeKey:
    namespace: str
    value: str

    @property
    def digest(self) -> str:
        return _canonical_digest("exploration-node-key/v1", [self.namespace, self.value])


@dataclass(frozen=True, slots=True)
class GraphNode:
    key: NodeKey
    label: str
    facts: tuple[str, ...] = ()
    inferences: tuple[str, ...] = ()
    unknowns: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class GraphEdge:
    source: NodeKey
    target: NodeKey
    relationship: RelationshipKind
    facts: tuple[str, ...] = ()
    inferences: tuple[str, ...] = ()
    unknowns: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        return _canonical_digest(
            "exploration-edge/v1", [self.source.digest, self.target.digest, self.relationship]
        )


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    evidence_id: str
    provenance: MethodProvenance
    snapshot_digest: str
    subject: NodeKey | None = None
    facts: tuple[str, ...] = ()
    inferences: tuple[str, ...] = ()
    unknowns: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    locator: str | None = None
    method: str | None = None
    extractor_version: str | None = None
    searched_scope: tuple[str, ...] = ()
    location: str | None = None
    query_uncertainty: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CollectorReport:
    collector_id: str
    status: CollectorStatus
    snapshot_digest: str
    evidence: tuple[EvidenceRecord, ...] = ()
    diagnostic: str | None = None


@dataclass(frozen=True, slots=True)
class CompletenessReport:
    expected_collectors: tuple[str, ...]
    reports: tuple[CollectorReport, ...]
    complete: bool
    missing_collectors: tuple[str, ...] = ()
    failed_collectors: tuple[str, ...] = ()


_CURSOR_VERSION: Final = 2


@dataclass(frozen=True, slots=True)
class ContinuationCursor:
    result_digest: str
    offset: int
    page_size: int
    authority_digest: str = ""

    def __post_init__(self) -> None:
        if self.offset < 0 or self.page_size <= 0:
            raise ValueError("cursor offset must be non-negative and page size positive")

    def encode(self) -> str:
        payload = {
            "a": self.authority_digest,
            "d": self.result_digest,
            "o": self.offset,
            "p": self.page_size,
            "v": _CURSOR_VERSION,
        }
        return (
            base64.urlsafe_b64encode(
                json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("ascii")
            )
            .decode("ascii")
            .rstrip("=")
        )

    @classmethod
    def decode(cls, token: str) -> ContinuationCursor:
        try:
            padded = token + "=" * (-len(token) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
            if set(payload) != {"a", "d", "o", "p", "v"} or payload["v"] != _CURSOR_VERSION:
                raise ValueError("unknown cursor schema")
            if (
                not isinstance(payload["a"], str)
                or not isinstance(payload["d"], str)
                or not isinstance(payload["o"], int)
                or isinstance(payload["o"], bool)
                or not isinstance(payload["p"], int)
                or isinstance(payload["p"], bool)
            ):
                raise ValueError("invalid cursor field types")
            return cls(
                result_digest=payload["d"],
                offset=payload["o"],
                page_size=payload["p"],
                authority_digest=payload["a"],
            )
        except (TypeError, ValueError, UnicodeDecodeError, UnicodeEncodeError) as exc:
            raise ValueError("invalid continuation cursor") from exc


@dataclass(frozen=True, slots=True)
class EvidencePage:
    evidence: tuple[EvidenceRecord, ...]
    result_digest: str
    completeness: CompletenessReport
    continuation: ContinuationCursor | None = None
    graph_nodes: tuple[GraphNode, ...] = ()
    graph_edges: tuple[GraphEdge, ...] = ()
    graph_conflicts: tuple[str, ...] = ()


__all__ = [
    "CapabilityResolution",
    "CapabilityResolutionStatus",
    "CollectorReport",
    "CollectorStatus",
    "CompletenessReport",
    "ContinuationCursor",
    "EvidencePage",
    "EvidenceRecord",
    "ExplorationApplicability",
    "ExplorationContextStoreProtocol",
    "ExplorationQuerySpec",
    "ExplorationRouterPlan",
    "ExplorationTaskSpec",
    "FrontierItem",
    "GraphEdge",
    "GraphNode",
    "MethodProvenance",
    "NodeKey",
    "ProfileActivation",
    "RelationshipKind",
    "RepositoryIdentity",
    "RepositoryProfileId",
    "RepositorySnapshot",
]
