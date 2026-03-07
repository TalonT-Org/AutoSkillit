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
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autoskillit.core import OutputFormat, dump_yaml_str, load_yaml, pkg_root

if TYPE_CHECKING:
    from dynaconf import Dynaconf


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
    default: str | None = None
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
    cache_max_age: int = 60
    credentials_path: str = "~/.claude/.credentials.json"
    cache_path: str = "~/.claude/autoskillit_quota_cache.json"


@dataclass
class GitHubConfig:
    token: str | None = None
    default_repo: str | None = None
    in_progress_label: str = "in-progress"


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
                default=val(mc, "default", _mc["default"]) or None,
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
        )


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

    File layers (defaults, user, project, secrets) are merged in advance using
    _merge_yaml_layers(), which applies dict deep-merge + list-replace semantics.
    The merged result is written to a temp YAML file so that Dynaconf can apply
    env var overrides (AUTOSKILLIT_SECTION__KEY) on top.

    Deferred import keeps dynaconf off the module-level import chain.
    """
    from dynaconf import Dynaconf  # noqa: PLC0415

    defaults_path = pkg_root() / "config" / "defaults.yaml"
    root = project_dir or Path.cwd()

    merged = _merge_yaml_layers(
        defaults_path,
        Path.home() / ".autoskillit" / "config.yaml",
        root / ".autoskillit" / "config.yaml",
        root / ".autoskillit" / ".secrets.yaml",
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
