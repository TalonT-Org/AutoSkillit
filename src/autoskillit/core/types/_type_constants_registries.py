"""Tool registries, pack registries, tool-to-tag mappings, visibility tags.

Zero autoskillit imports — except sibling `_type_constants_env` for backend name constants,
sibling `_type_enums` for `FleetErrorCode`, and sibling `_type_backend` for the
`BackendCapabilities` boot-time field check.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields
from types import MappingProxyType
from typing import Literal, NamedTuple

# _type_backend.py itself imports several other core/types siblings, but all
# of them stay within core/ (IL-0) — importing BackendCapabilities here does
# not introduce a circular or cross-layer dependency.
from ._type_backend import BackendCapabilities
from ._type_constants_env import AGENT_BACKEND_CLAUDE_CODE
from ._type_enums import FleetErrorCode, SkillExecutionRole
from ._type_exceptions import SkillContractError

__all__ = [
    "PIPELINE_FORBIDDEN_TOOLS",
    "SKILL_TOOLS",
    "GATED_TOOLS",
    "HEADLESS_TOOLS",
    "FLEET_TOOLS",
    "FLEET_DISPATCH_TOOLS",
    "FLEET_MENU_TOOLS",
    "FLEET_ERROR_CODES",
    "FREE_RANGE_TOOLS",
    "UNGATED_TOOLS",
    "PackDef",
    "PACK_REGISTRY",
    "CATEGORY_TAGS",
    "RecipePackDef",
    "RECIPE_PACK_REGISTRY",
    "RECIPE_PACK_TAGS",
    "ResponseBackstopExemptionDef",
    "RESPONSE_BACKSTOP_EXEMPTION_REGISTRY",
    "RESPONSE_BACKSTOP_EXEMPTION_REGISTRY_DIGEST",
    "AgentPackDef",
    "AGENT_PACK_REGISTRY",
    "CORE_PACKS",
    "TOOL_SUBSET_TAGS",
    "ALL_VISIBILITY_TAGS",
    "RecipeDeliverySurfaceDef",
    "RECIPE_DELIVERY_SURFACE_REGISTRY",
    "RECIPE_DELIVERY_SURFACE_REGISTRY_DIGEST",
    "ExecutionInstallSiteDef",
    "RECIPE_EXECUTION_INSTALL_SITE_REGISTRY",
    "RECIPE_EXECUTION_INSTALL_SITE_REGISTRY_DIGEST",
    "RecipeSectionDef",
    "RECIPE_SECTION_REGISTRY",
    "DYNAMIC_RECIPE_SECTION_DEF",
    "RECIPE_SECTION_PAGINATION_VERSION",
    "RECIPE_SECTION_REGISTRY_DIGEST",
    "RECIPE_SECTION_PAGINATION_POLICY_DIGEST",
    "RecipeSectionContentFormatDef",
    "RECIPE_SECTION_CONTENT_FORMAT_REGISTRY",
    "RECIPE_SECTION_MANDATORY_FAILURE_CODES",
    "RECIPE_SECTION_RESPONSE_FLOOR_BYTES",
    "SkillCapabilityDef",
    "HardCapabilityMismatch",
    "SKILL_CAPABILITY_REGISTRY",
    "describe_capability_mismatches",
    "unsatisfied_backend_capabilities",
    "validate_skill_capability_roles",
]

# Native Claude Code tools that pipeline orchestrators must NEVER use directly.
PIPELINE_FORBIDDEN_TOOLS: tuple[str, ...] = (
    "Read",
    "Grep",
    "Glob",
    "Edit",
    "Write",
    "Bash",
    "Agent",
    "WebFetch",
    "WebSearch",
    "NotebookEdit",
)

# Skill tools that route headless Claude sessions.
SKILL_TOOLS: frozenset[str] = frozenset({"run_skill"})

# Authoritative MCP tool registries.
GATED_TOOLS: frozenset[str] = frozenset(
    {
        "run_cmd",
        "run_python",
        "read_db",
        "run_skill",
        "merge_worktree",
        "reset_test_dir",
        "classify_fix",
        "reset_workspace",
        "migrate_recipe",
        "clone_repo",
        "remove_clone",
        "push_to_remote",
        "report_bug",
        "prepare_issue",
        "enrich_issues",
        "claim_issue",
        "release_issue",
        "wait_for_ci",
        "wait_for_merge_queue",
        "check_repo_merge_state",
        "toggle_auto_merge",
        "enqueue_pr",
        "create_unique_branch",
        "write_telemetry_files",
        "get_pr_reviews",
        "bulk_close_issues",
        "check_pr_mergeable",
        "set_commit_status",
        "analyze_tool_sequences",
        "fetch_github_issue",
        "get_issue_title",
        "get_ci_status",
        "get_pipeline_report",
        "get_quota_events",
        "get_timing_summary",
        "get_token_summary",
        "kitchen_status",
        "list_recipes",
        "load_recipe",
        "validate_recipe",
        "register_clone_status",
        "batch_cleanup_clones",
        "dispatch_food_truck",
        "record_gate_dispatch",
        "bootstrap_clone",
        "claim_and_resolve_issue",
        "create_and_publish_branch",
        "record_pipeline_step",
        "reset_dispatch",
        "get_recipe_section",
        "complete_recipe_initialization",
    }
)

HEADLESS_TOOLS: frozenset[str] = frozenset(
    {
        "test_check",
        "unlock_agent_pack",
        "commit_files",
        "post_pr_review",
        "write_audit_semantic_result",
        "write_standalone_audit_evidence",
        "write_audit_disposition_bundle",
    }
)

FLEET_TOOLS: frozenset[str] = frozenset(
    {
        "batch_cleanup_clones",
        "get_pipeline_report",
        "get_token_summary",
        "get_timing_summary",
        "get_quota_events",
        "dispatch_food_truck",
        "record_gate_dispatch",
        "reset_dispatch",
    }
)

FLEET_DISPATCH_TOOLS: frozenset[str] = frozenset(
    {
        "list_recipes",
        "load_recipe",
        "fetch_github_issue",
        "get_issue_title",
    }
)

FLEET_MENU_TOOLS: tuple[str, ...] = (
    "dispatch_food_truck",
    "record_gate_dispatch",
    "reset_dispatch",
)

FLEET_ERROR_CODES: frozenset[str] = frozenset(FleetErrorCode)

FREE_RANGE_TOOLS: frozenset[str] = frozenset(
    {
        "open_kitchen",
        "close_kitchen",
        "disable_quota_guard",
        "reload_session",
        "configure_fleet",
        "configure_order",
        "lock_ingredients",  # NEW (#3357)
    }
)

UNGATED_TOOLS: frozenset[str] = FREE_RANGE_TOOLS


class ResponseBackstopExemptionDef(NamedTuple):
    """Measured ceiling for a tool that bypasses universal response shaping."""

    max_chars: int
    max_utf8_bytes: int
    measurement_id: str


class RecipeDeliverySurfaceDef(NamedTuple):
    """Static definition of one recipe-bearing public delivery route."""

    producer_tool: str
    route_kind: Literal["tool", "deferred_tool", "resource"]
    negotiation_eligible: bool
    pull_eligible: bool
    recreation_eligible: bool
    initialization_activating: bool
    response_exemption_tool: str | None
    response_exemption: ResponseBackstopExemptionDef | None


_RECIPE_RESPONSE_MAX_UTF8_BYTES = 195_000

RECIPE_DELIVERY_SURFACE_REGISTRY: Mapping[str, RecipeDeliverySurfaceDef] = MappingProxyType(
    {
        "open_kitchen": RecipeDeliverySurfaceDef(
            producer_tool="open_kitchen",
            route_kind="tool",
            negotiation_eligible=True,
            pull_eligible=True,
            recreation_eligible=True,
            initialization_activating=True,
            response_exemption_tool="open_kitchen",
            response_exemption=ResponseBackstopExemptionDef(
                max_chars=_RECIPE_RESPONSE_MAX_UTF8_BYTES,
                max_utf8_bytes=_RECIPE_RESPONSE_MAX_UTF8_BYTES,
                measurement_id="bundled-recipes-all-modes-2026-07-22/open-kitchen",
            ),
        ),
        "open_kitchen_deferred_recall": RecipeDeliverySurfaceDef(
            producer_tool="open_kitchen",
            route_kind="deferred_tool",
            negotiation_eligible=True,
            pull_eligible=True,
            recreation_eligible=True,
            initialization_activating=True,
            response_exemption_tool="open_kitchen",
            response_exemption=None,
        ),
        "load_recipe": RecipeDeliverySurfaceDef(
            producer_tool="load_recipe",
            route_kind="tool",
            negotiation_eligible=True,
            pull_eligible=True,
            recreation_eligible=False,
            initialization_activating=False,
            response_exemption_tool="load_recipe",
            response_exemption=ResponseBackstopExemptionDef(
                max_chars=_RECIPE_RESPONSE_MAX_UTF8_BYTES,
                max_utf8_bytes=_RECIPE_RESPONSE_MAX_UTF8_BYTES,
                measurement_id="bundled-recipes-all-modes-2026-07-22/load-recipe",
            ),
        ),
        "get_recipe": RecipeDeliverySurfaceDef(
            producer_tool="get_recipe",
            route_kind="resource",
            negotiation_eligible=False,
            pull_eligible=True,
            recreation_eligible=True,
            initialization_activating=False,
            response_exemption_tool=None,
            response_exemption=None,
        ),
    }
)


def _recipe_delivery_surface_registry_digest() -> str:
    canonical = {
        surface: {
            **definition._asdict(),
            "response_exemption": (
                definition.response_exemption._asdict()
                if definition.response_exemption is not None
                else None
            ),
        }
        for surface, definition in sorted(RECIPE_DELIVERY_SURFACE_REGISTRY.items())
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


RECIPE_DELIVERY_SURFACE_REGISTRY_DIGEST: str = _recipe_delivery_surface_registry_digest()


class ExecutionInstallSiteDef(NamedTuple):
    """Static binding of one execution-install site to the response that delivers it."""

    installer_module: str
    installer_symbol: str
    delivering_module: str
    delivering_symbol: str
    delivery_surface: str


RECIPE_EXECUTION_INSTALL_SITE_REGISTRY: Mapping[str, ExecutionInstallSiteDef] = MappingProxyType(
    {
        "inline_delivery": ExecutionInstallSiteDef(
            installer_module="src/autoskillit/server/_recipe_delivery.py",
            installer_symbol="complete_finalized_recipe_response",
            delivering_module="src/autoskillit/server/_recipe_delivery.py",
            delivering_symbol="build_canonical_recipe_artifact_payload",
            delivery_surface="open_kitchen",
        ),
        "initialization_completion": ExecutionInstallSiteDef(
            installer_module="src/autoskillit/server/_recipe_initialization.py",
            installer_symbol="complete_initialization_response",
            delivering_module="src/autoskillit/server/_recipe_initialization.py",
            delivering_symbol="build_completion_response",
            delivery_surface="complete_recipe_initialization",
        ),
    }
)


def _recipe_execution_install_site_registry_digest() -> str:
    canonical = {
        site: definition._asdict()
        for site, definition in sorted(RECIPE_EXECUTION_INSTALL_SITE_REGISTRY.items())
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


RECIPE_EXECUTION_INSTALL_SITE_REGISTRY_DIGEST: str = (
    _recipe_execution_install_site_registry_digest()
)


@dataclass(frozen=True, slots=True)
class RecipeSectionDef:
    """Static schema and pagination definition for one pullable recipe section."""

    name: str
    value_kind: Literal["string", "array"]
    element_kind: Literal["string"] | None
    missing_behavior: Literal["invalid", "absent", "default"]
    none_behavior: Literal["invalid", "absent"]
    section_strategy: Literal["raw", "scalar", "array"]
    range_unit: Literal["utf8-bytes", "decoded-utf8-bytes", "elements"]
    ordinary_content_format: Literal["raw-text", "json-scalar-page", "json-array-page"]
    oversized_content_format: Literal["json-element-fragment"] | None
    has_default: bool = False
    default_value: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        """Reject contradictory public definitions at their construction boundary."""
        if not self.name:
            raise ValueError("recipe section definition name must not be empty")
        if self.missing_behavior not in {"invalid", "absent", "default"}:
            raise ValueError("invalid recipe section missing behavior")
        if self.none_behavior not in {"invalid", "absent"}:
            raise ValueError("invalid recipe section null behavior")
        layout = (
            self.value_kind,
            self.element_kind,
            self.section_strategy,
            self.range_unit,
            self.ordinary_content_format,
            self.oversized_content_format,
        )
        valid_layouts = {
            ("string", None, "raw", "utf8-bytes", "raw-text", None),
            (
                "string",
                None,
                "scalar",
                "decoded-utf8-bytes",
                "json-scalar-page",
                None,
            ),
            (
                "array",
                "string",
                "array",
                "elements",
                "json-array-page",
                "json-element-fragment",
            ),
        }
        if layout not in valid_layouts:
            raise ValueError("invalid recipe section strategy and content-format combination")
        if self.has_default != (self.missing_behavior == "default"):
            raise ValueError("recipe section default flag must match missing behavior")
        if self.has_default:
            if self.value_kind != "array" or self.default_value != ():
                raise ValueError("defaulted recipe sections require an empty array default")
        elif self.default_value is not None:
            raise ValueError("recipe sections without defaults must not declare a default value")


def _validated_recipe_section_registry(
    definitions: dict[str, RecipeSectionDef],
) -> Mapping[str, RecipeSectionDef]:
    for name, definition in definitions.items():
        if definition.name != name:
            raise ValueError(f"recipe section registry key {name!r} must match definition name")
    return MappingProxyType(definitions)


RECIPE_SECTION_REGISTRY: Mapping[str, RecipeSectionDef] = _validated_recipe_section_registry(
    {
        "content": RecipeSectionDef(
            name="content",
            value_kind="string",
            element_kind=None,
            missing_behavior="invalid",
            none_behavior="invalid",
            section_strategy="raw",
            range_unit="utf8-bytes",
            ordinary_content_format="raw-text",
            oversized_content_format=None,
        ),
        "ingredients_table": RecipeSectionDef(
            name="ingredients_table",
            value_kind="string",
            element_kind=None,
            missing_behavior="absent",
            none_behavior="absent",
            section_strategy="scalar",
            range_unit="decoded-utf8-bytes",
            ordinary_content_format="json-scalar-page",
            oversized_content_format=None,
        ),
        "orchestration_rules": RecipeSectionDef(
            name="orchestration_rules",
            value_kind="string",
            element_kind=None,
            missing_behavior="absent",
            none_behavior="invalid",
            section_strategy="raw",
            range_unit="utf8-bytes",
            ordinary_content_format="raw-text",
            oversized_content_format=None,
        ),
        "stop_step_semantics": RecipeSectionDef(
            name="stop_step_semantics",
            value_kind="string",
            element_kind=None,
            missing_behavior="absent",
            none_behavior="invalid",
            section_strategy="raw",
            range_unit="utf8-bytes",
            ordinary_content_format="raw-text",
            oversized_content_format=None,
        ),
        "errors": RecipeSectionDef(
            name="errors",
            value_kind="array",
            element_kind="string",
            missing_behavior="default",
            none_behavior="invalid",
            section_strategy="array",
            range_unit="elements",
            ordinary_content_format="json-array-page",
            oversized_content_format="json-element-fragment",
            has_default=True,
            default_value=(),
        ),
        "flow_records": RecipeSectionDef(
            name="flow_records",
            value_kind="array",
            element_kind="string",
            missing_behavior="invalid",
            none_behavior="invalid",
            section_strategy="array",
            range_unit="elements",
            ordinary_content_format="json-array-page",
            oversized_content_format="json-element-fragment",
        ),
        "warnings": RecipeSectionDef(
            name="warnings",
            value_kind="array",
            element_kind="string",
            missing_behavior="default",
            none_behavior="invalid",
            section_strategy="array",
            range_unit="elements",
            ordinary_content_format="json-array-page",
            oversized_content_format="json-element-fragment",
            has_default=True,
            default_value=(),
        ),
    },
)

DYNAMIC_RECIPE_SECTION_DEF = RecipeSectionDef(
    name="post_prune_step",
    value_kind="string",
    element_kind=None,
    missing_behavior="absent",
    none_behavior="invalid",
    section_strategy="raw",
    range_unit="utf8-bytes",
    ordinary_content_format="raw-text",
    oversized_content_format=None,
)

RECIPE_SECTION_PAGINATION_VERSION = 2
_RECIPE_SECTION_CANONICAL_JSON_ENSURE_ASCII = False
_RECIPE_SECTION_CANONICAL_JSON_SEPARATORS = (",", ":")
_RECIPE_SECTION_CANONICAL_JSON_SORT_KEYS = True
_RECIPE_SECTION_DIGEST_DOMAINS: Mapping[str, str] = MappingProxyType(
    {
        "raw_section": "autoskillit.recipe-section.raw.v1",
        "structured_section": "autoskillit.recipe-section.structured.v1",
        "element": "autoskillit.recipe-section.element.v1",
        "plan": "autoskillit.recipe-section.plan.v1",
    }
)


def _qualified_registry_digest(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


RECIPE_SECTION_REGISTRY_DIGEST = _qualified_registry_digest(
    {name: asdict(definition) for name, definition in sorted(RECIPE_SECTION_REGISTRY.items())}
    | {"$dynamic": asdict(DYNAMIC_RECIPE_SECTION_DEF)}
)


@dataclass(frozen=True, slots=True)
class RecipeSectionContentFormatDef:
    range_fields: tuple[str, ...]
    reconstruction: str


RECIPE_SECTION_CONTENT_FORMAT_REGISTRY: Mapping[str, RecipeSectionContentFormatDef] = (
    MappingProxyType(
        {
            "raw-text": RecipeSectionContentFormatDef(
                range_fields=("byte_start", "byte_end", "byte_total"),
                reconstruction="concatenate-content",
            ),
            "json-array-page": RecipeSectionContentFormatDef(
                range_fields=("element_start", "element_end", "element_total"),
                reconstruction="json-load-each-and-extend",
            ),
            "json-scalar-page": RecipeSectionContentFormatDef(
                range_fields=("scalar_byte_start", "scalar_byte_end", "scalar_byte_total"),
                reconstruction="json-load-each-and-concatenate-strings",
            ),
            "json-element-fragment": RecipeSectionContentFormatDef(
                range_fields=(
                    "element_index",
                    "element_sha256",
                    "fragment_index",
                    "fragment_count",
                    "fragment_byte_start",
                    "fragment_byte_end",
                    "fragment_byte_total",
                ),
                reconstruction="json-load-fragments-concatenate-verify-and-json-load",
            ),
        }
    )
)


def _recipe_section_policy_definitions() -> tuple[RecipeSectionDef, ...]:
    return (*RECIPE_SECTION_REGISTRY.values(), DYNAMIC_RECIPE_SECTION_DEF)


def _declared_recipe_section_content_formats() -> tuple[str, ...]:
    formats = {
        content_format
        for definition in _recipe_section_policy_definitions()
        for content_format in (
            definition.ordinary_content_format,
            definition.oversized_content_format,
        )
        if content_format is not None
    }
    if formats != RECIPE_SECTION_CONTENT_FORMAT_REGISTRY.keys():
        raise ValueError("recipe section definitions and content-format metadata must agree")
    return tuple(sorted(formats))


_DECLARED_RECIPE_SECTION_CONTENT_FORMATS = _declared_recipe_section_content_formats()

_RECIPE_SECTION_PAGINATION_POLICY = {
    "version": RECIPE_SECTION_PAGINATION_VERSION,
    "canonical_json": {
        "ensure_ascii": _RECIPE_SECTION_CANONICAL_JSON_ENSURE_ASCII,
        "separators": list(_RECIPE_SECTION_CANONICAL_JSON_SEPARATORS),
        "sort_keys": _RECIPE_SECTION_CANONICAL_JSON_SORT_KEYS,
    },
    "success_fields": [
        "success",
        "pagination_version",
        "section_registry_sha256",
        "section",
        "content_format",
        "content",
        "part",
        "total_parts",
        "has_more",
        "next_part",
        "section_sha256",
        "page_plan_sha256",
        "payload_sha256",
        "body_sha256",
    ],
    "optional_fields": {"next_part": "omit_on_terminal"},
    "content_formats": {
        content_format: list(RECIPE_SECTION_CONTENT_FORMAT_REGISTRY[content_format].range_fields)
        for content_format in _DECLARED_RECIPE_SECTION_CONTENT_FORMATS
    },
    "range_units": {
        definition.section_strategy: definition.range_unit
        for definition in _recipe_section_policy_definitions()
    },
    "digest_domains": dict(_RECIPE_SECTION_DIGEST_DOMAINS),
    "reconstruction": {
        content_format: RECIPE_SECTION_CONTENT_FORMAT_REGISTRY[content_format].reconstruction
        for content_format in _DECLARED_RECIPE_SECTION_CONTENT_FORMATS
    },
}
RECIPE_SECTION_PAGINATION_POLICY_DIGEST = _qualified_registry_digest(
    _RECIPE_SECTION_PAGINATION_POLICY
)

RECIPE_SECTION_MANDATORY_FAILURE_CODES: tuple[str, ...] = (
    "invalid_recipe_artifact_identity",
    "invalid_recipe_initialization_identity",
    "invalid_recipe_page_plan_identity",
    "invalid_recipe_section_continuation",
    "invalid_recipe_section_part",
    "recipe_artifact_identity_required",
    "recipe_artifact_parse_failed",
    "recipe_artifact_schema_mismatch",
    "recipe_artifact_unavailable",
    "recipe_section_bound_too_small",
    "recipe_section_cancelled",
    "recipe_section_internal_error",
    "recipe_section_pagination_nonconvergent",
    "recipe_section_serialization_failed",
    "section_not_found",
)


_RECIPE_SECTION_FAILURE_ENVELOPE_WITH_EMPTY_CODE_BYTES = len(b'{"error":"","success":false}')
RECIPE_SECTION_RESPONSE_FLOOR_BYTES = _RECIPE_SECTION_FAILURE_ENVELOPE_WITH_EMPTY_CODE_BYTES + max(
    len(code.encode("utf-8")) for code in RECIPE_SECTION_MANDATORY_FAILURE_CODES
)

RESPONSE_BACKSTOP_EXEMPTION_REGISTRY: Mapping[str, ResponseBackstopExemptionDef] = (
    MappingProxyType(
        {
            definition.response_exemption_tool: definition.response_exemption
            for definition in RECIPE_DELIVERY_SURFACE_REGISTRY.values()
            if definition.response_exemption_tool is not None
            and definition.response_exemption is not None
        }
    )
)


def _response_backstop_exemption_registry_digest() -> str:
    canonical = {
        tool_name: definition._asdict()
        for tool_name, definition in sorted(RESPONSE_BACKSTOP_EXEMPTION_REGISTRY.items())
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


RESPONSE_BACKSTOP_EXEMPTION_REGISTRY_DIGEST: str = _response_backstop_exemption_registry_digest()


class PackDef(NamedTuple):
    """Definition of a named skill pack with default visibility state."""

    default_enabled: bool
    description: str


class RecipePackDef(NamedTuple):
    """Definition of a named recipe pack with default visibility state."""

    default_enabled: bool
    description: str


PACK_REGISTRY: dict[str, PackDef] = {
    "kitchen-core": PackDef(True, "Core kitchen orchestration tools"),
    "github": PackDef(True, "GitHub issue and PR tools"),
    "ci": PackDef(True, "CI polling and merge queue tools"),
    "clone": PackDef(True, "Clone-based run isolation tools"),
    "telemetry": PackDef(True, "Token, timing, and quota reporting"),
    "arch-lens": PackDef(True, "Architecture diagram lenses"),
    "audit": PackDef(True, "Codebase audit skills"),
    "research": PackDef(False, "Research recipe and experiment skills"),
    "exp-lens": PackDef(False, "Experimental design audit lenses"),
    "vis-lens": PackDef(False, "Visualization planning lenses"),
    "audit-pipeline": PackDef(False, "Audit pipeline internals (recipe-dispatched only)"),
}

CATEGORY_TAGS: frozenset[str] = frozenset(PACK_REGISTRY.keys())

RECIPE_PACK_REGISTRY: dict[str, RecipePackDef] = {
    "implementation-family": RecipePackDef(True, "Implementation and refactoring recipes"),
    "research-family": RecipePackDef(False, "Research and exploration recipes"),
    "orchestration-family": RecipePackDef(True, "Campaign orchestration and automation"),
}

RECIPE_PACK_TAGS: frozenset[str] = frozenset(RECIPE_PACK_REGISTRY.keys())


class AgentPackDef(NamedTuple):
    """Definition of a named agent pack with default visibility state."""

    default_enabled: bool
    description: str


AGENT_PACK_REGISTRY: dict[str, AgentPackDef] = {
    "plan-review": AgentPackDef(False, "Adversarial plan review agents for make-plan and rectify"),
}

if any(k != k.lower() for k in AGENT_PACK_REGISTRY):
    raise AssertionError(
        "AGENT_PACK_REGISTRY keys must be lowercase. "
        f"Offending: {sorted(k for k in AGENT_PACK_REGISTRY if k != k.lower())}"
    )

CORE_PACKS: frozenset[str] = frozenset({"github", "ci", "clone", "telemetry"})

if any(k != k.lower() for k in PACK_REGISTRY):
    raise AssertionError(
        "PACK_REGISTRY keys must be lowercase. "
        f"Offending: {sorted(k for k in PACK_REGISTRY if k != k.lower())}"
    )
if any(k != k.lower() for k in RECIPE_PACK_REGISTRY):
    raise AssertionError(
        "RECIPE_PACK_REGISTRY keys must be lowercase. "
        f"Offending: {sorted(k for k in RECIPE_PACK_REGISTRY if k != k.lower())}"
    )

# Maps each MCP tool name to its functional category subset tags.
TOOL_SUBSET_TAGS: dict[str, frozenset[str]] = {
    "fetch_github_issue": frozenset({"github", "fleet-dispatch"}),
    "get_issue_title": frozenset({"github", "fleet-dispatch"}),
    "report_bug": frozenset({"github"}),
    "prepare_issue": frozenset({"github"}),
    "enrich_issues": frozenset({"github"}),
    "claim_issue": frozenset({"github"}),
    "release_issue": frozenset({"github"}),
    "get_pr_reviews": frozenset({"github"}),
    "post_pr_review": frozenset({"github"}),
    "bulk_close_issues": frozenset({"github"}),
    "check_pr_mergeable": frozenset({"github"}),
    "push_to_remote": frozenset({"github"}),
    "create_unique_branch": frozenset({"github"}),
    "set_commit_status": frozenset({"github"}),
    "claim_and_resolve_issue": frozenset({"github"}),
    "create_and_publish_branch": frozenset({"github"}),
    "wait_for_ci": frozenset({"ci"}),
    "wait_for_merge_queue": frozenset({"ci"}),
    "check_repo_merge_state": frozenset({"ci"}),
    "toggle_auto_merge": frozenset({"ci"}),
    "enqueue_pr": frozenset({"ci"}),
    "get_ci_status": frozenset({"ci"}),
    "clone_repo": frozenset({"clone"}),
    "remove_clone": frozenset({"clone"}),
    "register_clone_status": frozenset({"clone"}),
    "batch_cleanup_clones": frozenset({"clone", "fleet"}),
    "bootstrap_clone": frozenset({"clone"}),
    "get_token_summary": frozenset({"kitchen-core", "telemetry", "fleet"}),
    "get_timing_summary": frozenset({"kitchen-core", "telemetry", "fleet"}),
    "write_telemetry_files": frozenset({"kitchen-core", "telemetry"}),
    "get_quota_events": frozenset({"kitchen-core", "telemetry", "fleet"}),
    "analyze_tool_sequences": frozenset({"kitchen-core", "telemetry"}),
    "run_cmd": frozenset({"kitchen-core"}),
    "run_python": frozenset({"kitchen-core"}),
    "run_skill": frozenset({"kitchen-core"}),
    "test_check": frozenset({"kitchen-core"}),
    "reset_test_dir": frozenset({"kitchen-core"}),
    "reset_workspace": frozenset({"kitchen-core"}),
    "classify_fix": frozenset({"kitchen-core"}),
    "commit_files": frozenset({"kitchen-core"}),
    "write_audit_semantic_result": frozenset({"kitchen-core"}),
    "write_standalone_audit_evidence": frozenset({"kitchen-core"}),
    "write_audit_disposition_bundle": frozenset({"kitchen-core"}),
    "list_recipes": frozenset({"kitchen-core", "fleet-dispatch"}),
    "load_recipe": frozenset({"kitchen-core", "fleet-dispatch"}),
    "validate_recipe": frozenset({"kitchen-core"}),
    "migrate_recipe": frozenset({"kitchen-core"}),
    "kitchen_status": frozenset({"kitchen-core"}),
    "read_db": frozenset({"kitchen-core"}),
    "get_pipeline_report": frozenset({"kitchen-core", "fleet"}),
    "dispatch_food_truck": frozenset({"kitchen-core", "fleet"}),
    "record_gate_dispatch": frozenset({"kitchen-core", "fleet"}),
    "reset_dispatch": frozenset({"kitchen-core", "fleet"}),
    "merge_worktree": frozenset({"kitchen-core"}),
    "unlock_agent_pack": frozenset({"kitchen-core"}),
    "record_pipeline_step": frozenset({"kitchen-core"}),
    "get_recipe_section": frozenset({"kitchen-core"}),
    "complete_recipe_initialization": frozenset({"kitchen-core"}),
}

ALL_VISIBILITY_TAGS: frozenset[str] = frozenset(
    {"kitchen", "headless", "fleet", "fleet-dispatch", "kitchen-core", "plan-review"}
)

if not TOOL_SUBSET_TAGS:
    raise RuntimeError("TOOL_SUBSET_TAGS is empty — cannot validate ALL_VISIBILITY_TAGS coverage")
_all_tool_tags = {tag for tags in TOOL_SUBSET_TAGS.values() for tag in tags}
_non_category_tool_tags = _all_tool_tags - CATEGORY_TAGS
if not _non_category_tool_tags <= ALL_VISIBILITY_TAGS:
    _missing = _non_category_tool_tags - ALL_VISIBILITY_TAGS
    raise RuntimeError(
        f"ALL_VISIBILITY_TAGS is missing non-category tags found in TOOL_SUBSET_TAGS: "
        f"{sorted(_missing)}. Add the missing tags to ALL_VISIBILITY_TAGS."
    )


@dataclass(frozen=True, slots=True)
class SkillCapabilityDef:
    """A capability that a skill may require from its execution backend."""

    description: str
    codex_status: Literal["works-as-is", "degraded", "fix-required", "not-applicable"]
    allowed_execution_roles: frozenset[SkillExecutionRole]
    required_sandbox_overrides: frozenset[str] = frozenset()
    worker_routable: bool = False
    # Name of a Boolean field on `BackendCapabilities` that must be True
    # on the dispatch-target backend for this capability to be feasible.
    # When set, `check_hard_capability_feasibility()` rejects the dispatch
    # at both admission (`_check_dispatch_feasibility`) and dispatch-time
    # (`_check_backend_compat`) gates if the backend's value is falsy —
    # independent of explicit backend pinning (REQ-RES-001).
    #
    # `required_recipe_ingredient` drives the corresponding soft,
    # recipe-level gate. `CAPABILITY_INGREDIENT_MAP` is derived from this
    # definition so admission and dispatch cannot drift apart.
    required_backend_property: str | None = None
    required_recipe_ingredient: str | None = None

    @property
    def required_backends(self) -> frozenset[str]:
        # worker_routable=True → reroute (REROUTE), not reject: required_backends must be empty
        # so the compat gate doesn't fire for routable capabilities.
        if self.codex_status == "not-applicable" and not self.worker_routable:
            return frozenset({AGENT_BACKEND_CLAUDE_CODE})
        return frozenset()


class HardCapabilityMismatch(NamedTuple):
    """A required backend capability property whose value is unsatisfied."""

    capability: str
    property_name: str
    actual_value: object


# Semantics divergence: fix-required has different enforcement in HOOK_REGISTRY vs
# SKILL_CAPABILITY_REGISTRY. In HOOK_REGISTRY, fix-required triggers _check_backend_compat
# to block dispatch on backends whose applicable_guards don't cover the hook's scripts.
# In SKILL_CAPABILITY_REGISTRY, fix-required is advisory only — it does NOT block dispatch
# (required_backends returns frozenset() for all non-not-applicable statuses). Skills
# declaring fix-required capabilities (github_api_write) are still Codex-dispatchable
# because the capability is documentary about the feature being incomplete, not a hard
# blocker. agent_subagent / agent_model / cross_skill_ref are routed via worker_routable
# rather than fix-required. Do not conflate the two registries' semantics.
_ALL_SKILL_EXECUTION_ROLES = frozenset(SkillExecutionRole)

SKILL_CAPABILITY_REGISTRY: dict[str, SkillCapabilityDef] = {
    "agent_subagent": SkillCapabilityDef(
        description="Agent(subagent_type=...) tool — delegates to specialized subagent",
        codex_status="not-applicable",
        allowed_execution_roles=_ALL_SKILL_EXECUTION_ROLES,
        worker_routable=True,
    ),
    "agent_model": SkillCapabilityDef(
        description="Agent(model=...) tool — spawns model-specific subagent",
        codex_status="not-applicable",
        allowed_execution_roles=_ALL_SKILL_EXECUTION_ROLES,
        worker_routable=True,
    ),
    "open_kitchen": SkillCapabilityDef(
        description="open_kitchen / close_kitchen lifecycle tools",
        codex_status="not-applicable",
        allowed_execution_roles=_ALL_SKILL_EXECUTION_ROLES,
    ),
    "run_skill": SkillCapabilityDef(
        description="run_skill MCP tool call (headless session dispatch)",
        codex_status="works-as-is",
        allowed_execution_roles=frozenset({SkillExecutionRole.ORCHESTRATOR}),
    ),
    "test_check": SkillCapabilityDef(
        description="test_check MCP tool (headless test runner)",
        codex_status="works-as-is",
        allowed_execution_roles=_ALL_SKILL_EXECUTION_ROLES,
    ),
    "claude_dir": SkillCapabilityDef(
        description="Reads/writes .claude/ directory structure",
        codex_status="works-as-is",
        allowed_execution_roles=_ALL_SKILL_EXECUTION_ROLES,
    ),
    "cross_skill_ref": SkillCapabilityDef(
        description="Cross-skill /autoskillit: invocation via Skill tool",
        codex_status="not-applicable",
        allowed_execution_roles=_ALL_SKILL_EXECUTION_ROLES,
        worker_routable=True,
    ),
    "commit_files": SkillCapabilityDef(
        description="commit_files MCP tool — server-side git stage/commit",
        codex_status="works-as-is",
        allowed_execution_roles=_ALL_SKILL_EXECUTION_ROLES,
    ),
    "write_audit_semantic_result": SkillCapabilityDef(
        description="Typed child-semantic submission through a server reservation",
        codex_status="works-as-is",
        allowed_execution_roles=_ALL_SKILL_EXECUTION_ROLES,
    ),
    "write_standalone_audit_evidence": SkillCapabilityDef(
        description="Typed non-authoritative standalone audit evidence writer",
        codex_status="works-as-is",
        allowed_execution_roles=_ALL_SKILL_EXECUTION_ROLES,
    ),
    "write_audit_disposition_bundle": SkillCapabilityDef(
        description="Verified-copy disposition and association transaction",
        codex_status="works-as-is",
        allowed_execution_roles=_ALL_SKILL_EXECUTION_ROLES,
    ),
    "git_metadata_write": SkillCapabilityDef(
        description=(
            "Requires .git/ metadata write access (git commit, git rebase, "
            "git worktree add, git checkout -b)"
        ),
        codex_status="not-applicable",
        allowed_execution_roles=_ALL_SKILL_EXECUTION_ROLES,
        worker_routable=True,
        required_backend_property="git_metadata_writable",
        required_recipe_ingredient="backend_supports_git_write",
    ),
    "github_api_write": SkillCapabilityDef(
        description=(
            "Skill makes outbound GitHub API write calls (gh pr review, gh api --method POST, "
            "gh pr create, gh pr merge, gh issue create/edit/close, etc.) that require network "
            "access. On Codex workers, enables network_access=true in the workspace-write sandbox."
        ),
        codex_status="fix-required",
        allowed_execution_roles=_ALL_SKILL_EXECUTION_ROLES,
        required_sandbox_overrides=frozenset({"sandbox_workspace_write.network_access=true"}),
    ),
}


def validate_skill_capability_roles(
    uses_capabilities: frozenset[str],
    execution_role: SkillExecutionRole,
) -> None:
    """Reject unknown capabilities and declarations not owned by ``execution_role``."""
    unknown = uses_capabilities - SKILL_CAPABILITY_REGISTRY.keys()
    if unknown:
        raise SkillContractError(
            f"unknown skill capabilities for {execution_role.value}: {sorted(unknown)}"
        )

    incompatible = sorted(
        capability
        for capability in uses_capabilities
        if execution_role not in SKILL_CAPABILITY_REGISTRY[capability].allowed_execution_roles
    )
    if incompatible:
        raise SkillContractError(
            f"execution role {execution_role.value!r} cannot declare capabilities {incompatible}"
        )


_VALID_CODEX_STATUSES = {"works-as-is", "degraded", "fix-required", "not-applicable"}
for _cap_name, _cap_def in SKILL_CAPABILITY_REGISTRY.items():
    if _cap_def.codex_status not in _VALID_CODEX_STATUSES:
        raise RuntimeError(
            f"SKILL_CAPABILITY_REGISTRY[{_cap_name!r}].codex_status="
            f"{_cap_def.codex_status!r} is not valid. "
            f"Must be one of {sorted(_VALID_CODEX_STATUSES)}."
        )
    if (
        not _cap_def.allowed_execution_roles
        or not _cap_def.allowed_execution_roles <= _ALL_SKILL_EXECUTION_ROLES
    ):
        raise RuntimeError(
            f"SKILL_CAPABILITY_REGISTRY[{_cap_name!r}].allowed_execution_roles "
            "must be a non-empty subset of SkillExecutionRole."
        )

# Boot-time validation: every `required_backend_property` must name a real
# field on `BackendCapabilities`. Catches typos in the registry at import
# time rather than at first dispatch. The recipe-level ingredient map is
# derived from the same capability definitions in `_type_constants.py`.
_BACKEND_CAPABILITIES_FIELDS = {f.name for f in fields(BackendCapabilities)}
for _cap_name, _cap_def in SKILL_CAPABILITY_REGISTRY.items():
    if (_cap_def.required_backend_property is None) != (
        _cap_def.required_recipe_ingredient is None
    ):
        raise RuntimeError(
            f"SKILL_CAPABILITY_REGISTRY[{_cap_name!r}] must define both "
            "required_backend_property and required_recipe_ingredient, or neither."
        )
    if (
        _cap_def.required_backend_property is not None
        and _cap_def.required_backend_property not in _BACKEND_CAPABILITIES_FIELDS
    ):
        raise RuntimeError(
            f"SKILL_CAPABILITY_REGISTRY[{_cap_name!r}].required_backend_property="
            f"{_cap_def.required_backend_property!r} is not a field on BackendCapabilities."
        )


def describe_capability_mismatches(
    mismatches: tuple[HardCapabilityMismatch, ...] | list[HardCapabilityMismatch],
) -> str:
    """Format capability mismatches into a human-readable string."""
    parts = []
    for m in mismatches:
        parts.append(
            f"{m.property_name}=True required (via '{m.capability}') "
            f"but backend has {m.property_name}={m.actual_value!r}"
        )
    return "; ".join(parts)


def unsatisfied_backend_capabilities(
    uses_capabilities: frozenset[str],
    backend_capabilities: BackendCapabilities,
) -> tuple[HardCapabilityMismatch, ...]:
    """Return hard capability requirements not satisfied by a backend."""
    mismatches: list[HardCapabilityMismatch] = []
    for capability in sorted(uses_capabilities):
        capability_def = SKILL_CAPABILITY_REGISTRY.get(capability)
        if capability_def is None or capability_def.required_backend_property is None:
            continue
        property_name = capability_def.required_backend_property
        actual_value = getattr(backend_capabilities, property_name, None)
        if not actual_value:
            mismatches.append(HardCapabilityMismatch(capability, property_name, actual_value))
    return tuple(mismatches)
