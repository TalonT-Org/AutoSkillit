"""Read-only deterministic substrate for specialized repository exploration."""

from ._digest import canonical_json, qualified_digest
from .collectors import (
    COLLECTOR_PROFILES,
    CollectorInvocation,
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
from .pagination import normalize_query, page_evidence, pagination_identity
from .profile import (
    RepositoryProfileActivation,
    activate_repository_profiles,
    resolve_repository_profile,
)
from .router import readiness_waves, reclassify_cross_leaf, route_frontier
from .snapshot import (
    ArtifactCaptureError,
    ArtifactCaptureStatus,
    SnapshotCaptureLimits,
    SnapshotCaptureResult,
    StableArtifactCapture,
    capture_repository_snapshot,
    capture_stable_artifact,
    resolve_repository_path,
    stable_artifact_matches,
)

__all__ = [
    "AUTOSKILLIT_REPOSITORY_IDENTITY",
    "ArtifactCaptureError",
    "ArtifactCaptureStatus",
    "COLLECTOR_PROFILES",
    "OFFLINE_DECLARATION_PATH",
    "IdentityEvidence",
    "RepositoryIdentityResolution",
    "RepositoryProfileActivation",
    "CollectorLimits",
    "CollectorInvocation",
    "CollectorProfile",
    "SnapshotCaptureLimits",
    "SnapshotCaptureResult",
    "StableArtifactCapture",
    "activate_repository_profiles",
    "capture_repository_snapshot",
    "capture_stable_artifact",
    "canonical_json",
    "build_canonical_evidence_graph",
    "collect_search",
    "collector_manifest_digest",
    "evaluate_completeness",
    "normalize_query",
    "pagination_identity",
    "page_evidence",
    "qualified_digest",
    "resolve_repository_identity",
    "resolve_repository_profile",
    "resolve_repository_path",
    "stable_artifact_matches",
    "readiness_waves",
    "reclassify_cross_leaf",
    "route_frontier",
]
