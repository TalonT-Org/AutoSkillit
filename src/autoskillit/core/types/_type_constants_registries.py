"""Tool registries, pack registries, tool-to-tag mappings, visibility tags.

Zero autoskillit imports; sibling imports remain within the IL-0 type package.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal, NamedTuple

from ._type_enums import ExplorationFailureCode, FleetErrorCode, SkillExecutionRole
from ._type_exceptions import SkillContractError
from ._type_exploration import BrokerAuthorityStatus

# Re-export recipe-section types from the canonical shard through this facade.
# The ``X as X`` alias pattern is intentional: it marks each name as an explicit
# re-export so ruff's F401 (unused-import) check treats these as facade passes
# rather than unused imports, without forcing them into __all__ (which would
# conflict with the identity-preservation invariant that the test
# ``test_recipe_section_facade_preserves_identity_and_export_ownership``
# enforces via ``assert name not in legacy_mod.__all__``).
from ._type_recipe_sections import (
    DYNAMIC_RECIPE_SECTION_DEF as DYNAMIC_RECIPE_SECTION_DEF,
)
from ._type_recipe_sections import (
    RECIPE_SECTION_CONTENT_FORMAT_REGISTRY as RECIPE_SECTION_CONTENT_FORMAT_REGISTRY,
)
from ._type_recipe_sections import (
    RECIPE_SECTION_MANDATORY_FAILURE_CODES as RECIPE_SECTION_MANDATORY_FAILURE_CODES,
)
from ._type_recipe_sections import (
    RECIPE_SECTION_PAGINATION_POLICY_DIGEST as RECIPE_SECTION_PAGINATION_POLICY_DIGEST,
)
from ._type_recipe_sections import (
    RECIPE_SECTION_PAGINATION_VERSION as RECIPE_SECTION_PAGINATION_VERSION,
)
from ._type_recipe_sections import (
    RECIPE_SECTION_REGISTRY as RECIPE_SECTION_REGISTRY,
)
from ._type_recipe_sections import (
    RECIPE_SECTION_REGISTRY_DIGEST as RECIPE_SECTION_REGISTRY_DIGEST,
)
from ._type_recipe_sections import (
    RECIPE_SECTION_RESPONSE_FLOOR_BYTES as RECIPE_SECTION_RESPONSE_FLOOR_BYTES,
)
from ._type_recipe_sections import (
    RecipeSectionContentFormatDef as RecipeSectionContentFormatDef,
)
from ._type_recipe_sections import (
    RecipeSectionDef as RecipeSectionDef,
)

__all__ = [
    "ANNOTATION_HARD_CAP_CHARS",
    "CLAUDE_ANNOTATION_SUPPORT_MIN_VERSION",
    "CLAUDE_DEFAULT_CLIENT_RESULT_TOKENS",
    "CLAUDE_INJECTED_CLIENT_RESULT_TOKENS",
    "CONSERVATIVE_GATE_HEADROOM_DENOMINATOR",
    "CONSERVATIVE_GATE_HEADROOM_NUMERATOR",
    "PIPELINE_FORBIDDEN_TOOLS",
    "SKILL_TOOLS",
    "GATED_TOOLS",
    "HEADLESS_TOOLS",
    "EVIDENCE_READER_TOOLS",
    "FLEET_TOOLS",
    "FLEET_DISPATCH_TOOLS",
    "FLEET_MENU_TOOLS",
    "EXPLORATION_TOOLS",
    "KITCHEN_GATED_TOOLS",
    "FLEET_ERROR_CODES",
    "EXPLORATION_FAILURE_CODES",
    "BROKER_AUTHORITY_STATUSES",
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
    "RECIPE_RESPONSE_MAX_UTF8_BYTES",
    "RECIPE_RESPONSE_DEFAULT_BYTES",
    "CONSERVATIVE_RESULT_TOKEN_FLOOR",
    "ExecutionInstallSiteDef",
    "RECIPE_EXECUTION_INSTALL_SITE_REGISTRY",
    "RECIPE_EXECUTION_INSTALL_SITE_REGISTRY_DIGEST",
    "SkillCapabilityDef",
    "SKILL_CAPABILITY_REGISTRY",
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
        "run_fixed_batch",
        "read_fixed_batch_result",
        "complete_run_skill_result",
        "recover_run_skill_result",
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
        "submit_exploration_query",
        "get_exploration_page",
        "resume_exploration_context",
        "inspect_session_logs",
        "read_authorized_artifact",
        "get_authorized_artifact_page",
    }
)

HEADLESS_TOOLS: frozenset[str] = frozenset(
    {
        "delegate_evidence_reader",
        "test_check",
        "unlock_agent_pack",
        "commit_files",
        "post_pr_review",
        "write_audit_semantic_result",
        "write_standalone_audit_evidence",
        "write_audit_disposition_bundle",
    }
)

EVIDENCE_READER_TOOLS: frozenset[str] = frozenset(
    {"read_authorized_artifact", "get_authorized_artifact_page"}
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

EXPLORATION_FAILURE_CODES: frozenset[str] = frozenset(ExplorationFailureCode)

BROKER_AUTHORITY_STATUSES: frozenset[str] = frozenset(BrokerAuthorityStatus)

FREE_RANGE_TOOLS: frozenset[str] = frozenset(
    {
        "open_kitchen",
        "close_kitchen",
        "disable_quota_guard",
        "enable_exploration",
        "reload_session",
        "configure_fleet",
        "configure_order",
        "lock_ingredients",  # NEW (#3357)
        "declare_join_batch",  # NEW (REQ-JOIN-001 — REQ-JOIN-002, #4575)
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


RECIPE_RESPONSE_MAX_UTF8_BYTES = 195_000
RECIPE_RESPONSE_DEFAULT_BYTES = 90_000

# Conservative fallback for the general-output token limit when no backend
# capability is resolvable. Matches BackendCapabilities.unnegotiated_tool_result_token_limit's
# dataclass default — the smallest registered backend bound (Codex code-mode).
CONSERVATIVE_RESULT_TOKEN_FLOOR = 10_000

# Client-gate constants. Binary-verified on Claude Code CLI v2.1.220.
# Source: inspection of the installed CLI binary's MCP result handling.
CLAUDE_DEFAULT_CLIENT_RESULT_TOKENS = 25_000
CLAUDE_INJECTED_CLIENT_RESULT_TOKENS = 50_000
ANNOTATION_HARD_CAP_CHARS = 500_000

# Conservative headroom fraction applied to client token gates when
# deriving the server's admission threshold. Reserves 7% below the
# nominal gate to account for the gap between the 1:1 conservative
# byte-to-token estimate (CONSERVATIVE_ADMISSION_POLICY) and actual
# tokenization — a 46K-byte payload may tokenize to slightly more than
# 46K tokens under some tokenizers, so the 93% multiplier ensures the
# server never admits a payload that risks spilling at the client gate.
CONSERVATIVE_GATE_HEADROOM_NUMERATOR = 93
CONSERVATIVE_GATE_HEADROOM_DENOMINATOR = 100

# The minimum Claude Code CLI version that supports the
# ``anthropic/maxResultSizeChars`` tool-result annotation. Below this
# version, annotation metadata is stripped and the tool result is gated
# purely by ``MAX_MCP_OUTPUT_TOKENS``.
CLAUDE_ANNOTATION_SUPPORT_MIN_VERSION = "2.1.91"

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
                max_chars=RECIPE_RESPONSE_MAX_UTF8_BYTES,
                max_utf8_bytes=RECIPE_RESPONSE_MAX_UTF8_BYTES,
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
                max_chars=RECIPE_RESPONSE_MAX_UTF8_BYTES,
                max_utf8_bytes=RECIPE_RESPONSE_MAX_UTF8_BYTES,
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
            delivering_module="src/autoskillit/server/_recipe_artifact.py",
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


RESPONSE_BACKSTOP_EXEMPTION_REGISTRY: Mapping[str, ResponseBackstopExemptionDef] = (
    MappingProxyType(
        {
            "get_recipe_section": ResponseBackstopExemptionDef(
                max_chars=RECIPE_RESPONSE_MAX_UTF8_BYTES,
                max_utf8_bytes=RECIPE_RESPONSE_MAX_UTF8_BYTES,
                measurement_id="bundled-recipes-all-modes-2026-08-09/get-recipe-section",
            ),
            **{
                definition.response_exemption_tool: definition.response_exemption
                for definition in RECIPE_DELIVERY_SURFACE_REGISTRY.values()
                if definition.response_exemption_tool is not None
                and definition.response_exemption is not None
            },
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
    "exploration": PackDef(False, "Bounded specialized repository exploration tools"),
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
    "run_fixed_batch": frozenset({"kitchen-core"}),
    "read_fixed_batch_result": frozenset({"kitchen-core"}),
    "complete_run_skill_result": frozenset({"kitchen-core"}),
    "recover_run_skill_result": frozenset({"kitchen-core"}),
    "test_check": frozenset({"kitchen-core"}),
    "reset_test_dir": frozenset({"kitchen-core"}),
    "reset_workspace": frozenset({"kitchen-core"}),
    "classify_fix": frozenset({"kitchen-core"}),
    "commit_files": frozenset({"kitchen-core"}),
    "write_audit_semantic_result": frozenset({"kitchen-core"}),
    "write_standalone_audit_evidence": frozenset({"kitchen-core"}),
    "write_audit_disposition_bundle": frozenset({"kitchen-core"}),
    "delegate_evidence_reader": frozenset({"kitchen-core"}),
    "read_authorized_artifact": frozenset({"evidence-reader"}),
    "get_authorized_artifact_page": frozenset({"evidence-reader"}),
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
    "submit_exploration_query": frozenset({"exploration"}),
    "get_exploration_page": frozenset({"exploration"}),
    "resume_exploration_context": frozenset({"exploration"}),
    "inspect_session_logs": frozenset({"kitchen-core"}),
}

EXPLORATION_TOOLS: frozenset[str] = frozenset(
    name for name, tags in TOOL_SUBSET_TAGS.items() if "exploration" in tags
)
KITCHEN_GATED_TOOLS: frozenset[str] = (
    GATED_TOOLS - FLEET_TOOLS - FLEET_DISPATCH_TOOLS - EXPLORATION_TOOLS - EVIDENCE_READER_TOOLS
)

ALL_VISIBILITY_TAGS: frozenset[str] = frozenset(
    {
        "kitchen",
        "headless",
        "fleet",
        "fleet-dispatch",
        "kitchen-core",
        "plan-review",
        "exploration",
        "evidence-reader",
    }
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
    """A detectable skill behavior independent of backend routing authority."""

    description: str
    codex_status: Literal["works-as-is", "degraded", "fix-required", "not-applicable"]
    allowed_execution_roles: frozenset[SkillExecutionRole]
    required_sandbox_overrides: frozenset[str] = frozenset()


# Hook compatibility is enforced separately from this registry. A skill capability's
# codex_status is documentary and never selects a backend. Portable child, sibling, and
# git-write needs are declared through SkillSemanticPlan and are not entries in this
# lexical evidence registry.
_ALL_SKILL_EXECUTION_ROLES = frozenset(SkillExecutionRole)

SKILL_CAPABILITY_REGISTRY: dict[str, SkillCapabilityDef] = {
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
