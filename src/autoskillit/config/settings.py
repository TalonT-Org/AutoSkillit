"""Configuration loading with dynaconf layered resolution.

Resolution order (low → high priority):
  1. Package defaults  (config/defaults.yaml, always loaded)
  2. User config       (~/.autoskillit/config.yaml, if present)
  3. Project config    (.autoskillit/config.yaml, if present)
  4. Secrets file      (.autoskillit/.secrets.yaml, if present)
  5. Environment vars  (AUTOSKILLIT_SECTION__KEY=value)
"""

from __future__ import annotations

import dataclasses
import math
import types
import warnings
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    NamedTuple,
    TypeVar,
    Union,
    get_args,
    get_origin,
    get_type_hints,
)

from autoskillit.config._config_dataclasses import (
    _COMMAND_UNSET,
    _METADATA_KEYS,
    _SECRETS_ONLY_KEYS,
    AgentBackendConfig,
    BranchingConfig,
    CIConfig,
    ClassifyFixConfig,
    ConfigSchemaError,
    CoreRunConfig,
    DiagnosticsConfig,
    FleetConfig,
    GitHubConfig,
    ImplementGateConfig,
    LinuxTracingConfig,
    LoggingConfig,
    McpResponseConfig,
    MigrationConfig,
    OutputBudgetConfig,
    PacksConfig,
    PlanConfig,
    ProviderProfileDef,
    ProvidersConfig,
    QuotaGuardConfig,
    ReadDbConfig,
    ReportBugConfig,
    ResetWorkspaceConfig,
    ReviewConfig,
    RunSkillConfig,
    SafetyConfig,
    SkillsConfig,
    SubsetsConfig,
    TestCheckConfig,
    TokenUsageConfig,
    WorkspaceConfig,
    WorktreeSetupConfig,
)
from autoskillit.config._config_loader import (
    _build_packs_config,
    _build_subsets_config,
    _to_optional_commands,
    load_config,
)
from autoskillit.core import (
    FEATURE_REGISTRY,
    FeatureLifecycle,
    SkillVisibilitySpec,
    atomic_write,
    dump_yaml_str,
    get_logger,
    is_dev_install,
    is_feature_enabled,
)

if TYPE_CHECKING:
    from dynaconf import Dynaconf

logger = get_logger(__name__)

_UNSET = object()

# Known tool timeouts for coherence validation.
# These are the maximum observed blocking durations for tools that may produce
# zero stdout during execution — used to validate idle_output_timeout coherence.
_MERGE_QUEUE_DEFAULT = 600
_MERGE_QUEUE_RECIPE_MAX = 900
_CI_WATCH_DEFAULT = 300


def _timeout_coherence_gate(run_skill: RunSkillConfig) -> None:
    """Warn when idle_output_timeout is too low relative to known long-polling tool durations.

    The idle stall watchdog monitors raw stdout byte growth with no awareness of MCP tool
    execution state. When idle_output_timeout <= a known tool's max duration, the watchdog
    can fire and kill legitimate sessions that are simply waiting on a long poll.

    This is a WARNING-only gate — existing configs continue working.
    """
    idle = run_skill.idle_output_timeout
    if idle == 0:
        return
    if idle <= _MERGE_QUEUE_RECIPE_MAX:
        logger.warning(
            "idle_output_timeout_coherence",
            idle_output_timeout=idle,
            merge_queue_recipe_max=_MERGE_QUEUE_RECIPE_MAX,
            merge_queue_default=_MERGE_QUEUE_DEFAULT,
            ci_watch_default=_CI_WATCH_DEFAULT,
            message=(
                f"idle_output_timeout={idle}s is at or below the maximum known blocking tool "
                f"duration ({_MERGE_QUEUE_RECIPE_MAX}s for wait_for_merge_queue recipe override). "
                f"This creates a race condition where the idle stall watchdog fires before the "
                f"long-polling tool returns. Consider raising idle_output_timeout to at least "
                f"{_MERGE_QUEUE_RECIPE_MAX + 100}s, or set it to 0 to disable the watchdog "
                f"for L2 food truck sessions."
            ),
        )


