"""Feature gates (FeatureDef, FEATURE_REGISTRY), label lifecycle state machine.

Zero autoskillit imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ._type_constants_registries import TOOL_SUBSET_TAGS
from ._type_enums import FeatureLifecycle, IssueLabelState

__all__ = [
    "FeatureDef",
    "FEATURE_REGISTRY",
    "RETIRED_FEATURES",
    "LabelDef",
    "LABEL_LIFECYCLE_REGISTRY",
    "LABEL_TRANSITIONS",
    "validate_label_transition",
]


@dataclass(frozen=True, slots=True)
class FeatureDef:
    """Definition of a named feature gate."""

    lifecycle: FeatureLifecycle
    description: str
    tool_tags: frozenset[str]
    skill_categories: frozenset[str]
    import_package: str | None
    tier: int = 1
    default_enabled: bool = False
    requires_backend_alignment: bool = False
    depends_on: frozenset[str] = field(default_factory=frozenset)
    since_version: str | None = None
    sunset_date: date | None = None


FEATURE_REGISTRY: dict[str, FeatureDef] = {
    "codex_backend": FeatureDef(
        lifecycle=FeatureLifecycle.EXPERIMENTAL,
        description="Codex CLI backend for headless sessions",
        tool_tags=frozenset(),
        skill_categories=frozenset(),
        import_package=None,
        tier=2,
        default_enabled=False,
        requires_backend_alignment=True,
    ),
    "fleet": FeatureDef(
        lifecycle=FeatureLifecycle.EXPERIMENTAL,
        description="L3 Fleet Orchestrator — multi-session campaign dispatch",
        tool_tags=frozenset({"fleet"}),
        skill_categories=frozenset({"fleet"}),
        import_package="autoskillit.fleet",
        tier=1,
        default_enabled=False,
        since_version="0.9.119",
    ),
    "planner": FeatureDef(
        lifecycle=FeatureLifecycle.EXPERIMENTAL,
        description=(
            "Progressive resolution planner — 3-pass sequential decomposition"
            " into GitHub-issue-ready work packages"
        ),
        tool_tags=frozenset(),
        skill_categories=frozenset({"planner"}),
        import_package="autoskillit.planner",
        tier=1,
        default_enabled=False,
        since_version="0.9.119",
    ),
    "providers": FeatureDef(
        lifecycle=FeatureLifecycle.EXPERIMENTAL,
        description=(
            "Provider routing — route recipe steps to a non-Anthropic LLM"
            " provider (e.g. MiniMax M2.7-highspeed)"
        ),
        tool_tags=frozenset(),
        skill_categories=frozenset(),
        import_package=None,
        tier=1,
        default_enabled=False,
        since_version="0.9.351",
    ),
}


@dataclass(frozen=True, slots=True)
class LabelDef:
    """Metadata for a lifecycle label managed by the issue state machine."""

    state: IssueLabelState
    color: str
    description: str
    removes_on_entry: frozenset[IssueLabelState]


LABEL_LIFECYCLE_REGISTRY: dict[IssueLabelState, LabelDef] = {
    IssueLabelState.QUEUED: LabelDef(
        state=IssueLabelState.QUEUED,
        color="c2e0c6",
        description="Issue claimed by orchestrator, waiting for recipe pickup",
        removes_on_entry=frozenset({IssueLabelState.FAIL}),
    ),
    IssueLabelState.IN_PROGRESS: LabelDef(
        state=IssueLabelState.IN_PROGRESS,
        color="fbca04",
        description="Issue is actively being processed by a pipeline session",
        removes_on_entry=frozenset({IssueLabelState.QUEUED, IssueLabelState.FAIL}),
    ),
    IssueLabelState.STAGED: LabelDef(
        state=IssueLabelState.STAGED,
        color="0075ca",
        description="Issue resolved, PR staged for promotion",
        removes_on_entry=frozenset(
            {IssueLabelState.IN_PROGRESS, IssueLabelState.FAIL, IssueLabelState.QUEUED}
        ),
    ),
    IssueLabelState.FAIL: LabelDef(
        state=IssueLabelState.FAIL,
        color="d73a4a",
        description="Recipe execution failed",
        removes_on_entry=frozenset({IssueLabelState.IN_PROGRESS, IssueLabelState.QUEUED}),
    ),
}

LABEL_TRANSITIONS: dict[IssueLabelState | None, frozenset[IssueLabelState | None]] = {
    None: frozenset({IssueLabelState.QUEUED, IssueLabelState.IN_PROGRESS}),
    IssueLabelState.QUEUED: frozenset({IssueLabelState.IN_PROGRESS, None}),
    IssueLabelState.IN_PROGRESS: frozenset(
        {
            IssueLabelState.STAGED,
            IssueLabelState.FAIL,
            None,
        }
    ),
    IssueLabelState.STAGED: frozenset(),
    IssueLabelState.FAIL: frozenset({IssueLabelState.QUEUED, IssueLabelState.IN_PROGRESS}),
}


def validate_label_transition(
    current: IssueLabelState | None,
    target: IssueLabelState | None,
) -> None:
    """Raise ValueError if the label state transition is not allowed."""
    allowed = LABEL_TRANSITIONS.get(current)
    if allowed is not None and target not in allowed:
        msg = f"Invalid label transition: {current!r} -> {target!r}"
        raise ValueError(msg)


for _ls in IssueLabelState:
    if _ls not in LABEL_LIFECYCLE_REGISTRY:
        raise AssertionError(f"IssueLabelState.{_ls.name} missing from LABEL_LIFECYCLE_REGISTRY")
    if _ls not in LABEL_TRANSITIONS:
        raise AssertionError(f"IssueLabelState.{_ls.name} missing from LABEL_TRANSITIONS")
if None not in LABEL_TRANSITIONS:
    raise AssertionError("LABEL_TRANSITIONS must contain a None (unlabeled) entry")
del _ls


RETIRED_FEATURES: frozenset[str] = frozenset()

if any(k != k.lower() for k in FEATURE_REGISTRY):
    raise AssertionError(
        "FEATURE_REGISTRY keys must be lowercase. "
        f"Offending: {sorted(k for k in FEATURE_REGISTRY if k != k.lower())}"
    )
if any(k != k.lower() for k in RETIRED_FEATURES):
    raise AssertionError(
        "RETIRED_FEATURES entries must be lowercase. "
        f"Offending: {sorted(k for k in RETIRED_FEATURES if k != k.lower())}"
    )

# Guard: FeatureDef.tool_tags must be in TOOL_SUBSET_TAGS — checked at import time.
_ALL_REGISTERED_TOOL_TAGS: frozenset[str] = frozenset(
    tag for tags in TOOL_SUBSET_TAGS.values() for tag in tags
)
if not all(
    tag in _ALL_REGISTERED_TOOL_TAGS
    for defn in FEATURE_REGISTRY.values()
    for tag in defn.tool_tags
):
    raise AssertionError(
        "FeatureDef.tool_tags contains a tag not present in TOOL_SUBSET_TAGS values. "
        "Add the tag to the appropriate tool entry in TOOL_SUBSET_TAGS first."
    )

# Guard: DEPRECATED features must have sunset_date
_DEPRECATED_WITHOUT_SUNSET = [
    k
    for k, defn in FEATURE_REGISTRY.items()
    if defn.lifecycle == FeatureLifecycle.DEPRECATED and defn.sunset_date is None
]
if _DEPRECATED_WITHOUT_SUNSET:
    raise AssertionError(
        f"DEPRECATED features must have a sunset_date. Missing: {_DEPRECATED_WITHOUT_SUNSET}"
    )
del _DEPRECATED_WITHOUT_SUNSET

# Guard: DEPRECATED features must not be default_enabled=True
_DEPRECATED_DEFAULT_ENABLED = [
    k
    for k, defn in FEATURE_REGISTRY.items()
    if defn.lifecycle == FeatureLifecycle.DEPRECATED and defn.default_enabled
]
if _DEPRECATED_DEFAULT_ENABLED:
    raise AssertionError(
        "DEPRECATED features must not be default_enabled=True. "
        f"Violations: {_DEPRECATED_DEFAULT_ENABLED}"
    )
del _DEPRECATED_DEFAULT_ENABLED
