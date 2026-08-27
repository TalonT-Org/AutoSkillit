"""Facade: re-exports every leaf config dataclass for backwards compatibility.

The implementation now lives in owner-bounded modules
(``_dataclasses_<concern>.py``). This facade exists so callers using
``from autoskillit.config._config_dataclasses import <Symbol>`` keep working
unchanged. ``from X import Y as Y`` re-exports preserve identity, so callers
that compare dataclass instances by identity (e.g. ``is`` checks on
``_COMMAND_UNSET``) keep matching across the facade boundary.

Symbol origin:
  - ``_dataclasses_diagnostics`` → DiagnosticsConfig, LinuxTracingConfig,
    LoggingConfig, McpResponseConfig, OutputBudgetConfig, TokenUsageConfig
  - ``_dataclasses_execution`` → QuotaGuardConfig, RunSkillConfig
  - ``_dataclasses_fleet`` → FleetConfig, ProcessTetherConfig, _MAX_CONCURRENT_DISPATCHES
  - ``_dataclasses_github`` → GitHubConfig, ReportBugConfig
  - ``_dataclasses_providers`` → AgentBackendConfig, CoreRunConfig, ProvidersConfig,
    ProviderProfileDef, RETIRED_PROFILE_KEYS
  - ``_dataclasses_shared`` → ConfigSchemaError, _METADATA_KEYS, _SECRETS_ONLY_KEYS
  - ``_dataclasses_surfaces`` → PacksConfig, SkillsConfig, SubsetsConfig,
    WorkspaceConfig, WorktreeSetupConfig
  - ``_dataclasses_test_gating`` → ClassifyFixConfig, ImplementGateConfig,
    ReadDbConfig, ResetWorkspaceConfig, SafetyConfig, TestCheckConfig, _COMMAND_UNSET
  - ``_dataclasses_workflow`` → BranchingConfig, CIConfig, MigrationConfig,
    PlanConfig, ReviewConfig
"""

from __future__ import annotations