def compute_codex_mcp_tool_timeout(
    run_skill: RunSkillConfig | None = None,
    fleet: FleetConfig | None = None,
) -> float:
    """Derive tool_timeout_sec from the system's timeout hierarchy.

    Uses loaded config values when provided, falls back to dataclass defaults.
    The result is always >= the floor derived from dataclass defaults.
    """
    rs = run_skill or RunSkillConfig()
    fc = fleet or FleetConfig()
    max_fleet = fc.default_timeout_sec + fc.max_extension_seconds
    max_skill = rs.timeout
    return max(max_fleet, max_skill) * 1.33


def _codex_mcp_timeout_coherence_gate(
    run_skill: RunSkillConfig, fleet: FleetConfig, *, tool_timeout: float | None = None
) -> None:
    """Warn when Codex MCP tool_timeout_sec is below the maximum session duration."""
    if tool_timeout is None:
        tool_timeout = compute_codex_mcp_tool_timeout(run_skill=run_skill, fleet=fleet)
    max_fleet = fleet.default_timeout_sec + fleet.max_extension_seconds
    max_skill = run_skill.timeout
    max_session = max(max_fleet, max_skill)
    if tool_timeout < max_session:
        logger.warning(
            "codex_mcp_tool_timeout_coherence",
            tool_timeout_sec=tool_timeout,
            max_session_duration=max_session,
            message=(
                f"Codex MCP tool_timeout_sec ({tool_timeout}s) is below the maximum "
                f"possible session duration ({max_session}s). This will cause Codex to kill "
                f"long-running MCP tool calls before autoskillit's own session management."
            ),
        )


def _claude_mcp_timeout_coherence_gate(
    run_skill: RunSkillConfig, fleet: FleetConfig, *, tool_timeout: float | None = None
) -> None:
    """Warn when Claude's MCP idle-abort timeout is below the maximum session duration.

    ``tool_timeout`` mirrors the Codex gate's signature; when omitted it defaults
    to ``run_skill.mcp_tool_timeout_sec``. Deployed/on-disk unset cases are
    covered by ``autoskillit doctor``, not this gate.
    """
    if tool_timeout is None:
        tool_timeout = run_skill.mcp_tool_timeout_sec
    if (
        not isinstance(tool_timeout, (int, float))
        or isinstance(tool_timeout, bool)
        or not math.isfinite(tool_timeout)
        or tool_timeout <= 0
    ):
        # Reject NaN/Inf/boolean/zero/negative: NaN comparison silently returns
        # False, so the gate would otherwise pass an unsound value through.
        raise ValueError(
            f"mcp_tool_timeout_sec must be a positive number of seconds, got {tool_timeout!r}."
        )
    max_fleet = fleet.default_timeout_sec + fleet.max_extension_seconds
    max_skill = run_skill.timeout
    max_session = max(max_fleet, max_skill)
    if tool_timeout < max_session:
        logger.warning(
            "claude_mcp_tool_timeout_coherence",
            tool_timeout_sec=tool_timeout,
            max_session_duration=max_session,
            message=(
                f"Claude MCP mcp_tool_timeout_sec ({tool_timeout}s) is below "
                f"the maximum possible session duration ({max_session}s). This will cause "
                f"Claude Code to idle-abort long-running MCP tool calls before autoskillit's "
                f"own session management."
            ),
        )


__all__ = [
    "AgentBackendConfig",
    "AutomationConfig",
    "compute_codex_mcp_tool_timeout",
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
    "ProviderProfileDef",
    "ProvidersConfig",
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
    "PlanConfig",
    "load_config",
    "remap_retired_keys",
    "RemappedConfigKey",
    "RETIRED_CONFIG_KEYS",
    "RetiredConfigKeyDef",
    "validate_layer_keys",
    "write_config_layer",
]


def _field_defaults(cls: type) -> dict[str, Any]:
    """Extract default values from dataclass fields into a dict keyed by field name."""
    defaults: dict[str, Any] = {}
    for f in dataclasses.fields(cls):  # type: ignore[arg-type]
        if f.default is not dataclasses.MISSING:
            defaults[f.name] = f.default
        elif f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
            defaults[f.name] = f.default_factory()  # type: ignore[call-arg]
    return defaults


