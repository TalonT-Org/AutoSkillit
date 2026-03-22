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
import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autoskillit.core import (
    CATEGORY_TAGS,
    OutputFormat,
    atomic_write,
    dump_yaml_str,
    load_yaml,
    pkg_root,
)

if TYPE_CHECKING:
    from dynaconf import Dynaconf

_logger = logging.getLogger(__name__)  # noqa: TID251


class ConfigSchemaError(ValueError):
    """Raised when a config YAML layer contains unrecognized or misplaced keys."""


_SECRETS_ONLY_KEYS: frozenset[str] = frozenset({"github.token"})
_METADATA_KEYS: frozenset[str] = frozenset({"version"})


@dataclass
class TestCheckConfig:
    command: list[str] = field(default_factory=lambda: ["task", "test-check"])
    timeout: int = 600


@dataclass
class ClassifyFixConfig:
    path_prefixes: list[str] = field(default_factory=list)


@dataclass
class ResetWorkspaceConfig:
    command: list[str] | None = None
    preserve_dirs: set[str] = field(default_factory=set)


@dataclass
class ImplementGateConfig:
    marker: str = "Dry-walkthrough verified = TRUE"
    skill_names: set[str] = field(
        default_factory=lambda: {
            "/autoskillit:implement-worktree",
            "/autoskillit:implement-worktree-no-merge",
        }
    )


@dataclass
class SafetyConfig:
    reset_guard_marker: str = ".autoskillit-workspace"
    require_dry_walkthrough: bool = True
    test_gate_on_merge: bool = True
    protected_branches: list[str] = field(
        default_factory=lambda: ["main", "integration", "stable"]
    )


@dataclass
class ReadDbConfig:
    timeout: int = 30
    max_rows: int = 10000


@dataclass
class RunSkillConfig:
    timeout: int = 7200
    stale_threshold: int = 1200  # 20 minutes
    completion_marker: str = "%%ORDER_UP%%"
    completion_drain_timeout: float = 5.0
    exit_after_stop_delay_ms: int = 120000

    @property
    def output_format(self) -> OutputFormat:
        """Derived from feature requirements — not independently configurable."""
        return OutputFormat.derive(completion_marker=self.completion_marker)


@dataclass
class ModelConfig:
    default: str = "sonnet"
    override: str | None = None


@dataclass
class WorktreeSetupConfig:
    command: list[str] | None = None


@dataclass
class MigrationConfig:
    suppressed: list[str] = field(default_factory=list)


@dataclass
class TokenUsageConfig:
    verbosity: str = "summary"  # "summary" | "none"


@dataclass
class QuotaGuardConfig:
    enabled: bool = True
    threshold: float = 90.0
    buffer_seconds: int = 60
    cache_max_age: int = 300
    credentials_path: str = "~/.claude/.credentials.json"
    cache_path: str = "~/.claude/autoskillit_quota_cache.json"


@dataclass
class GitHubConfig:
    token: str | None = None
    default_repo: str | None = None
    in_progress_label: str = "in-progress"
    staged_label: str = "staged"


@dataclass
class ReportBugConfig:
    timeout: int = 600
    model: str | None = None
    report_dir: str | None = None  # None = {cwd}/.autoskillit/temp/bug-reports/
    github_filing: bool = True
    github_labels: list[str] = field(default_factory=lambda: ["autoreported", "bug"])


@dataclass
class LoggingConfig:
    level: str = "INFO"
    json_output: bool | None = None  # None = auto-detect from stderr.isatty()


@dataclass
class LinuxTracingConfig:
    enabled: bool = True
    proc_interval: float = 5.0
    log_dir: str = ""  # empty = platform default (~/.local/share/autoskillit/logs on Linux)
    tmpfs_path: str = "/dev/shm"  # RAM-backed tmpfs for crash-resilient streaming


@dataclass
class McpResponseConfig:
    alert_threshold_tokens: int = 2000


@dataclass
class BranchingConfig:
    default_base_branch: str = "main"
    promotion_target: str = "main"  # Canonical upstream default for staged-label comparison.


@dataclass
class CIConfig:
    workflow: str | None = None


@dataclass
class SkillsConfig:
    tier1: list[str] = field(default_factory=list)
    tier2: list[str] = field(default_factory=list)
    tier3: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        t1, t2, t3 = set(self.tier1), set(self.tier2), set(self.tier3)
        dupes = (t1 & t2) | (t1 & t3) | (t2 & t3)
        if dupes:
            raise ValueError(f"Skills assigned to multiple tiers: {sorted(dupes)}")


@dataclass
class SubsetsConfig:
    disabled: list[str] = field(default_factory=list)
    custom_tags: dict[str, list[str]] = field(default_factory=dict)


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

        return cls(
            test_check=TestCheckConfig(
                command=list(val(tc, "command", _tc["command"])),
                timeout=int(val(tc, "timeout", _tc["timeout"])),
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
                threshold=float(val(qg, "threshold", _qg["threshold"])),
                buffer_seconds=int(val(qg, "buffer_seconds", _qg["buffer_seconds"])),
                cache_max_age=int(val(qg, "cache_max_age", _qg["cache_max_age"])),
                credentials_path=str(val(qg, "credentials_path", _qg["credentials_path"])),
                cache_path=str(val(qg, "cache_path", _qg["cache_path"])),
            ),
            github=GitHubConfig(
                token=val(gh, "token", _gh["token"]) or None,
                default_repo=val(gh, "default_repo", _gh["default_repo"]) or None,
                in_progress_label=str(val(gh, "in_progress_label", _gh["in_progress_label"])),
                staged_label=str(val(gh, "staged_label", _gh["staged_label"])),
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
            ),
            skills=SkillsConfig(
                tier1=list(val(sk, "tier1", _sk["tier1"])),
                tier2=list(val(sk, "tier2", _sk["tier2"])),
                tier3=list(val(sk, "tier3", _sk["tier3"])),
            ),
            subsets=_build_subsets_config(_sub),
        )


