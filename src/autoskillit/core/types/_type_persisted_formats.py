"""Ledger of enums embedded in versioned persisted formats.

The ledger is intentionally string-qualified so IL-0 does not import higher
layers merely to describe their on-disk contracts.  Contract tests resolve the
strings and keep this declaration synchronized with the real decoders.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

__all__ = [
    "PERSISTED_FORMAT_LEDGER",
    "PersistedEnumDef",
    "PersistedEnumTolerance",
    "PersistedFormatDef",
]


class PersistedEnumTolerance(StrEnum):
    """How an older reader handles an unrecognized persisted enum value."""

    QUARANTINE_RECORD = "quarantine_record"
    UNKNOWN_MEMBER = "unknown_member"


@dataclass(frozen=True, slots=True)
class PersistedEnumDef:
    """One enum persisted inside a versioned on-disk format."""

    enum_qualname: str
    members: Mapping[str, int]
    tolerance: PersistedEnumTolerance


@dataclass(frozen=True, slots=True)
class PersistedFormatDef:
    """One versioned persisted format and every enum it carries."""

    format_id: str
    version_constant: str
    decoder_module: str
    enums: tuple[PersistedEnumDef, ...]
    rationale: str


def _members(**introduced: int) -> Mapping[str, int]:
    return MappingProxyType(introduced)


PERSISTED_FORMAT_LEDGER: Mapping[str, PersistedFormatDef] = MappingProxyType(
    {
        "retiring_cache": PersistedFormatDef(
            format_id="retiring_cache",
            version_constant=("autoskillit.core._retiring_cache._RETIRING_CACHE_SCHEMA_VERSION"),
            decoder_module="core/_retiring_cache.py",
            enums=(
                PersistedEnumDef(
                    enum_qualname=(
                        "autoskillit.core.types._type_plugin_source.PluginArtifactKind"
                    ),
                    members=_members(
                        PROJECTION=1,
                        INSTALLED_PLUGIN=1,
                        PLUGIN_GENERATION=2,
                        INSTALL_ROOT_GENERATION=2,
                    ),
                    tolerance=PersistedEnumTolerance.QUARANTINE_RECORD,
                ),
            ),
            rationale=(
                "PluginArtifactKind is deletion-routing authority, so unknown kinds are "
                "opaque records rather than processable sentinels. PLUGIN_GENERATION "
                "shipped in schema 2; keeping the current version at 2 lets older readers "
                "quarantine it instead of rejecting caches already written by that release."
            ),
        ),
        "fleet_campaign_state": PersistedFormatDef(
            format_id="fleet_campaign_state",
            version_constant="autoskillit.fleet.state_types.FLEET_STATE_SCHEMA_VERSION",
            decoder_module="fleet/state_types.py",
            enums=(
                PersistedEnumDef(
                    enum_qualname="autoskillit.fleet.state_types.DispatchStatus",
                    members=_members(
                        PENDING=1,
                        RUNNING=1,
                        SUCCESS=1,
                        FAILURE=1,
                        INTERRUPTED=1,
                        RESUMABLE=1,
                        SKIPPED=1,
                        REFUSED=1,
                        RELEASED=1,
                        UNKNOWN=12,
                    ),
                    tolerance=PersistedEnumTolerance.UNKNOWN_MEMBER,
                ),
            ),
            rationale=(
                "Dispatch status is descriptive state. UNKNOWN is non-terminal and cannot "
                "authorize completion, cleanup, reset, or reaping; the original token is "
                "retained for round-trip preservation."
            ),
        ),
        "capture_lifecycle_ledger": PersistedFormatDef(
            format_id="capture_lifecycle_ledger",
            version_constant="autoskillit.hooks._capture._ledger.CURRENT_FORMAT_VERSION",
            decoder_module="hooks/_capture/_ledger.py",
            enums=tuple(
                PersistedEnumDef(
                    enum_qualname=("autoskillit.hooks._capture._lifecycle_policy." + enum_name),
                    members=_members(**members),
                    tolerance=PersistedEnumTolerance.QUARANTINE_RECORD,
                )
                for enum_name, members in (
                    (
                        "CaptureState",
                        {
                            "RESERVED": 1,
                            "STAGED": 1,
                            "PUBLISHED_WRITING": 1,
                            "FINALIZED": 1,
                            "FAILED": 1,
                            "ABANDONED": 1,
                            "DELETING": 1,
                            "TAMPERED": 1,
                            "DELETED": 1,
                        },
                    ),
                    (
                        "CaptureStatus",
                        {
                            "PENDING": 1,
                            "COMPLETE": 1,
                            "FAILED": 1,
                            "LEGACY_CLEANUP_ONLY": 2,
                        },
                    ),
                    ("CaptureSnapshotStatus", {"ABSENT": 1, "VERIFIED": 1}),
                    (
                        "CaptureReferenceStatus",
                        {
                            "NOT_REQUESTED": 1,
                            "ISSUED": 1,
                            "PUBLISHED": 1,
                            "UNAVAILABLE": 1,
                            "UNKNOWN": 1,
                            "EXPIRED": 1,
                            "REVOKED": 1,
                        },
                    ),
                    (
                        "CaptureDeliveryStatus",
                        {
                            "NOT_ATTEMPTED": 1,
                            "ATTEMPTING": 1,
                            "DELIVERED": 1,
                            "FAILED": 1,
                            "UNKNOWN": 1,
                        },
                    ),
                    (
                        "CaptureRetentionPhase",
                        {
                            "ACTIVE": 1,
                            "ELIGIBLE": 1,
                            "DELETING": 1,
                            "TAMPERED": 1,
                            "DELETED": 1,
                        },
                    ),
                )
            ),
            rationale=(
                "Lifecycle values feed total successor and reclaimability tables. An "
                "unknown value therefore quarantines its exact frame before policy lookup; "
                "hook-side decoding remains stdlib-only and does not import this ledger."
            ),
        ),
        "skill_session_contract": PersistedFormatDef(
            format_id="skill_session_contract",
            version_constant=(
                "autoskillit.core.types._type_skill_contract.SKILL_SESSION_CONTRACT_SCHEMA_VERSION"
            ),
            decoder_module="execution/session/_skill_session_contract_codec.py",
            enums=(
                PersistedEnumDef(
                    enum_qualname=("autoskillit.core.types._type_exploration.RepositoryProfileId"),
                    members=_members(
                        AUTO=1,
                        LANGUAGE_NEUTRAL=1,
                        GENERIC_PYTHON=1,
                        AUTOSKILLIT=1,
                    ),
                    tolerance=PersistedEnumTolerance.QUARANTINE_RECORD,
                ),
                PersistedEnumDef(
                    enum_qualname=(
                        "autoskillit.core.types._type_skill_contract.ExplorationVectorDisposition"
                    ),
                    members=_members(MIGRATED=1, RETAINED=1, EXCLUDED=1),
                    tolerance=PersistedEnumTolerance.QUARANTINE_RECORD,
                ),
                PersistedEnumDef(
                    enum_qualname=(
                        "autoskillit.core.types._type_skill_contract."
                        "ExplorationVectorApplicabilityId"
                    ),
                    members=_members(ALWAYS=1, PLANNER_EXTRACT_DOMAIN_DEEP=5),
                    tolerance=PersistedEnumTolerance.QUARANTINE_RECORD,
                ),
                PersistedEnumDef(
                    enum_qualname=("autoskillit.core.types._type_exploration.RelationshipKind"),
                    members=_members(
                        DECLARES=1,
                        DEFINES=1,
                        IMPORTS=1,
                        CALLS=1,
                        REFERENCES=1,
                        AFFECTS=1,
                        CONFLICTS_WITH=1,
                    ),
                    tolerance=PersistedEnumTolerance.QUARANTINE_RECORD,
                ),
            ),
            rationale=(
                "Repository profiles route to concrete handlers and vector enum values "
                "participate in digest-bound routing. Unknown values quarantine the exact "
                "vector instead of creating unowned processable sentinels."
            ),
        ),
    }
)