_T = TypeVar("_T")


def _coerce_value(value: Any, target_type: type, context: str) -> Any:
    """Coerce a raw config value to target_type based on its type annotation.

    Raises ConfigSchemaError for int/float conversion failures, including context.
    """
    origin = get_origin(target_type)
    args = get_args(target_type)

    if origin is types.UnionType or origin is Union:
        non_none = [a for a in args if a is not type(None)]
        if type(None) in args and len(non_none) == 1:
            inner = non_none[0]
            if inner is bool:
                return bool(value) if value is not None else None
            if inner in (int, float):
                return _coerce_value(value, inner, context) if value is not None else None
            if not value:
                return None
            return _coerce_value(value, inner, context)
        return value

    # Dimensional wrapper types (Utf8ByteLimit, etc.): bless raw YAML
    # integers into typed wrappers exactly once at the config boundary.
    from autoskillit.core import Utf8ByteLimit

    if target_type is Utf8ByteLimit:
        if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
            raise ConfigSchemaError(
                f"{context} must be a positive integer for Utf8ByteLimit, got {value!r}"
            )
        try:
            return Utf8ByteLimit(int(value))
        except (TypeError, ValueError) as exc:
            raise ConfigSchemaError(
                f"{context} must be a positive integer for Utf8ByteLimit, got {value!r}"
            ) from exc
    if target_type is int:
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ConfigSchemaError(f"{context} must be an integer, got {value!r}") from exc
    if target_type is float:
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ConfigSchemaError(f"{context} must be a number, got {value!r}") from exc
    if target_type is bool:
        return bool(value)
    if target_type is str:
        return str(value)
    if origin is list:
        try:
            return list(value)
        except TypeError as exc:
            raise ConfigSchemaError(f"{context} must be iterable for list, got {value!r}") from exc
    if origin is set:
        try:
            return set(value)
        except TypeError as exc:
            raise ConfigSchemaError(f"{context} must be iterable for set, got {value!r}") from exc
    if origin is dict:
        return value
    return value


# YAML key name differs from Python field name.
# Key: (section_name, field_name), Value: yaml_key_name
_YAML_KEY_ALIASES: dict[tuple[str, str], str] = {
    ("model", "default_model"): "default",
    ("model", "model_override"): "override",
}


class RetiredConfigKeyDef(NamedTuple):
    """One config sub-key this tool renamed, and where its value now lives.

    ``~/.autoskillit/config.yaml`` and project ``.autoskillit/config.yaml``
    persist across years of releases while ``_CONFIG_SCHEMA`` is derived from
    the *current* dataclasses. Renaming a field without an entry here makes
    every pre-existing config fail ``validate_layer_keys`` at startup — the
    outage this registry exists to prevent.
    """

    new_key: str
    retired_in: str
    note: str


