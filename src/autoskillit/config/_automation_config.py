"""Root AutomationConfig dataclass and the typed-from-Dynaconf loader.

Owns:
  - ``AutomationConfig`` (the root dataclass composed of every leaf sub-config).
  - ``_UNSET`` (the private sentinel ``object()`` instance used by
    ``_build_features_dict`` to detect absent vs None experimental_enabled).
  - ``skill_visibility_spec`` (projection of config-owned fields into the core
    workspace policy contract).
  - ``_build_features_dict`` (validation/coercion of the features section).
  - ``from_dynaconf`` (the typed-from-Dynaconf constructor; the entrypoint the
    loader calls).

``_UNSET`` is owned by THIS module because its only consumer
(``_build_features_dict``) lives here — moving it out would force a second
module to import a private sentinel it has no business touching.
"""

from __future__ import annotations

import dataclasses
import warnings
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from autoskillit.config._coercion import (
    _SECTION_BUILDERS,
    _SECTION_PREPROCESSORS,
    _build_subconfig,
)
from autoskillit.config._coherence import (
    _claude_mcp_timeout_coherence_gate,
    _codex_mcp_timeout_coherence_gate,
    _process_tether_coherence_gate,
    _timeout_coherence_gate,
)
from autoskillit.config._dataclasses_diagnostics import (
    DiagnosticsConfig,
    LinuxTracingConfig,
    LoggingConfig,
    McpResponseConfig,
    OutputBudgetConfig,
    TokenUsageConfig,
)
from autoskillit.config._dataclasses_errors import ConfigSchemaError
from autoskillit.config._dataclasses_execution import QuotaGuardConfig, RunSkillConfig
from autoskillit.config._dataclasses_fleet import FleetConfig, ProcessTetherConfig
from autoskillit.config._dataclasses_github import (
    GitHubConfig,
    ReportBugConfig,
)
from autoskillit.config._dataclasses_providers import (
    AgentBackendConfig,
    CoreRunConfig,
    ProvidersConfig,
)
from autoskillit.config._dataclasses_surfaces import (
    PacksConfig,
    SkillsConfig,
    SubsetsConfig,
    WorkspaceConfig,
    WorktreeSetupConfig,
)
from autoskillit.config._dataclasses_test_gating import (
    ClassifyFixConfig,
    ImplementGateConfig,
    ReadDbConfig,
    ResetWorkspaceConfig,
    SafetyConfig,
    TestCheckConfig,
)
from autoskillit.config._dataclasses_workflow import (
    BranchingConfig,
    CIConfig,
    MigrationConfig,
    PlanConfig,
    ReviewConfig,
)
from autoskillit.core import (
    FEATURE_REGISTRY,
    FeatureLifecycle,
    SkillVisibilitySpec,
    is_dev_install,
    is_feature_enabled,
)

if TYPE_CHECKING:
    from dynaconf import Dynaconf

_UNSET = object()


