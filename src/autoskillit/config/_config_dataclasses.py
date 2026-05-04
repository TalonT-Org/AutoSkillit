"""Leaf configuration dataclasses for AutomationConfig."""

from __future__ import annotations

import inspect
import os
from dataclasses import dataclass, field
from typing import ClassVar

from autoskillit.core import OutputFormat, get_logger

logger = get_logger(__name__)


class ConfigSchemaError(ValueError):
    """Raised when a config YAML layer contains unrecognized or misplaced keys."""


_SECRETS_ONLY_KEYS: frozenset[str] = frozenset({"github.token"})
_METADATA_KEYS: frozenset[str] = frozenset({"version"})


_DEFAULT_COMMAND: tuple[str, ...] = ("task", "test-check")

# Unique sentinel object — identity check in __post_init__ detects whether
# `command` was explicitly supplied by the caller or left at its default.
_COMMAND_UNSET: list[str] = []


@dataclass
class TestCheckConfig:
    command: list[str] = field(default_factory=lambda: _COMMAND_UNSET)
    timeout: int = 600
    filter_mode: str | None = None
    base_ref: str | None = None
    commands: list[list[str]] | None = None

    def __post_init__(self) -> None:
        if self.command is _COMMAND_UNSET:
            self.command = list(_DEFAULT_COMMAND)
        elif self.commands is not None:
            raise ConfigSchemaError(
                "test_check: 'command' and 'commands' are mutually exclusive; "
                "omit 'command' when using 'commands'"
            )

    @property
    def effective_commands(self) -> list[list[str]]:
        return self.commands if self.commands is not None else [self.command]


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
            "/implement-worktree",
            "/implement-worktree-no-merge",
        }
    )


@dataclass
class SafetyConfig:
    reset_guard_marker: str = ".autoskillit-workspace"
    require_dry_walkthrough: bool = True
    test_gate_on_merge: bool = True
    protected_branches: list[str] = field(default_factory=lambda: ["main", "develop", "stable"])


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
    exit_after_stop_delay_ms: int = 2000
    natural_exit_grace_seconds: float = 3.0
    idle_output_timeout: int = 600
    max_suppression_seconds: int = 1800

    # Safety margin (ms) above exit_after_stop_delay_ms that
    # natural_exit_grace_seconds must cover so the drain window can absorb
    # the CLI self-exit delay without a race.
    _EXIT_GRACE_BUFFER_MS: ClassVar[int] = 500

    def __post_init__(self) -> None:
        required_ms = self.exit_after_stop_delay_ms + self._EXIT_GRACE_BUFFER_MS
        # Convert seconds → ms for the comparison
        if self.natural_exit_grace_seconds * 1000 < required_ms:
            raise ValueError(
                f"natural_exit_grace_seconds={self.natural_exit_grace_seconds} is too small: "
                f"{self.natural_exit_grace_seconds * 1000:.0f}ms < "
                f"{required_ms}ms (exit_after_stop_delay_ms + {self._EXIT_GRACE_BUFFER_MS}). "
                "Increase natural_exit_grace_seconds so the drain window can absorb the "
                "CLI self-exit delay."
            )

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
    short_window_enabled: bool = True
    long_window_enabled: bool = True
    short_window_threshold: float = 85.0
    long_window_threshold: float = 95.0
    long_window_patterns: list[str] = field(
        default_factory=lambda: ["seven_day", "sonnet", "opus"]
    )
    buffer_seconds: int = 60
    cache_max_age: int = 300
    cache_refresh_interval: int = 240
    credentials_path: str = "~/.claude/.credentials.json"
    cache_path: str = "~/.claude/autoskillit_quota_cache.json"