# (section, retired_sub_key) -> where the value goes now. Append-only:
#   * DO NOT REMOVE entries.
#   * A retired name may NEVER be reused for a future field (guarded by T1a).
#   * Entries must always point at the *current* name. A chained rename
#     (old1 -> old2 -> new) gets a direct old1 -> new entry (guarded by T1b/T1d).
#   * The (section, key) shape covers SUB-KEY renames only. A top-level
#     *section* rename is rejected earlier, in validate_layer_keys, and would
#     need a dotted-path extension of this registry — it is deliberately not
#     built speculatively.
#   * Both halves of every entry use the **YAML key spelling**, not the Python
#     dataclass field name. These differ only in the ``model`` section today
#     (``_YAML_KEY_ALIASES``: default_model -> "default", model_override ->
#     "override"). ``_CONFIG_SCHEMA`` is built from YAML spellings and
#     ``_build_subconfig`` reads by YAML key, so a Python-field spelling here
#     would both fail T1b and write a key nothing reads.
#   * Every entry must have a migrations/*.yaml note whose ``detect.key``
#     matches (guarded by T2).
RETIRED_CONFIG_KEYS: Mapping[tuple[str, str], RetiredConfigKeyDef] = types.MappingProxyType(
    {
        ("diagnostics", "post_run_analysis"): RetiredConfigKeyDef(
            new_key="pipeline_health",
            retired_in="0.10.885",
            note=(
                "diagnostics.post_run_analysis was renamed to "
                "diagnostics.pipeline_health in 0.10.885."
            ),
        ),
        ("quota_guard", "threshold"): RetiredConfigKeyDef(
            new_key="short_window_threshold",
            retired_in="0.8.39",
            note=(
                "quota_guard.threshold was split into quota_guard.short_window_threshold "
                "and quota_guard.long_window_threshold in 0.8.39. The old value is carried "
                "onto short_window_threshold; long_window_threshold keeps its default "
                "(95.0) unless set explicitly."
            ),
        ),
        ("features", "franchise"): RetiredConfigKeyDef(
            new_key="fleet",
            retired_in="0.9.135",
            note="features.franchise was renamed to features.fleet in 0.9.135.",
        ),
        ("agent_backend", "force_claude_agent_teams_inactive"): RetiredConfigKeyDef(
            new_key="force_inactive_agent_teams",
            retired_in="0.10.987",
            note=(
                "agent_backend.force_claude_agent_teams_inactive was renamed to "
                "agent_backend.force_inactive_agent_teams in 0.10.987."
            ),
        ),
    }
)

_NON_LOWER_RETIRED_KEYS = sorted(
    (s, k) for s, k in RETIRED_CONFIG_KEYS if s != s.lower() or k != k.lower()
)
if _NON_LOWER_RETIRED_KEYS:
    raise AssertionError(
        "RETIRED_CONFIG_KEYS (section, old_key) pairs must be lowercase. "
        f"Offending: {_NON_LOWER_RETIRED_KEYS}"
    )
del _NON_LOWER_RETIRED_KEYS

_NON_LOWER_NEW_KEYS = sorted(
    d.new_key for d in RETIRED_CONFIG_KEYS.values() if d.new_key != d.new_key.lower()
)
if _NON_LOWER_NEW_KEYS:
    raise AssertionError(
        f"RETIRED_CONFIG_KEYS new_key values must be lowercase. Offending: {_NON_LOWER_NEW_KEYS}"
    )
del _NON_LOWER_NEW_KEYS


# Custom field builders that bypass _coerce_value.
# Signature: (section_dict, defaults_dict) -> coerced_value
# The override is responsible for its own key lookup from section_dict.
_FIELD_OVERRIDES: dict[tuple[str, str], Callable[[dict[str, Any], dict[str, Any]], Any]] = {
    # YAML key "default" with None-means-unset semantic
    ("model", "default_model"): lambda sec, defs: (
        str(sec["default"]) if sec.get("default") is not None else defs["default_model"]
    ),
    # Sentinel for __post_init__ mutual-exclusion check with commands
    ("test_check", "command"): lambda sec, defs: (
        list(sec["command"]) if sec.get("command") is not None else _COMMAND_UNSET
    ),
    # Structural validation for nested list shape
    ("test_check", "commands"): lambda sec, defs: _to_optional_commands(
        sec.get("commands", defs.get("commands"))
    ),
    # Uppercase transform
    ("logging", "level"): lambda sec, defs: str(sec.get("level", defs["level"])).upper(),
}


def _preprocess_agent_backend(raw: Any) -> dict[str, Any]:
    """Normalize agent_backend section: string shorthand or lowercased dict."""
    if isinstance(raw, str):
        return {"backend": raw}
    if isinstance(raw, dict):
        return {k.lower(): v for k, v in raw.items()}
    raise ConfigSchemaError(
        f"agent_backend must be a string or mapping, got {type(raw).__name__!r}: {raw!r}"
    )


# Section-level pre-processors applied before _build_subconfig.
_SECTION_PREPROCESSORS: dict[str, Callable[[Any], dict[str, Any]]] = {
    "agent_backend": _preprocess_agent_backend,
}