@dataclass
class AutomationConfig:
    """Root configuration dataclass for AutoSkillit.

    Schema contract: all direct fields (except ``features`` and
    ``experimental_enabled``) must use ``field(default_factory=<DataclassType>)``
    where the factory is a dataclass, or be registered in ``_SECTION_BUILDERS``
    for custom build logic. Any scalar or non-dataclass field not in
    ``_SECTION_BUILDERS`` will raise ``ConfigSchemaError`` at load time in
    ``from_dynaconf``.
    """

    test_check: TestCheckConfig = field(default_factory=TestCheckConfig)
    classify_fix: ClassifyFixConfig = field(default_factory=ClassifyFixConfig)
    reset_workspace: ResetWorkspaceConfig = field(default_factory=ResetWorkspaceConfig)
    implement_gate: ImplementGateConfig = field(default_factory=ImplementGateConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    read_db: ReadDbConfig = field(default_factory=ReadDbConfig)
    run_skill: RunSkillConfig = field(default_factory=RunSkillConfig)
    model: CoreRunConfig = field(default_factory=CoreRunConfig)
    worktree_setup: WorktreeSetupConfig = field(default_factory=WorktreeSetupConfig)
    migration: MigrationConfig = field(default_factory=MigrationConfig)
    token_usage: TokenUsageConfig = field(default_factory=TokenUsageConfig)
    quota_guard: QuotaGuardConfig = field(default_factory=QuotaGuardConfig)
    github: GitHubConfig = field(default_factory=GitHubConfig)
    report_bug: ReportBugConfig = field(default_factory=ReportBugConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    diagnostics: DiagnosticsConfig = field(default_factory=DiagnosticsConfig)
    linux_tracing: LinuxTracingConfig = field(default_factory=LinuxTracingConfig)
    mcp_response: McpResponseConfig = field(default_factory=McpResponseConfig)
    output_budget: OutputBudgetConfig = field(default_factory=OutputBudgetConfig)
    branching: BranchingConfig = field(default_factory=BranchingConfig)
    ci: CIConfig = field(default_factory=CIConfig)
    review: ReviewConfig = field(default_factory=ReviewConfig)
    plan: PlanConfig = field(default_factory=PlanConfig)
    skills: SkillsConfig = field(default_factory=SkillsConfig)
    subsets: SubsetsConfig = field(default_factory=SubsetsConfig)
    packs: PacksConfig = field(default_factory=PacksConfig)
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)
    fleet: FleetConfig = field(default_factory=FleetConfig)
    process_tether: ProcessTetherConfig = field(default_factory=ProcessTetherConfig)
    providers: ProvidersConfig = field(default_factory=ProvidersConfig)
    agent_backend: AgentBackendConfig = field(default_factory=AgentBackendConfig)
    features: dict[str, bool] = field(default_factory=dict)
    experimental_enabled: bool = False

    def skill_visibility_spec(self) -> SkillVisibilitySpec:
        """Project config-owned fields into the core workspace policy contract."""
        return SkillVisibilitySpec(
            disabled_categories=frozenset(self.subsets.disabled),
            custom_tags={
                tag: frozenset(skill_names)
                for tag, skill_names in self.subsets.custom_tags.items()
            },
            features=self.features,
            experimental_enabled=self.experimental_enabled,
            enabled_packs=frozenset(self.packs.enabled),
            tier1_skills=frozenset(self.skills.tier1),
            tier2_skills=frozenset(self.skills.tier2),
            tier3_skills=frozenset(self.skills.tier3),
        )

    @staticmethod
    def _build_features_dict(raw: dict[str, Any]) -> tuple[dict[str, bool], bool]:
        """Validate and coerce the features section from a raw config dict.

        Returns (features_dict, experimental_enabled).

        Raises ConfigSchemaError for:
        - Unknown feature names (not in FEATURE_REGISTRY)
        - Attempting to enable a DISABLED lifecycle feature
        - Dependency violations: enabling feature B without its required feature A

        Coerces all values to bool.
        """
        raw = dict(raw)  # copy to avoid mutating caller's dict
        _raw_exp = raw.pop("experimental_enabled", _UNSET)
        if _raw_exp is _UNSET:
            _raw_exp = raw.pop("EXPERIMENTAL_ENABLED", _UNSET)
        if _raw_exp is _UNSET:
            experimental_enabled: bool = is_dev_install()
        else:
            # Strict validation mirrors the per-feature bool check below — a
            # user writing `experimental_enabled: "false"` (a truthy string)
            # must NOT silently become True via `bool()` coercion.
            if not isinstance(_raw_exp, bool):
                raise ConfigSchemaError(
                    f"features.experimental_enabled must be a bool, "
                    f"got {type(_raw_exp).__name__!r}: {_raw_exp!r}"
                )
            experimental_enabled = _raw_exp
        result: dict[str, bool] = {}
        for name, value in raw.items():
            if not isinstance(name, str):
                raise ConfigSchemaError(
                    f"Feature key must be a string, got {type(name).__name__!r}: {name!r}"
                )
            name = name.lower()
            if name not in FEATURE_REGISTRY:
                known = sorted(FEATURE_REGISTRY.keys())
                raise ConfigSchemaError(
                    f"Unknown feature {name!r} in features config. Known features: {known}"
                )
            if not isinstance(value, bool):
                raise ConfigSchemaError(
                    f"Feature {name!r} value must be a bool, "
                    f"got {type(value).__name__!r}: {value!r}"
                )
            if value is True:
                if FEATURE_REGISTRY[name].lifecycle == FeatureLifecycle.DISABLED:
                    raise ConfigSchemaError(
                        f"Feature {name!r} has lifecycle DISABLED"
                        " and cannot be explicitly enabled."
                    )
                if FEATURE_REGISTRY[name].lifecycle == FeatureLifecycle.DEPRECATED:
                    warnings.warn(
                        f"Feature {name!r} has lifecycle DEPRECATED"
                        f" (sunset: {FEATURE_REGISTRY[name].sunset_date}). "
                        "Consider removing this override before the sunset date.",
                        DeprecationWarning,
                        stacklevel=2,
                    )
            result[name] = value

        # Dependency validation
        for name, enabled in result.items():
            if not enabled:
                continue
            defn = FEATURE_REGISTRY[name]
            for dep in defn.depends_on:
                try:
                    dep_default = FEATURE_REGISTRY[dep].default_enabled
                except KeyError:
                    raise ConfigSchemaError(
                        f"Feature {name!r} depends_on {dep!r}, which is not in FEATURE_REGISTRY. "
                        f"This is a bug in the FeatureDef definition."
                    )
                dep_enabled = result.get(dep, dep_default)
                if not dep_enabled:
                    raise ConfigSchemaError(
                        f"Feature {name!r} is enabled but its dependency {dep!r} is disabled. "
                        f"Enable {dep!r} first."
                    )

        return result, experimental_enabled

    @classmethod
    def from_dynaconf(cls, d: Dynaconf) -> AutomationConfig:
        """Build a typed AutomationConfig from a loaded Dynaconf instance."""
        raw = d.as_dict()

        def sec(name: str) -> dict[str, Any]:
            return raw.get(name.upper(), {})

        feat = sec("features")
        if not isinstance(feat, dict):
            raise ConfigSchemaError(
                f"features must be a mapping, got {type(feat).__name__!r}: {feat!r}"
            )
        features_dict, exp_enabled = AutomationConfig._build_features_dict(dict(feat))

        kwargs: dict[str, Any] = {}
        for f in dataclasses.fields(cls):
            if f.name in ("features", "experimental_enabled"):
                continue

            section_name = f.name
            section_raw = sec(section_name)

            preprocess = _SECTION_PREPROCESSORS.get(section_name)
            if preprocess is not None:
                section_raw = preprocess(section_raw)

            builder = _SECTION_BUILDERS.get(section_name)
            if builder is not None:
                kwargs[section_name] = builder(section_raw)
            else:
                if f.default_factory is dataclasses.MISSING or not dataclasses.is_dataclass(
                    f.default_factory
                ):
                    raise ConfigSchemaError(
                        f"AutomationConfig field {f.name!r} has no dataclass factory; "
                        f"add it to _SECTION_BUILDERS or handle it explicitly."
                    )
                kwargs[section_name] = _build_subconfig(
                    f.default_factory, section_raw, section_name
                )

        kwargs["features"] = features_dict
        kwargs["experimental_enabled"] = exp_enabled

        result = cls(**kwargs)
        try:
            result.fleet.validate(
                is_feature_enabled(
                    "fleet", result.features, experimental_enabled=result.experimental_enabled
                )
            )
        except ValueError as exc:
            raise ValueError(f"fleet config: {exc}") from exc
        try:
            result.process_tether.validate()
        except ValueError as exc:
            raise ValueError(f"process_tether config: {exc}") from exc
        _timeout_coherence_gate(result.run_skill)
        _codex_mcp_timeout_coherence_gate(
            result.run_skill, result.fleet, tool_timeout=result.run_skill.mcp_tool_timeout_sec
        )
        _claude_mcp_timeout_coherence_gate(result.run_skill, result.fleet)
        _process_tether_coherence_gate(result.process_tether, result.fleet, result.run_skill)
        return result
