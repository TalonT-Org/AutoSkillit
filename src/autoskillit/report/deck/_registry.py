"""Questions, facets, and availability vocabulary for the observability deck."""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType
from typing import NamedTuple

from autoskillit.core import SessionType, TokenMeasureState


class ChipState(StrEnum):
    LIVE = "live"
    STRUCK = "struck"
    ABSENT = "absent"


class ReasonDef(NamedTuple):
    text: str
    issue: int | None = None


class FacetValueDef(NamedTuple):
    key: str
    label: str
    match: str | None
    unresolvable_in: tuple[tuple[str, ReasonDef], ...] = ()


class FacetDef(NamedTuple):
    facet_id: str
    label: str
    column: str
    declared: tuple[FacetValueDef, ...]
    null_label: str | None
    unrecorded_gap: ReasonDef | None


class WindowDef(NamedTuple):
    key: str
    label: str
    days: int | None


class DeckViewDef(NamedTuple):
    view_id: str
    question: str
    decision: str
    group: str
    table: str | None
    script: str | None
    planned_issue: int | None


SESSION_TABLE = "sessions"
SESSION_COLUMNS: tuple[str, ...] = (
    "key",
    "session_id",
    "time_ms",
    "harness",
    "provider",
    "model",
    "skill",
    "recipe",
    "step",
    "level",
    "kitchen_id",
    "order_id",
    "dispatch_id",
    "campaign_id",
    "caller_session_id",
    "parent_session_id",
    "success",
    "subtype",
    "adjudication_reason",
    "adjudication_subtype",
    "duration_seconds",
    "input_tokens",
    "output_tokens",
    "cache_write_tokens",
    "cache_read_tokens",
    "assistant_turn_count",
    "tool_counts",
)
FACETS: tuple[FacetDef, ...] = (
    FacetDef("harness", "harness", "harness", (), None, None),
    FacetDef("provider", "provider", "provider", (), None, None),
    FacetDef(
        "level",
        "level",
        "level",
        (
            FacetValueDef(
                "L0",
                "L0",
                None,
                (
                    (
                        SESSION_TABLE,
                        ReasonDef(
                            "L0 leaf agents write no session row; "
                            "they exist only as subagent transcripts"
                        ),
                    ),
                ),
            ),
            FacetValueDef("L1", "L1", SessionType.SKILL.value),
            FacetValueDef("L2", "L2", SessionType.ORCHESTRATOR.value),
            FacetValueDef("L3", "L3", SessionType.FLEET.value),
        ),
        "unrecorded",
        ReasonDef("no session row in this index records its orchestration level", 4622),
    ),
)
WINDOWS = (
    WindowDef("7d", "7 days", 7),
    WindowDef("28d", "28 days", 28),
    WindowDef("all", "all history", None),
)
WINDOW_FACET_ID = "window"
WINDOW_HISTORY_ISSUE = 4621
AVAILABILITY_VOCABULARY: Mapping[TokenMeasureState, tuple[str, str]] = MappingProxyType(
    {
        TokenMeasureState.MEASURED: ("measured", "Reported by the producer"),
        TokenMeasureState.MEASURED_ZERO: ("0", "Reported by the producer as zero"),
        TokenMeasureState.UNAVAILABLE: (
            "not reported",
            "This harness/provider pair does not emit this field — absent, not zero",
        ),
        TokenMeasureState.UNKNOWN: (
            "unknown",
            "The producer emits this field but the value is missing",
        ),
        TokenMeasureState.NOT_APPLICABLE: ("n/a", "The field does not apply here"),
    }
)
COHORT_VIEW = "cohort"
LANDING_VIEW = COHORT_VIEW
DECK_VIEWS: tuple[DeckViewDef, ...] = (
    DeckViewDef(
        COHORT_VIEW,
        "Who is in this cohort?",
        "Whether the population on screen is the one you meant, "
        "before you read any number from it",
        "Start here",
        SESSION_TABLE,
        "views/cohort.js",
        None,
    ),
    DeckViewDef(
        "spend",
        "Where do the tokens go?",
        "Where it is worth spending optimization time at all",
        "What do you need to know?",
        "skills",
        "views/spend.js",
        None,
    ),
    DeckViewDef(
        "efficiency",
        "What's inefficient?",
        "Which skill or role is wasteful, as opposed to merely big",
        "What do you need to know?",
        "skills",
        "views/efficiency.js",
        None,
    ),
    DeckViewDef(
        "skill",
        "What is this skill spending on?",
        "Which step, which subagent, how much is re-transmitted prompt",
        "What do you need to know?",
        "skills",
        "views/skill.js",
        None,
    ),
    DeckViewDef(
        "role",
        "Can this agent be cheaper?",
        "Whether to restrict a role to read-only on a smaller model",
        "What do you need to know?",
        "roles",
        "views/role.js",
        None,
    ),
    DeckViewDef(
        "context",
        "Where does context build up?",
        "Tune a subagent's output, or insert one to filter",
        "What do you need to know?",
        None,
        None,
        4652,
    ),
    DeckViewDef(
        "errors",
        "Where are the errors?",
        "Which step of which skill to fix",
        "What do you need to know?",
        None,
        None,
        4652,
    ),
    DeckViewDef(
        "trend",
        "What's the trend?",
        "Whether a change held, and over what window",
        "What do you need to know?",
        None,
        None,
        4652,
    ),
    DeckViewDef(
        "gaps",
        "What can't we see?",
        "Which telemetry gap to close first",
        "What do you need to know?",
        None,
        None,
        4652,
    ),
    DeckViewDef(
        "parity",
        "Every provider, same fields",
        "Target state for cross-pair comparison",
        "If telemetry were complete",
        None,
        None,
        4652,
    ),
)


def _validate_deck_views(
    views: tuple[DeckViewDef, ...],
    landing: str,
) -> None:
    ids = [v.view_id for v in views]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate view_id")
    for view in views:
        if view.planned_issue is None:
            if not view.table or not view.script:
                raise ValueError(f"built view {view.view_id} requires table and script")
        elif view.table is not None or view.script is not None:
            raise ValueError(f"planned view {view.view_id} must not have table or script")
    if not any(v.view_id == landing and v.planned_issue is None for v in views):
        raise ValueError("landing must name a built view")


_validate_deck_views(DECK_VIEWS, LANDING_VIEW)