# Sections with fully custom builders (bypass _build_subconfig entirely).
_SECTION_BUILDERS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "subsets": _build_subsets_config,
    "packs": _build_packs_config,
}


def _build_subconfig(cls: type[_T], section: dict[str, Any], section_name: str) -> _T:
    """Build a sub-config dataclass from a raw Dynaconf section dict.

    Uses dataclass field introspection and type annotations to auto-coerce
    values. Fields listed in _FIELD_OVERRIDES use custom builders. Fields
    listed in _YAML_KEY_ALIASES read from an alternate YAML key name.
    """
    defaults = _field_defaults(cls)
    hints = get_type_hints(cls)
    kwargs: dict[str, Any] = {}

    for f in dataclasses.fields(cls):  # type: ignore[arg-type]
        override_key = (section_name, f.name)
        if override_key in _FIELD_OVERRIDES:
            kwargs[f.name] = _FIELD_OVERRIDES[override_key](section, defaults)
            continue
        yaml_key = _YAML_KEY_ALIASES.get(override_key, f.name)
        raw = section.get(yaml_key, defaults.get(f.name))
        if raw is None and f.name not in defaults:
            raise ConfigSchemaError(
                f"{section_name}.{f.name} is required but absent from config and has no default."
            )
        kwargs[f.name] = _coerce_value(raw, hints[f.name], f"{section_name}.{yaml_key}")

    return cls(**kwargs)  # type: ignore[return-value]


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
        experimental_enabled: bool = is_dev_install() if _raw_exp is _UNSET else bool(_raw_exp)
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
        features_dict, exp_enabled = AutomationConfig._build_features_dict(
            dict(feat) if isinstance(feat, dict) else {}
        )

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
        _timeout_coherence_gate(result.run_skill)
        _codex_mcp_timeout_coherence_gate(
            result.run_skill, result.fleet, tool_timeout=result.run_skill.mcp_tool_timeout_sec
        )
        _claude_mcp_timeout_coherence_gate(result.run_skill, result.fleet)
        return result


def _build_config_schema() -> dict[str, frozenset[str]]:
    """Derive a two-level schema map {section: {valid_field_names}} from AutomationConfig."""
    schema: dict[str, frozenset[str]] = {}
    for f in dataclasses.fields(AutomationConfig):
        if f.name == "features":
            schema["features"] = frozenset(FEATURE_REGISTRY.keys()) | frozenset(
                {"experimental_enabled"}
            )
            continue
        if f.name == "experimental_enabled":
            continue
        sub_type: type | None = None
        if f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
            factory = f.default_factory  # type: ignore[assignment]
            if dataclasses.is_dataclass(factory):
                sub_type = factory
        elif f.default is not dataclasses.MISSING and dataclasses.is_dataclass(f.default):
            sub_type = type(f.default)
        if sub_type is not None:
            yaml_keys: set[str] = set()
            for sf in dataclasses.fields(sub_type):
                alias = _YAML_KEY_ALIASES.get((f.name, sf.name))
                yaml_keys.add(alias if alias is not None else sf.name)
            # Also include YAML keys from field overrides that use different key names
            for (sec_name, _field_name), _override in _FIELD_OVERRIDES.items():
                if sec_name == f.name:
                    alias = _YAML_KEY_ALIASES.get((sec_name, _field_name))
                    if alias is not None:
                        yaml_keys.add(alias)
            schema[f.name] = frozenset(yaml_keys)
        else:
            schema[f.name] = frozenset()
    return schema


_CONFIG_SCHEMA: dict[str, frozenset[str]] = _build_config_schema()


