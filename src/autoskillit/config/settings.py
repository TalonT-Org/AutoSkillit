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
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autoskillit.config._config_dataclasses import (
    _COMMAND_UNSET,
    _METADATA_KEYS,
    _SECRETS_ONLY_KEYS,
    BranchingConfig,
    CIConfig,
    ClassifyFixConfig,
    ConfigSchemaError,
    FleetConfig,
    GitHubConfig,
    ImplementGateConfig,
    LinuxTracingConfig,
    LoggingConfig,
    McpResponseConfig,
    MigrationConfig,
    ModelConfig,
    PacksConfig,
    ProvidersConfig,
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
    WorkspaceConfig,
    WorktreeSetupConfig,
)
from autoskillit.config._config_loader import (
    _build_packs_config,
    _build_subsets_config,
    _to_optional_commands,
    _to_optional_list,
    load_config,
)
from autoskillit.core import (
    FEATURE_REGISTRY,
    FeatureLifecycle,
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

__all__ = [
    "AutomationConfig",
    "BranchingConfig",
    "CIConfig",
    "ClassifyFixConfig",
    "ConfigSchemaError",
    "FleetConfig",
    "GitHubConfig",
    "ImplementGateConfig",
    "LinuxTracingConfig",
    "LoggingConfig",
    "McpResponseConfig",
    "MigrationConfig",
    "ModelConfig",
    "PacksConfig",
    "ProvidersConfig",
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
    "WorkspaceConfig",
    "WorktreeSetupConfig",
    "load_config",
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


@dataclass
class AutomationConfig:
    test_check: TestCheckConfig = field(default_factory=TestCheckConfig)
    classify_fix: ClassifyFixConfig = field(default_factory=ClassifyFixConfig)
    reset_workspace: ResetWorkspaceConfig = field(default_factory=ResetWorkspaceConfig)
    implement_gate: ImplementGateConfig = field(default_factory=ImplementGateConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    read_db: ReadDbConfig = field(default_factory=ReadDbConfig)
    run_skill: RunSkillConfig = field(default_factory=RunSkillConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    worktree_setup: WorktreeSetupConfig = field(default_factory=WorktreeSetupConfig)
    migration: MigrationConfig = field(default_factory=MigrationConfig)
    token_usage: TokenUsageConfig = field(default_factory=TokenUsageConfig)
    quota_guard: QuotaGuardConfig = field(default_factory=QuotaGuardConfig)
    github: GitHubConfig = field(default_factory=GitHubConfig)
    report_bug: ReportBugConfig = field(default_factory=ReportBugConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    linux_tracing: LinuxTracingConfig = field(default_factory=LinuxTracingConfig)
    mcp_response: McpResponseConfig = field(default_factory=McpResponseConfig)
    branching: BranchingConfig = field(default_factory=BranchingConfig)
    ci: CIConfig = field(default_factory=CIConfig)
    skills: SkillsConfig = field(default_factory=SkillsConfig)
    subsets: SubsetsConfig = field(default_factory=SubsetsConfig)
    packs: PacksConfig = field(default_factory=PacksConfig)
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)
    fleet: FleetConfig = field(default_factory=FleetConfig)
    providers: ProvidersConfig = field(default_factory=ProvidersConfig)
    features: dict[str, bool] = field(default_factory=dict)
    experimental_enabled: bool = False

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
        """Build a typed AutomationConfig from a loaded Dynaconf instance.

        d.as_dict() returns UPPERCASE keys — map them explicitly.
        Lists are converted to set where the dataclass field is set[str].
        """
        raw = d.as_dict()

        def sec(name: str) -> dict[str, Any]:
            return raw.get(name.upper(), {})

        def val(section: dict[str, Any], key: str, default: Any) -> Any:
            return section.get(key, default)

        tc = sec("test_check")
        cf = sec("classify_fix")
        rw = sec("reset_workspace")
        ig = sec("implement_gate")
        sf = sec("safety")
        rd = sec("read_db")
        rs = sec("run_skill")
        mc = sec("model")
        ws = sec("worktree_setup")
        mi = sec("migration")
        tu = sec("token_usage")
        qg = sec("quota_guard")
        gh = sec("github")
        rb = sec("report_bug")
        lg = sec("logging")
        lt = sec("linux_tracing")
        mr = sec("mcp_response")
        br = sec("branching")
        ci = sec("ci")
        sk = sec("skills")
        _sub = sec("subsets")
        pk = sec("packs")
        ws_raw = sec("workspace")
        fr = sec("fleet")
        pv = sec("providers")
        feat = sec("features")

        _tc = _field_defaults(TestCheckConfig)
        _cf = _field_defaults(ClassifyFixConfig)
        _rw = _field_defaults(ResetWorkspaceConfig)
        _ig = _field_defaults(ImplementGateConfig)
        _sf = _field_defaults(SafetyConfig)
        _rd = _field_defaults(ReadDbConfig)
        _rs = _field_defaults(RunSkillConfig)
        _mc = _field_defaults(ModelConfig)
        _ws = _field_defaults(WorktreeSetupConfig)
        _mi = _field_defaults(MigrationConfig)
        _tu = _field_defaults(TokenUsageConfig)
        _qg = _field_defaults(QuotaGuardConfig)
        _gh = _field_defaults(GitHubConfig)
        _rb = _field_defaults(ReportBugConfig)
        _lg = _field_defaults(LoggingConfig)
        _lt = _field_defaults(LinuxTracingConfig)
        _mr = _field_defaults(McpResponseConfig)
        _br = _field_defaults(BranchingConfig)
        _ci = _field_defaults(CIConfig)
        _sk = _field_defaults(SkillsConfig)
        _wsc = _field_defaults(WorkspaceConfig)
        _fr = _field_defaults(FleetConfig)
        _pv = _field_defaults(ProvidersConfig)

        _features_dict, _exp_enabled = AutomationConfig._build_features_dict(
            dict(feat) if isinstance(feat, dict) else {}
        )

        _raw_command = val(tc, "command", None)
        result = cls(
            test_check=TestCheckConfig(
                command=list(_raw_command) if _raw_command is not None else _COMMAND_UNSET,
                timeout=int(val(tc, "timeout", _tc["timeout"])),
                filter_mode=val(tc, "filter_mode", _tc["filter_mode"]) or None,
                base_ref=val(tc, "base_ref", _tc["base_ref"]) or None,
                commands=_to_optional_commands(val(tc, "commands", _tc["commands"])),
            ),
            classify_fix=ClassifyFixConfig(
                path_prefixes=list(val(cf, "path_prefixes", _cf["path_prefixes"])),
            ),
            reset_workspace=ResetWorkspaceConfig(
                command=_to_optional_list(val(rw, "command", _rw["command"])),
                preserve_dirs=set(val(rw, "preserve_dirs", _rw["preserve_dirs"])),
            ),
            implement_gate=ImplementGateConfig(
                marker=str(val(ig, "marker", _ig["marker"])),
                skill_names=set(val(ig, "skill_names", _ig["skill_names"])),
            ),
            safety=SafetyConfig(
                reset_guard_marker=str(val(sf, "reset_guard_marker", _sf["reset_guard_marker"])),
                require_dry_walkthrough=bool(
                    val(sf, "require_dry_walkthrough", _sf["require_dry_walkthrough"])
                ),
                test_gate_on_merge=bool(val(sf, "test_gate_on_merge", _sf["test_gate_on_merge"])),
                protected_branches=list(val(sf, "protected_branches", _sf["protected_branches"])),
            ),
            read_db=ReadDbConfig(
                timeout=int(val(rd, "timeout", _rd["timeout"])),
                max_rows=int(val(rd, "max_rows", _rd["max_rows"])),
            ),
            run_skill=RunSkillConfig(
                timeout=int(val(rs, "timeout", _rs["timeout"])),
                stale_threshold=int(val(rs, "stale_threshold", _rs["stale_threshold"])),
                completion_marker=str(val(rs, "completion_marker", _rs["completion_marker"])),
                completion_drain_timeout=float(
                    val(rs, "completion_drain_timeout", _rs["completion_drain_timeout"])
                ),
                exit_after_stop_delay_ms=int(
                    val(rs, "exit_after_stop_delay_ms", _rs["exit_after_stop_delay_ms"])
                ),
                natural_exit_grace_seconds=float(
                    val(rs, "natural_exit_grace_seconds", _rs["natural_exit_grace_seconds"])
                ),
                idle_output_timeout=int(
                    val(rs, "idle_output_timeout", _rs["idle_output_timeout"])
                ),
                max_suppression_seconds=int(
                    val(rs, "max_suppression_seconds", _rs["max_suppression_seconds"])
                ),
            ),
            model=ModelConfig(
                default=_d if (_d := val(mc, "default", None)) is not None else _mc["default"],
                override=val(mc, "override", _mc["override"]) or None,
            ),
            worktree_setup=WorktreeSetupConfig(
                command=_to_optional_list(val(ws, "command", _ws["command"])),
            ),
            migration=MigrationConfig(
                suppressed=list(val(mi, "suppressed", _mi["suppressed"])),
            ),
            token_usage=TokenUsageConfig(
                verbosity=str(val(tu, "verbosity", _tu["verbosity"])),
            ),
            quota_guard=QuotaGuardConfig(
                enabled=bool(val(qg, "enabled", _qg["enabled"])),
                short_window_enabled=bool(
                    val(qg, "short_window_enabled", _qg["short_window_enabled"])
                ),
                long_window_enabled=bool(
                    val(qg, "long_window_enabled", _qg["long_window_enabled"])
                ),
                short_window_threshold=float(
                    val(qg, "short_window_threshold", _qg["short_window_threshold"])
                ),
                long_window_threshold=float(
                    val(qg, "long_window_threshold", _qg["long_window_threshold"])
                ),
                long_window_patterns=list(
                    val(qg, "long_window_patterns", _qg["long_window_patterns"])
                ),
                buffer_seconds=int(val(qg, "buffer_seconds", _qg["buffer_seconds"])),
                cache_max_age=int(val(qg, "cache_max_age", _qg["cache_max_age"])),
                cache_refresh_interval=int(
                    val(qg, "cache_refresh_interval", _qg["cache_refresh_interval"])
                ),
                credentials_path=str(val(qg, "credentials_path", _qg["credentials_path"])),
                cache_path=str(val(qg, "cache_path", _qg["cache_path"])),
            ),
            github=GitHubConfig(
                token=val(gh, "token", _gh["token"]) or None,
                default_repo=val(gh, "default_repo", _gh["default_repo"]) or None,
                in_progress_label=str(val(gh, "in_progress_label", _gh["in_progress_label"])),
                staged_label=str(val(gh, "staged_label", _gh["staged_label"])),
                fail_label=str(val(gh, "fail_label", _gh["fail_label"])),
                allowed_labels=list(val(gh, "allowed_labels", _gh["allowed_labels"])),
            ),
            report_bug=ReportBugConfig(
                timeout=int(val(rb, "timeout", _rb["timeout"])),
                model=val(rb, "model", _rb["model"]) or None,
                report_dir=val(rb, "report_dir", _rb["report_dir"]) or None,
                github_filing=bool(val(rb, "github_filing", _rb["github_filing"])),
                github_labels=list(val(rb, "github_labels", _rb["github_labels"])),
            ),
            logging=LoggingConfig(
                level=str(val(lg, "level", _lg["level"])).upper(),
                json_output=(
                    bool(_jo)
                    if (_jo := val(lg, "json_output", _lg["json_output"])) is not None
                    else None
                ),
            ),
            linux_tracing=LinuxTracingConfig(
                enabled=bool(val(lt, "enabled", _lt["enabled"])),
                proc_interval=float(val(lt, "proc_interval", _lt["proc_interval"])),
                log_dir=str(val(lt, "log_dir", _lt["log_dir"])),
                tmpfs_path=str(val(lt, "tmpfs_path", _lt["tmpfs_path"])),
                max_sessions=int(val(lt, "max_sessions", _lt["max_sessions"])),
            ),
            mcp_response=McpResponseConfig(
                alert_threshold_tokens=int(
                    val(mr, "alert_threshold_tokens", _mr["alert_threshold_tokens"])
                ),
            ),
            branching=BranchingConfig(
                default_base_branch=str(
                    val(br, "default_base_branch", _br["default_base_branch"])
                ),
                promotion_target=str(val(br, "promotion_target", _br["promotion_target"])),
            ),
            ci=CIConfig(
                workflow=val(ci, "workflow", _ci["workflow"]) or None,
                event=val(ci, "event", _ci["event"]) or None,
            ),
            skills=SkillsConfig(
                tier1=list(val(sk, "tier1", _sk["tier1"])),
                tier2=list(val(sk, "tier2", _sk["tier2"])),
                tier3=list(val(sk, "tier3", _sk["tier3"])),
            ),
            subsets=_build_subsets_config(_sub),
            packs=_build_packs_config(pk),
            workspace=WorkspaceConfig(
                worktree_root=val(ws_raw, "worktree_root", _wsc["worktree_root"]) or None,
                runs_root=val(ws_raw, "runs_root", _wsc["runs_root"]) or None,
                temp_dir=val(ws_raw, "temp_dir", _wsc["temp_dir"]) or None,
            ),
            fleet=FleetConfig(
                default_timeout_sec=int(
                    val(fr, "default_timeout_sec", _fr["default_timeout_sec"])
                ),
                max_concurrent_dispatches=int(
                    val(fr, "max_concurrent_dispatches", _fr["max_concurrent_dispatches"])
                ),
            ),
            providers=ProvidersConfig(
                default_provider=val(pv, "default_provider", _pv["default_provider"]),
                profiles=val(pv, "profiles", _pv["profiles"]),
                step_overrides=val(pv, "step_overrides", _pv["step_overrides"]),
                provider_retry_limit=int(
                    val(pv, "provider_retry_limit", _pv["provider_retry_limit"])
                ),
            ),
            features=_features_dict,
            experimental_enabled=_exp_enabled,
        )
        try:
            result.fleet.validate(
                is_feature_enabled(
                    "fleet", result.features, experimental_enabled=result.experimental_enabled
                )
            )
        except ValueError as exc:
            raise ValueError(f"fleet config: {exc}") from exc
        return result


def _build_config_schema() -> dict[str, frozenset[str]]:
    """Derive a two-level schema map {section: {valid_field_names}} from AutomationConfig."""
    schema: dict[str, frozenset[str]] = {}
    for f in dataclasses.fields(AutomationConfig):
        # Special case: features is a dict[str, bool]; valid sub-keys come from FEATURE_REGISTRY
        if f.name == "features":
            schema["features"] = frozenset(FEATURE_REGISTRY.keys()) | frozenset(
                {"experimental_enabled"}
            )
            continue
        # Skip the scalar experimental_enabled field — it is handled under the features section
        if f.name == "experimental_enabled":
            continue
        sub_type: type | None = None
        if f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
            factory = f.default_factory  # type: ignore[assignment]
            if dataclasses.is_dataclass(factory):
                sub_type = factory
        elif f.default is not dataclasses.MISSING and dataclasses.is_dataclass(f.default):
            sub_type = type(f.default)
        schema[f.name] = (
            frozenset(sf.name for sf in dataclasses.fields(sub_type))
            if sub_type is not None
            else frozenset()
        )
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
