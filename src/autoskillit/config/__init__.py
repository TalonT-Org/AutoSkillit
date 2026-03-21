"""config/ L1 package: configuration loading with layered YAML resolution.

Re-exports the full public surface of config.settings so callers can use
either `from autoskillit.config import AutomationConfig` or the explicit
`from autoskillit.config.settings import AutomationConfig`.
"""

from autoskillit.config.ingredient_defaults import resolve_ingredient_defaults
from autoskillit.config.settings import (
    AutomationConfig,
    BranchingConfig,
    CIConfig,
    ClassifyFixConfig,
    ConfigSchemaError,
    GitHubConfig,
    ImplementGateConfig,
    LinuxTracingConfig,
    LoggingConfig,
    McpResponseConfig,
    MigrationConfig,
    ModelConfig,
    QuotaGuardConfig,
    ReadDbConfig,
    ReportBugConfig,
    ResetWorkspaceConfig,
    RunSkillConfig,
    SafetyConfig,
    SkillsConfig,
    SubsetsConfig,
    TestCheckConfig,
    TokenUsageConfig,
    WorktreeSetupConfig,
    load_config,
)

__all__ = [
    "AutomationConfig",
    "BranchingConfig",
    "ConfigSchemaError",
    "CIConfig",
    "ClassifyFixConfig",
    "GitHubConfig",
    "ImplementGateConfig",
    "LinuxTracingConfig",
    "LoggingConfig",
    "McpResponseConfig",
    "MigrationConfig",
    "ModelConfig",
    "QuotaGuardConfig",
    "ReadDbConfig",
    "ReportBugConfig",
    "ResetWorkspaceConfig",
    "RunSkillConfig",
    "SafetyConfig",
    "SkillsConfig",
    "SubsetsConfig",
    "TestCheckConfig",
    "TokenUsageConfig",
    "WorktreeSetupConfig",
    "load_config",
    "resolve_ingredient_defaults",
]