@dataclass
class GitHubConfig:
    token: str | None = None
    default_repo: str | None = None
    in_progress_label: str = "in-progress"
    staged_label: str = "staged"
    fail_label: str = "fail"
    allowed_labels: list[str] = field(default_factory=list)

    def check_label_allowed(self, label: str) -> str | None:
        """Return None if label is permitted, or an error message string if not.

        When allowed_labels is empty, all labels are permitted (unrestricted/opt-out mode).
        """
        if not self.allowed_labels:
            return None
        if label not in self.allowed_labels:
            allowed_sorted = sorted(self.allowed_labels)
            return (
                f"Label '{label}' is not in the configured allowed labels. "
                f"Allowed: {allowed_sorted}. "
                f"Add '{label}' to github.allowed_labels in your config to permit it."
            )
        return None

    def check_labels_allowed(self, labels: list[str]) -> str | None:
        """Return None if all labels are permitted, or an error message for the first violation.

        When allowed_labels is empty, all labels are permitted (unrestricted/opt-out mode).
        """
        for label in labels:
            if err := self.check_label_allowed(label):
                return err
        return None


@dataclass
class ReportBugConfig:
    timeout: int = 600
    model: str | None = None
    report_dir: str | None = None  # None = resolved temp dir + /bug-reports/
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
    max_sessions: int = 2000

    def __post_init__(self) -> None:
        if self.tmpfs_path != "/dev/shm" or not os.environ.get("PYTEST_CURRENT_TEST"):
            return
        # Only raise when called directly from test code — not from library machinery
        # (e.g. AutomationConfig default_factory, from_dynaconf). We inspect the call
        # frame two levels up: __post_init__ → __init__ (generated) → actual caller.
        frame = inspect.currentframe()
        init_frame = frame.f_back if frame is not None else None
        caller = init_frame.f_back if init_frame is not None else None
        if caller is not None and "/tests/" in (caller.f_code.co_filename or ""):
            raise RuntimeError(
                "LinuxTracingConfig.tmpfs_path is '/dev/shm' but PYTEST_CURRENT_TEST "
                "is set — this test would write to the real shared tmpfs and pollute "
                "production state. Override tmpfs_path with a test-local path, e.g.: "
                "LinuxTracingConfig(tmpfs_path=str(tmp_path)). "
                "Use the isolated_tracing_config fixture for new tests."
            )
        del frame, init_frame, caller


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
    event: str | None = None


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


@dataclass
class PacksConfig:
    enabled: list[str] = field(default_factory=list)


@dataclass
class WorkspaceConfig:
    worktree_root: str | None = None  # null = auto-resolve to ../worktrees/
    runs_root: str | None = None  # null = auto-resolve to ../autoskillit-runs/
    temp_dir: str | None = None  # null = canonical default (see resolve_temp_dir)


@dataclass
class FleetConfig:
    default_timeout_sec: int = 3600
    max_concurrent_dispatches: int = 1

    def validate(self, feature_enabled: bool) -> None:
        """Validate only when the feature is active."""
        if not feature_enabled:
            return
        if self.default_timeout_sec <= 0:
            raise ValueError(
                f"default_timeout_sec must be positive, got {self.default_timeout_sec}"
            )
        if self.max_concurrent_dispatches < 1:
            raise ValueError(
                f"max_concurrent_dispatches must be >= 1, got {self.max_concurrent_dispatches}"
            )


@dataclass
class ProvidersConfig:
    """Configuration for alternative LLM provider routing.

    API keys must live in .secrets.yaml or environment variables and must
    never be committed to version-controlled config files.
    """

    default_provider: str | None = None
    profiles: dict[str, dict[str, str]] = field(default_factory=dict)
    step_overrides: dict[str, str] = field(default_factory=dict)
    provider_retry_limit: int = 2

    def __post_init__(self) -> None:
        if self.provider_retry_limit < 1:
            raise ValueError(f"provider_retry_limit must be >= 1, got {self.provider_retry_limit}")
        for name, profile in self.profiles.items():
            for k, v in profile.items():
                if not isinstance(v, str):
                    raise ValueError(
                        f"profiles[{name!r}][{k!r}] must be a string, got {type(v).__name__!r}"
                    )
