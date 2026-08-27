"""Facade: thin re-export of every config-public symbol.

The actual implementations live in owner-bounded modules under
``autoskillit.config._<concern>``. This module preserves every prior import
path so callers using ``from autoskillit.config.settings import <Symbol>``
keep working unchanged. ``from X import Y as Y`` re-exports preserve identity,
so callers that ``monkeypatch.setitem`` on dicts like ``_YAML_KEY_ALIASES`` or
``_FIELD_OVERRIDES`` continue to see their mutations reflected in the
production code.
"""

from __future__ import annotations

from autoskillit.config._automation_config import (
    _UNSET as _UNSET,  # owned by _automation_config (its only consumer)
)
from autoskillit.config._automation_config import (
    AutomationConfig as AutomationConfig,
)
from autoskillit.config._coercion import (
    _FIELD_OVERRIDES as _FIELD_OVERRIDES,
)
from autoskillit.config._coercion import (
    _SECTION_BUILDERS as _SECTION_BUILDERS,
)
from autoskillit.config._coercion import (
    _SECTION_PREPROCESSORS as _SECTION_PREPROCESSORS,
)
from autoskillit.config._coercion import (
    _YAML_KEY_ALIASES as _YAML_KEY_ALIASES,
)
from autoskillit.config._coercion import (
    _build_subconfig as _build_subconfig,
)
from autoskillit.config._coercion import (
    _coerce_value as _coerce_value,
)
from autoskillit.config._coercion import (
    _field_defaults as _field_defaults,
)
from autoskillit.config._coercion import (
    _preprocess_agent_backend as _preprocess_agent_backend,
)
from autoskillit.config._coherence import (
    _CI_WATCH_DEFAULT as _CI_WATCH_DEFAULT,
)
from autoskillit.config._coherence import (
    _MERGE_QUEUE_DEFAULT as _MERGE_QUEUE_DEFAULT,
)
from autoskillit.config._coherence import (
    _MERGE_QUEUE_RECIPE_MAX as _MERGE_QUEUE_RECIPE_MAX,
)
from autoskillit.config._coherence import (
    _claude_mcp_timeout_coherence_gate as _claude_mcp_timeout_coherence_gate,
)
from autoskillit.config._coherence import (
    _codex_mcp_timeout_coherence_gate as _codex_mcp_timeout_coherence_gate,
)
from autoskillit.config._coherence import (
    _process_tether_coherence_gate as _process_tether_coherence_gate,
)
from autoskillit.config._coherence import (
    _timeout_coherence_gate as _timeout_coherence_gate,
)
from autoskillit.config._coherence import (
    compute_codex_mcp_tool_timeout as compute_codex_mcp_tool_timeout,
)
from autoskillit.config._config_dataclasses import (
    _COMMAND_UNSET as _COMMAND_UNSET,
)
from autoskillit.config._config_dataclasses import (
    _MAX_CONCURRENT_DISPATCHES as _MAX_CONCURRENT_DISPATCHES,
)
from autoskillit.config._config_dataclasses import (
    _METADATA_KEYS as _METADATA_KEYS,
)
from autoskillit.config._config_dataclasses import (
    _SECRETS_ONLY_KEYS as _SECRETS_ONLY_KEYS,
)
from autoskillit.config._config_dataclasses import (
    RETIRED_PROFILE_KEYS as RETIRED_PROFILE_KEYS,
)
from autoskillit.config._config_dataclasses import (
    AgentBackendConfig as AgentBackendConfig,
)
from autoskillit.config._config_dataclasses import (
    BranchingConfig as BranchingConfig,
)
from autoskillit.config._config_dataclasses import (
    CIConfig as CIConfig,
)
from autoskillit.config._config_dataclasses import (
    ClassifyFixConfig as ClassifyFixConfig,
)
from autoskillit.config._config_dataclasses import (
    ConfigSchemaError as ConfigSchemaError,
)
from autoskillit.config._config_dataclasses import (
    CoreRunConfig as CoreRunConfig,
)
from autoskillit.config._config_dataclasses import (
    DiagnosticsConfig as DiagnosticsConfig,
)
from autoskillit.config._config_dataclasses import (
    FleetConfig as FleetConfig,
)
from autoskillit.config._config_dataclasses import (
    GitHubConfig as GitHubConfig,
)
from autoskillit.config._config_dataclasses import (
    ImplementGateConfig as ImplementGateConfig,
)
from autoskillit.config._config_dataclasses import (
    LinuxTracingConfig as LinuxTracingConfig,
)
from autoskillit.config._config_dataclasses import (
    LoggingConfig as LoggingConfig,
)
from autoskillit.config._config_dataclasses import (
    McpResponseConfig as McpResponseConfig,
)
from autoskillit.config._config_dataclasses import (
    MigrationConfig as MigrationConfig,
)
from autoskillit.config._config_dataclasses import (
    OutputBudgetConfig as OutputBudgetConfig,
)
from autoskillit.config._config_dataclasses import (
    PacksConfig as PacksConfig,
)
from autoskillit.config._config_dataclasses import (
    PlanConfig as PlanConfig,
)
from autoskillit.config._config_dataclasses import (
    ProcessTetherConfig as ProcessTetherConfig,
)
from autoskillit.config._config_dataclasses import (
    ProviderProfileDef as ProviderProfileDef,
)
from autoskillit.config._config_dataclasses import (
    ProvidersConfig as ProvidersConfig,
)
from autoskillit.config._config_dataclasses import (
    QuotaGuardConfig as QuotaGuardConfig,
)
from autoskillit.config._config_dataclasses import (
    ReadDbConfig as ReadDbConfig,
)
from autoskillit.config._config_dataclasses import (
    ReportBugConfig as ReportBugConfig,
)
from autoskillit.config._config_dataclasses import (
    ResetWorkspaceConfig as ResetWorkspaceConfig,
)
from autoskillit.config._config_dataclasses import (
    ReviewConfig as ReviewConfig,
)
from autoskillit.config._config_dataclasses import (
    RunSkillConfig as RunSkillConfig,
)
from autoskillit.config._config_dataclasses import (
    SafetyConfig as SafetyConfig,
)
from autoskillit.config._config_dataclasses import (
    SkillsConfig as SkillsConfig,
)
from autoskillit.config._config_dataclasses import (
    SubsetsConfig as SubsetsConfig,
)
from autoskillit.config._config_dataclasses import (
    TestCheckConfig as TestCheckConfig,
)
from autoskillit.config._config_dataclasses import (
    TokenUsageConfig as TokenUsageConfig,
)
from autoskillit.config._config_dataclasses import (
    WorkspaceConfig as WorkspaceConfig,
)
from autoskillit.config._config_dataclasses import (
    WorktreeSetupConfig as WorktreeSetupConfig,
)
from autoskillit.config._config_loader import (
    _build_packs_config as _build_packs_config,
)
from autoskillit.config._config_loader import (
    _build_subsets_config as _build_subsets_config,
)
from autoskillit.config._config_loader import (
    _make_dynaconf as _make_dynaconf,
)
from autoskillit.config._config_loader import (
    _to_optional_commands as _to_optional_commands,
)
from autoskillit.config._config_loader import (
    load_config as load_config,
)
from autoskillit.config._retired_keys import (
    RETIRED_CONFIG_KEYS as RETIRED_CONFIG_KEYS,
)
from autoskillit.config._retired_keys import (
    RemappedConfigKey as RemappedConfigKey,
)
from autoskillit.config._retired_keys import (
    RetiredConfigKeyDef as RetiredConfigKeyDef,
)
from autoskillit.config._retired_keys import (
    remap_retired_keys as remap_retired_keys,
)
from autoskillit.config._validation import (
    _CONFIG_SCHEMA as _CONFIG_SCHEMA,
)
from autoskillit.config._validation import (
    _build_config_schema as _build_config_schema,
)
from autoskillit.config._validation import (
    validate_env_layer_keys as validate_env_layer_keys,
)
from autoskillit.config._validation import (
    validate_layer_keys as validate_layer_keys,
)
from autoskillit.config._writer import (
    write_config_layer as write_config_layer,
)

