"""pipeline/ IL-1 package: audit log, durable accounting, gate policy, and ToolContext.

Re-exports the public pipeline service surface. Only pipeline/context.py
imports from config/; the remaining modules depend only on autoskillit.core.*.
"""

from autoskillit.core import (
    GATED_TOOLS,
    UNGATED_TOOLS,
    FailureRecord,
    fleet_error,
    is_protected_branch,
)
from autoskillit.pipeline.audit import (
    COMMAND_MAX_LEN,
    STDERR_MAX_LEN,
    DefaultAuditLog,
)
from autoskillit.pipeline.audit_admission_ledger import DefaultAuditAdmissionLedger
from autoskillit.pipeline.background import (
    DefaultBackgroundSupervisor,
    create_background_task,
    write_status,
)
from autoskillit.pipeline.context import ToolContext
from autoskillit.pipeline.context_admission_ledger import (
    DefaultContextAdmissionLedger,
)
from autoskillit.pipeline.exploration_context import (
    EXPLORATION_AUTHORITY_PATH_ENV,
    EXPLORATION_CAPABILITY_ENV,
    EXPLORATION_PRINCIPAL_ROLE,
    EXPLORATION_ROLE_ENV,
    EXPLORATION_SESSION_ENV,
    EXPLORER_INELIGIBLE_SESSION_TYPES,
    EXPLORER_ROLE_NAMES,
    CapabilityResolution,
    CapabilityResolutionStatus,
    ExplorationContext,
    ExplorationContextStoreProtocol,
    ExplorationLaunchBinding,
    OwnerBoundExplorationContextStore,
    is_explorer_binding_eligible,
)
from autoskillit.pipeline.gate import (
    DefaultGateState,
    gate_error_result,
    headless_error_result,
)
from autoskillit.pipeline.github_api_log import DefaultGitHubApiLog
from autoskillit.pipeline.kitchen_transition import (
    KITCHEN_EFFECT_RECIPE_SERVING,
    KITCHEN_EFFECT_RESPONSE_ENFORCEMENT,
    KitchenEffectPhase,
    KitchenEffectRecord,
    KitchenIntentConflict,
    KitchenOpenPhase,
    KitchenOpenState,
    KitchenRetryDisposition,
    KitchenTransitionToken,
    abort_kitchen_effect,
    advance_kitchen_phase,
    bind_kitchen_intent,
    canonical_kitchen_intent_fingerprint,
    claim_kitchen_request,
    closed_kitchen_open_state,
    commit_kitchen_response,
    confirm_kitchen_effect,
    kitchen_state_payload,
    mark_kitchen_effect_ambiguous,
    mark_kitchen_effect_degraded,
    new_kitchen_open_state,
    prepare_kitchen_response,
    release_kitchen_request,
    start_kitchen_effect,
    transition_abort,
    transition_ambiguous,
    transition_confirm,
    transition_degraded,
)
from autoskillit.pipeline.mcp_response import DefaultMcpResponseLog, McpResponseEntry
from autoskillit.pipeline.pr_gates import (
    is_ci_passing,
    is_review_passing,
    partition_prs,
)
from autoskillit.pipeline.recipe_initialization import (
    InitializingRecipe,
    NoActiveRecipe,
    ReadyRecipe,
    RecipeInitializationProgress,
    RecipeInitializationRequirement,
    RecipeInitializationState,
    initialization_is_complete,
    record_initialization_page,
    replace_ready_execution,
    start_recipe_initialization,
    transition_recipe_ready,
)
from autoskillit.pipeline.telemetry_fmt import TelemetryFormatter
from autoskillit.pipeline.timings import DefaultTimingLog, TimingEntry
from autoskillit.pipeline.tokens import DefaultTokenLog, TokenEntry, canonical_step_name

__all__ = [
    # branch_guard
    "is_protected_branch",
    # audit
    "DefaultAuditLog",
    "FailureRecord",
    "STDERR_MAX_LEN",
    "COMMAND_MAX_LEN",
    # mcp_response
    "DefaultMcpResponseLog",
    "McpResponseEntry",
    # timings
    "DefaultTimingLog",
    "TimingEntry",
    # tokens
    "DefaultTokenLog",
    "TokenEntry",
    "canonical_step_name",
    # gate
    "DefaultGateState",
    "GATED_TOOLS",
    "UNGATED_TOOLS",
    "fleet_error",
    "gate_error_result",
    "headless_error_result",
    # telemetry_fmt
    "TelemetryFormatter",
    # background
    "DefaultBackgroundSupervisor",
    "create_background_task",
    "write_status",
    # context
    "ToolContext",
    "CapabilityResolution",
    "CapabilityResolutionStatus",
    "EXPLORER_INELIGIBLE_SESSION_TYPES",
    "EXPLORER_ROLE_NAMES",
    "EXPLORATION_AUTHORITY_PATH_ENV",
    "EXPLORATION_CAPABILITY_ENV",
    "EXPLORATION_PRINCIPAL_ROLE",
    "EXPLORATION_ROLE_ENV",
    "EXPLORATION_SESSION_ENV",
    "ExplorationContext",
    "ExplorationContextStoreProtocol",
    "ExplorationLaunchBinding",
    "OwnerBoundExplorationContextStore",
    "is_explorer_binding_eligible",
    "DefaultAuditAdmissionLedger",
    "DefaultContextAdmissionLedger",
    # kitchen transition
    "KITCHEN_EFFECT_RECIPE_SERVING",
    "KITCHEN_EFFECT_RESPONSE_ENFORCEMENT",
    "KitchenEffectPhase",
    "KitchenEffectRecord",
    "KitchenIntentConflict",
    "KitchenOpenPhase",
    "KitchenOpenState",
    "KitchenRetryDisposition",
    "KitchenTransitionToken",
    "transition_abort",
    "transition_ambiguous",
    "transition_confirm",
    "transition_degraded",
    "abort_kitchen_effect",
    "advance_kitchen_phase",
    "bind_kitchen_intent",
    "canonical_kitchen_intent_fingerprint",
    "claim_kitchen_request",
    "closed_kitchen_open_state",
    "commit_kitchen_response",
    "confirm_kitchen_effect",
    "kitchen_state_payload",
    "mark_kitchen_effect_ambiguous",
    "mark_kitchen_effect_degraded",
    "new_kitchen_open_state",
    "prepare_kitchen_response",
    "release_kitchen_request",
    "start_kitchen_effect",
    # github_api_log
    "DefaultGitHubApiLog",
    # pr_gates
    "is_ci_passing",
    "is_review_passing",
    "partition_prs",
    # recipe initialization
    "InitializingRecipe",
    "NoActiveRecipe",
    "ReadyRecipe",
    "RecipeInitializationProgress",
    "RecipeInitializationRequirement",
    "RecipeInitializationState",
    "initialization_is_complete",
    "record_initialization_page",
    "replace_ready_execution",
    "start_recipe_initialization",
    "transition_recipe_ready",
]