def _build_config_schema() -> dict[str, frozenset[str]]:
    """Derive a two-level schema map {section: {valid_field_names}} from AutomationConfig."""
    schema: dict[str, frozenset[str]] = {}
    for f in dataclasses.fields(AutomationConfig):
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


def _build_subsets_config(raw: dict[str, Any]) -> SubsetsConfig:
    """Parse subsets section, emitting warnings for unknown disabled categories."""
    disabled = list(raw.get("disabled", []))
    custom_tags_raw = raw.get("custom_tags", {}) or {}
    if not isinstance(custom_tags_raw, dict):
        raise ValueError(
            f"subsets.custom_tags must be a dict mapping tag names to skill lists, "
            f"got {type(custom_tags_raw).__name__!r}: {custom_tags_raw!r}"
        )
    custom_tags: dict[str, list[str]] = {}
    for k, v in custom_tags_raw.items():
        if isinstance(v, list):
            custom_tags[str(k)] = [str(item) for item in v]
        else:
            _logger.warning(
                "Ignoring non-list value for custom_tags entry %r: %r",
                k,
                v,
            )
    known_categories = CATEGORY_TAGS | frozenset(custom_tags.keys())
    for tag in disabled:
        if tag not in known_categories:
            _logger.warning(
                "Unknown category %r in subsets.disabled"
                " (not in CATEGORY_TAGS and not a custom_tag)",
                tag,
            )
    return SubsetsConfig(disabled=disabled, custom_tags=custom_tags)


def _to_optional_list(value: Any) -> list[str] | None:
    """Return None if value is falsy, else coerce to list[str]."""
    if not value:
        return None
    return list(value)


def _apply_layer(base: dict[str, Any], override: dict[str, Any]) -> None:
    """Apply override into base with dict deep-merge and list-replace semantics.

    Dicts are recursively merged so that a partial section in a later layer
    (e.g. project config with only github.default_repo) does not wipe sibling
    keys set by an earlier layer (e.g. user config with github.token).
    All other value types — including lists — are replaced outright, preserving
    the intuitive expectation that setting test_check.command in a config file
    gives exactly that command (not the defaults appended to it).
    """
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _apply_layer(base[key], value)
        else:
            base[key] = value


def _merge_yaml_layers(*paths: Path) -> dict[str, Any]:
    """Load and merge YAML files in order, applying _apply_layer for each."""
    result: dict[str, Any] = {}
    for path in paths:
        if path.is_file():
            data = load_yaml(path)
            if isinstance(data, dict):
                _apply_layer(result, data)
    return result


def _make_dynaconf(project_dir: Path | None = None) -> Dynaconf:
    """Create a Dynaconf instance for env-var overrides over pre-merged file layers.

    File layers (defaults, user, project, secrets) are merged in advance with
    dict deep-merge + list-replace semantics. User-writable layers are validated
    for unrecognized keys before merging. The merged result is written to a temp
    YAML file so that Dynaconf can apply env var overrides (AUTOSKILLIT_SECTION__KEY).

    Deferred import keeps dynaconf off the module-level import chain.
    """
    from dynaconf import Dynaconf  # noqa: PLC0415

    defaults_path = pkg_root() / "config" / "defaults.yaml"
    root = project_dir or Path.cwd()

    # Layer definitions: (path, should_validate, is_secrets_layer)
    _layers = [
        (defaults_path, False, False),
        (Path.home() / ".autoskillit" / "config.yaml", True, False),
        (root / ".autoskillit" / "config.yaml", True, False),
        (root / ".autoskillit" / ".secrets.yaml", True, True),
    ]

    merged: dict[str, Any] = {}
    for path, should_validate, is_secrets in _layers:
        if path.is_file():
            data = load_yaml(path)
            if isinstance(data, dict):
                if should_validate:
                    validate_layer_keys(data, path, is_secrets_layer=is_secrets)
                _apply_layer(merged, data)
            elif data is not None:
                raise ConfigSchemaError(
                    f"Invalid configuration in {str(path)!r}: "
                    f"expected a YAML mapping at the top level, "
                    f"got {type(data).__name__!r}."
                )

    # Write to a temp file so Dynaconf can load it and apply env var overrides.
    # Dynaconf reads files lazily; we trigger eager loading before the file is
    # deleted so the in-memory cache remains valid.
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as tmp:
        tmp.write(dump_yaml_str(merged))
        tmp_path = Path(tmp.name)

    try:
        d = Dynaconf(
            envvar_prefix="AUTOSKILLIT",
            preload=[str(tmp_path)],
            settings_files=[],
            merge_enabled=False,
            load_dotenv=False,
            environments=False,
        )
        d.as_dict()  # trigger eager load so the temp file can be safely deleted
    finally:
        tmp_path.unlink(missing_ok=True)

    return d


def load_config(project_dir: Path | None = None) -> AutomationConfig:
    """Load layered config: defaults < user < project < secrets < env vars."""
    return AutomationConfig.from_dynaconf(_make_dynaconf(project_dir))