# Preserve transitive re-exports from autoskillit.core that the prior
# monolithic settings.py exposed at module scope. Callers using
# `from autoskillit.config.settings import <core_symbol>` keep working unchanged.
from autoskillit.core import (
    FEATURE_REGISTRY as FEATURE_REGISTRY,
)
from autoskillit.core import (
    FeatureLifecycle as FeatureLifecycle,
)
from autoskillit.core import (
    SkillVisibilitySpec as SkillVisibilitySpec,
)
from autoskillit.core import (
    atomic_write as atomic_write,
)
from autoskillit.core import (
    dump_yaml_str as dump_yaml_str,
)
from autoskillit.core import (
    is_dev_install as is_dev_install,
)
from autoskillit.core import (
    is_feature_enabled as is_feature_enabled,
)

__all__ = [
    "AgentBackendConfig",
    "AutomationConfig",
    "BranchingConfig",
    "CIConfig",
    "ClassifyFixConfig",
    "ConfigSchemaError",
    "CoreRunConfig",
    "DiagnosticsConfig",
    "FleetConfig",
    "GitHubConfig",
    "ImplementGateConfig",
    "LinuxTracingConfig",
    "LoggingConfig",
    "McpResponseConfig",
    "MigrationConfig",
    "OutputBudgetConfig",
    "PacksConfig",
    "PlanConfig",
    "ProcessTetherConfig",
    "ProvidersConfig",
    "ProviderProfileDef",
    "QuotaGuardConfig",
    "ReadDbConfig",
    "RemappedConfigKey",
    "ReportBugConfig",
    "ResetWorkspaceConfig",
    "RETIRED_CONFIG_KEYS",
    "RETIRED_PROFILE_KEYS",
    "RetiredConfigKeyDef",
    "ReviewConfig",
    "RunSkillConfig",
    "SafetyConfig",
    "SkillsConfig",
    "SubsetsConfig",
    "TestCheckConfig",
    "TokenUsageConfig",
    "WorkspaceConfig",
    "WorktreeSetupConfig",
    "compute_codex_mcp_tool_timeout",
    "load_config",
    "remap_retired_keys",
    "validate_env_layer_keys",
    "validate_layer_keys",
    "write_config_layer",
]
