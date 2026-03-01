"""Configuration loading with dynaconf layered resolution.

Resolution order (low → high priority):
  1. Package defaults  (config/defaults.yaml, always loaded)
  2. User config       (~/.autoskillit/config.yaml, if present)
  3. Project config    (.autoskillit/config.yaml, if present)
  4. Secrets file      (.autoskillit/.secrets.yaml, if present)
  5. Environment vars  (AUTOSKILLIT_SECTION__KEY=value)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autoskillit.core import pkg_root

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
    timeout: int = 3600
    heartbeat_marker: str = '"type":"result"'
    stale_threshold: int = 1200  # 20 minutes
    completion_marker: str = "%%ORDER_UP%%"
    completion_drain_timeout: float = 5.0
    exit_after_stop_delay_ms: int = 30000


@dataclass
class RunSkillRetryConfig:
    timeout: int = 7200
    stale_threshold: int = 1200


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
    threshold: float = 80.0
    buffer_seconds: int = 60
    cache_max_age: int = 60
    credentials_path: str = "~/.claude/.credentials.json"
    cache_path: str = "~/.claude/usage_cache.json"


@dataclass
class GitHubConfig:
    token: str | None = None
    default_repo: str | None = None


@dataclass
class AutomationConfig:
    test_check: TestCheckConfig = field(default_factory=TestCheckConfig)
    classify_fix: ClassifyFixConfig = field(default_factory=ClassifyFixConfig)
    reset_workspace: ResetWorkspaceConfig = field(default_factory=ResetWorkspaceConfig)
    implement_gate: ImplementGateConfig = field(default_factory=ImplementGateConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    read_db: ReadDbConfig = field(default_factory=ReadDbConfig)
    run_skill: RunSkillConfig = field(default_factory=RunSkillConfig)
    run_skill_retry: RunSkillRetryConfig = field(default_factory=RunSkillRetryConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    worktree_setup: WorktreeSetupConfig = field(default_factory=WorktreeSetupConfig)
    migration: MigrationConfig = field(default_factory=MigrationConfig)
    token_usage: TokenUsageConfig = field(default_factory=TokenUsageConfig)
    quota_guard: QuotaGuardConfig = field(default_factory=QuotaGuardConfig)
    github: GitHubConfig = field(default_factory=GitHubConfig)

    @classmethod
    def from_dynaconf(cls, d: "Dynaconf") -> "AutomationConfig":
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
        rsr = sec("run_skill_retry")
        mc = sec("model")
        ws = sec("worktree_setup")
        mi = sec("migration")
        tu = sec("token_usage")
        qg = sec("quota_guard")
        gh = sec("github")

        return cls(
            test_check=TestCheckConfig(
                command=list(val(tc, "command", ["task", "test-check"])),
                timeout=int(val(tc, "timeout", 600)),
            ),
            classify_fix=ClassifyFixConfig(
                path_prefixes=list(val(cf, "path_prefixes", [])),
            ),
            reset_workspace=ResetWorkspaceConfig(
                command=_to_optional_list(val(rw, "command", None)),
                preserve_dirs=set(val(rw, "preserve_dirs", [])),
            ),
            implement_gate=ImplementGateConfig(
                marker=str(val(ig, "marker", "Dry-walkthrough verified = TRUE")),
                skill_names=set(
                    val(
                        ig,
                        "skill_names",
                        [
                            "/autoskillit:implement-worktree",
                            "/autoskillit:implement-worktree-no-merge",
                        ],
                    )
                ),
            ),
            safety=SafetyConfig(
                reset_guard_marker=str(
                    val(sf, "reset_guard_marker", ".autoskillit-workspace")
                ),
                require_dry_walkthrough=bool(val(sf, "require_dry_walkthrough", True)),
                test_gate_on_merge=bool(val(sf, "test_gate_on_merge", True)),
            ),
            read_db=ReadDbConfig(
                timeout=int(val(rd, "timeout", 30)),
                max_rows=int(val(rd, "max_rows", 10000)),
            ),
            run_skill=RunSkillConfig(
                timeout=int(val(rs, "timeout", 3600)),
                heartbeat_marker=str(val(rs, "heartbeat_marker", '"type":"result"')),
                stale_threshold=int(val(rs, "stale_threshold", 1200)),
                completion_marker=str(val(rs, "completion_marker", "%%ORDER_UP%%")),
                completion_drain_timeout=float(val(rs, "completion_drain_timeout", 5.0)),
                exit_after_stop_delay_ms=int(val(rs, "exit_after_stop_delay_ms", 30000)),
            ),
            run_skill_retry=RunSkillRetryConfig(
                timeout=int(val(rsr, "timeout", 7200)),
                stale_threshold=int(val(rsr, "stale_threshold", 1200)),
            ),
            model=ModelConfig(
                default=val(mc, "default", None) or None,
                override=val(mc, "override", None) or None,
            ),
            worktree_setup=WorktreeSetupConfig(
                command=_to_optional_list(val(ws, "command", None)),
            ),
            migration=MigrationConfig(
                suppressed=list(val(mi, "suppressed", [])),
            ),
            token_usage=TokenUsageConfig(
                verbosity=str(val(tu, "verbosity", "summary")),
            ),
            quota_guard=QuotaGuardConfig(
                enabled=bool(val(qg, "enabled", True)),
                threshold=float(val(qg, "threshold", 80.0)),
                buffer_seconds=int(val(qg, "buffer_seconds", 60)),
                cache_max_age=int(val(qg, "cache_max_age", 60)),
                credentials_path=str(
                    val(qg, "credentials_path", "~/.claude/.credentials.json")
                ),
                cache_path=str(val(qg, "cache_path", "~/.claude/usage_cache.json")),
            ),
            github=GitHubConfig(
                token=val(gh, "token", None) or None,
                default_repo=val(gh, "default_repo", None) or None,
            ),
        )


def _to_optional_list(value: Any) -> list[str] | None:
    """Return None if value is falsy, else coerce to list[str]."""
    if not value:
        return None
    return list(value)


def _make_dynaconf(project_dir: Path | None = None) -> "Dynaconf":
    """Create a fully-layered Dynaconf instance.

    Deferred import keeps dynaconf off the module-level import chain.
    """
    from dynaconf import Dynaconf  # noqa: PLC0415

    defaults_path = pkg_root() / "config" / "defaults.yaml"
    root = project_dir or Path.cwd()

    return Dynaconf(
        envvar_prefix="AUTOSKILLIT",
        preload=[str(defaults_path)],
        settings_files=[
            str(Path.home() / ".autoskillit" / "config.yaml"),
            str(root / ".autoskillit" / "config.yaml"),
        ],
        secrets=str(root / ".autoskillit" / ".secrets.yaml"),
        load_dotenv=False,
        environments=False,
        merge_enabled=True,
    )


def load_config(project_dir: Path | None = None) -> AutomationConfig:
    """Load layered config: defaults < user < project < secrets < env vars."""
    return AutomationConfig.from_dynaconf(_make_dynaconf(project_dir))
