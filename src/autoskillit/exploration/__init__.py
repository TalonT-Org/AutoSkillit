"""Read-only deterministic substrate for specialized repository exploration."""

from .collectors import (
    COLLECTOR_PROFILES,
    CollectorLimits,
    CollectorProfile,
    collect_search,
    collector_manifest_digest,
)
from .completeness import evaluate_completeness
from .graph import build_canonical_evidence_graph
from .identity import (
    AUTOSKILLIT_REPOSITORY_IDENTITY,
    OFFLINE_DECLARATION_PATH,
    IdentityEvidence,
    RepositoryIdentityResolution,
    resolve_repository_identity,
)
from .pagination import page_evidence
from .profile import (
    RepositoryProfileActivation,
    activate_repository_profiles,
    resolve_repository_profile,
)
from .router import readiness_waves, reclassify_cross_leaf, route_frontier
from .snapshot import (
    SnapshotCaptureLimits,
    SnapshotCaptureResult,
    capture_repository_snapshot,
    normalize_query,
    pagination_identity,
    resolve_repository_path,
)

__all__ = [
    "AUTOSKILLIT_REPOSITORY_IDENTITY",
    "COLLECTOR_PROFILES",
    "OFFLINE_DECLARATION_PATH",
    "IdentityEvidence",
    "RepositoryIdentityResolution",
    "RepositoryProfileActivation",
    "CollectorLimits",
    "CollectorProfile",
    "SnapshotCaptureLimits",
    "SnapshotCaptureResult",
    "activate_repository_profiles",
    "capture_repository_snapshot",
    "build_canonical_evidence_graph",
    "collect_search",
    "collector_manifest_digest",
    "evaluate_completeness",
    "normalize_query",
    "pagination_identity",
    "page_evidence",
    "resolve_repository_identity",
    "resolve_repository_profile",
    "resolve_repository_path",
    "readiness_waves",
    "reclassify_cross_leaf",
    "route_frontier",
]