def validate_layer_keys(
    layer_dict: dict[str, Any],
    layer_path: Path,
    *,
    is_secrets_layer: bool,
) -> None:
    """Validate that all keys in a YAML config layer are recognized and allowed.

    Raises ConfigSchemaError for:
    - Unrecognized top-level section name
    - Unrecognized field name within a known section
    - A _SECRETS_ONLY_KEYS path appearing in a non-secrets layer
    """
    import difflib  # stdlib — safe to import here

    for top_key, value in layer_dict.items():
        if top_key in _METADATA_KEYS:
            continue
        if top_key not in _CONFIG_SCHEMA:
            known = sorted(_CONFIG_SCHEMA.keys())
            close = difflib.get_close_matches(top_key, known, n=1, cutoff=0.6)
            hint = f" did you mean '{close[0]}'?" if close else ""
            raise ConfigSchemaError(
                f"Invalid configuration in {str(layer_path)!r}: "
                f"unrecognized key '{top_key}'.{hint}"
            )
        # Validate sub-keys for all dict-valued sections; empty frozenset means no valid sub-keys
        if isinstance(value, dict):
            for sub_key in value:
                dotted = f"{top_key}.{sub_key}"
                if dotted in _SECRETS_ONLY_KEYS:
                    if not is_secrets_layer:
                        secrets_hint_path = layer_path.parent / ".secrets.yaml"
                        top, sub = dotted.split(".", 1)
                        raise ConfigSchemaError(
                            f"Invalid configuration in {str(layer_path)!r}: "
                            f"'{dotted}' is a secret key that must not appear in config.yaml.\n\n"
                            f"To fix, add the following to {str(secrets_hint_path)!r}:\n\n"
                            f"  {top}:\n"
                            f"    {sub}: <your_token_value>\n\n"
                            f"Then remove the '{dotted}' key from {str(layer_path)!r}."
                        )
                    continue  # secrets-only keys are valid in .secrets.yaml
                if sub_key not in _CONFIG_SCHEMA[top_key]:
                    known_sub = sorted(_CONFIG_SCHEMA[top_key])
                    close = difflib.get_close_matches(sub_key, known_sub, n=1, cutoff=0.6)
                    hint = f" did you mean '{top_key}.{close[0]}'?" if close else ""
                    raise ConfigSchemaError(
                        f"Invalid configuration in {str(layer_path)!r}: "
                        f"unrecognized key '{dotted}' in section '{top_key}'.{hint}"
                    )


class RemappedConfigKey(NamedTuple):
    section: str
    old_key: str
    new_key: str
    value_moved: bool
    definition: RetiredConfigKeyDef


def remap_retired_keys(
    layer_dict: dict[str, Any],
    *,
    is_secrets_layer: bool,
) -> tuple[dict[str, Any], list[RemappedConfigKey]]:
    """Return (layer_dict with retired sub-keys rewritten, remap records).

    Pure: never mutates ``layer_dict``. Returns the input object unchanged
    when there is nothing to do. Secrets layers are returned untouched.
    """
    if is_secrets_layer:
        return layer_dict, []

    hits: list[tuple[str, str, RetiredConfigKeyDef]] = []
    for (section, old_key), defn in sorted(RETIRED_CONFIG_KEYS.items()):
        section_dict = layer_dict.get(section)
        if isinstance(section_dict, dict) and old_key in section_dict:
            hits.append((section, old_key, defn))

    if not hits:
        return layer_dict, []

    result = dict(layer_dict)
    records: list[RemappedConfigKey] = []
    for section, old_key, defn in hits:
        if result[section] is layer_dict[section]:
            result[section] = dict(result[section])
        section_dict = result[section]
        value = section_dict.pop(old_key)
        moved = defn.new_key not in section_dict
        if moved:
            section_dict[defn.new_key] = value
        records.append(
            RemappedConfigKey(
                section=section,
                old_key=old_key,
                new_key=defn.new_key,
                value_moved=moved,
                definition=defn,
            )
        )

    return result, records


def write_config_layer(path: Path, data: dict[str, Any]) -> None:
    """Validate config data against the schema, then atomically write it to path.

    Raises ConfigSchemaError before touching the file if the data contains
    unrecognized keys, unknown sub-keys, or any _SECRETS_ONLY_KEYS entries.
    This is the canonical write gateway for all config.yaml write sites.

    Parameters
    ----------
    path:
        Destination file path. Must be a non-secrets config.yaml path — never
        .secrets.yaml (which allows different keys).
    data:
        YAML-serializable dict to validate and write.
    """
    validate_layer_keys(data, path, is_secrets_layer=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, dump_yaml_str(data, default_flow_style=False, allow_unicode=True))