from autoskillit.config._dataclasses_diagnostics import (
    DiagnosticsConfig as DiagnosticsConfig,
)
from autoskillit.config._dataclasses_diagnostics import (
    LinuxTracingConfig as LinuxTracingConfig,
)
from autoskillit.config._dataclasses_diagnostics import (
    LoggingConfig as LoggingConfig,
)
from autoskillit.config._dataclasses_diagnostics import (
    McpResponseConfig as McpResponseConfig,
)
from autoskillit.config._dataclasses_diagnostics import (
    OutputBudgetConfig as OutputBudgetConfig,
)
from autoskillit.config._dataclasses_diagnostics import (
    TokenUsageConfig as TokenUsageConfig,
)
from autoskillit.config._dataclasses_execution import (
    QuotaGuardConfig as QuotaGuardConfig,
)
from autoskillit.config._dataclasses_execution import (
    RunSkillConfig as RunSkillConfig,
)
from autoskillit.config._dataclasses_fleet import (
    _MAX_CONCURRENT_DISPATCHES as _MAX_CONCURRENT_DISPATCHES,
)
from autoskillit.config._dataclasses_fleet import (
    FleetConfig as FleetConfig,
)
from autoskillit.config._dataclasses_fleet import (
    ProcessTetherConfig as ProcessTetherConfig,
)
from autoskillit.config._dataclasses_github import (
    GitHubConfig as GitHubConfig,
)
from autoskillit.config._dataclasses_github import (
    ReportBugConfig as ReportBugConfig,
)
from autoskillit.config._dataclasses_providers import (
    RETIRED_PROFILE_KEYS as RETIRED_PROFILE_KEYS,
)
from autoskillit.config._dataclasses_providers import (
    AgentBackendConfig as AgentBackendConfig,
)
from autoskillit.config._dataclasses_providers import (
    CoreRunConfig as CoreRunConfig,
)
from autoskillit.config._dataclasses_providers import (
    ProviderProfileDef as ProviderProfileDef,
)
from autoskillit.config._dataclasses_providers import (
    ProvidersConfig as ProvidersConfig,
)
from autoskillit.config._dataclasses_shared import (
    _METADATA_KEYS as _METADATA_KEYS,
)
from autoskillit.config._dataclasses_shared import (
    _SECRETS_ONLY_KEYS as _SECRETS_ONLY_KEYS,
)
from autoskillit.config._dataclasses_shared import (
    ConfigSchemaError as ConfigSchemaError,
)
from autoskillit.config._dataclasses_surfaces import (
    PacksConfig as PacksConfig,
)
from autoskillit.config._dataclasses_surfaces import (
    SkillsConfig as SkillsConfig,
)
from autoskillit.config._dataclasses_surfaces import (
    SubsetsConfig as SubsetsConfig,
)
from autoskillit.config._dataclasses_surfaces import (
    WorkspaceConfig as WorkspaceConfig,
)
from autoskillit.config._dataclasses_surfaces import (
    WorktreeSetupConfig as WorktreeSetupConfig,
)
from autoskillit.config._dataclasses_test_gating import (
    _COMMAND_UNSET as _COMMAND_UNSET,
)
from autoskillit.config._dataclasses_test_gating import (
    ClassifyFixConfig as ClassifyFixConfig,
)
from autoskillit.config._dataclasses_test_gating import (
    ImplementGateConfig as ImplementGateConfig,
)
from autoskillit.config._dataclasses_test_gating import (
    ReadDbConfig as ReadDbConfig,
)
from autoskillit.config._dataclasses_test_gating import (
    ResetWorkspaceConfig as ResetWorkspaceConfig,
)
from autoskillit.config._dataclasses_test_gating import (
    SafetyConfig as SafetyConfig,
)
from autoskillit.config._dataclasses_test_gating import (
    TestCheckConfig as TestCheckConfig,
)
from autoskillit.config._dataclasses_workflow import (
    BranchingConfig as BranchingConfig,
)
from autoskillit.config._dataclasses_workflow import (
    CIConfig as CIConfig,
)
from autoskillit.config._dataclasses_workflow import (
    MigrationConfig as MigrationConfig,
)
from autoskillit.config._dataclasses_workflow import (
    PlanConfig as PlanConfig,
)
from autoskillit.config._dataclasses_workflow import (
    ReviewConfig as ReviewConfig,
)
from autoskillit.core import (
    DRY_WALKTHROUGH_VERIFIED_MARKER as DRY_WALKTHROUGH_VERIFIED_MARKER,
)
from autoskillit.core import (
    KNOWN_BACKEND_NAMES as KNOWN_BACKEND_NAMES,
)
from autoskillit.core import (
    LABEL_LIFECYCLE_REGISTRY as LABEL_LIFECYCLE_REGISTRY,
)
from autoskillit.core import (
    RECIPE_RESPONSE_DEFAULT_BYTES as RECIPE_RESPONSE_DEFAULT_BYTES,
)
from autoskillit.core import (
    RECIPE_RESPONSE_MAX_UTF8_BYTES as RECIPE_RESPONSE_MAX_UTF8_BYTES,
)
from autoskillit.core import (
    RECIPE_SECTION_RESPONSE_FLOOR_BYTES as RECIPE_SECTION_RESPONSE_FLOOR_BYTES,
)
from autoskillit.core import (
    IssueLabelState as IssueLabelState,
)
from autoskillit.core import (
    OutputFormat as OutputFormat,
)
from autoskillit.core import (
    Utf8ByteLimit as Utf8ByteLimit,
)

__all__ = [
    "AgentBackendConfig",
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
    "ReportBugConfig",
    "ResetWorkspaceConfig",
    "ReviewConfig",
    "RunSkillConfig",
    "SafetyConfig",
    "SkillsConfig",
    "SubsetsConfig",
    "TestCheckConfig",
    "TokenUsageConfig",
    "WorkspaceConfig",
    "WorktreeSetupConfig",
    "RETIRED_PROFILE_KEYS",
    # Transitive autoskillit.core re-exports preserved at the legacy path:
    "DRY_WALKTHROUGH_VERIFIED_MARKER",
    "IssueLabelState",
    "KNOWN_BACKEND_NAMES",
    "LABEL_LIFECYCLE_REGISTRY",
    "OutputFormat",
    "RECIPE_RESPONSE_DEFAULT_BYTES",
    "RECIPE_RESPONSE_MAX_UTF8_BYTES",
    "RECIPE_SECTION_RESPONSE_FLOOR_BYTES",
    "Utf8ByteLimit",
]
