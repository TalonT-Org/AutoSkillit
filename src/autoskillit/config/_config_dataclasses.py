"""Leaf configuration dataclasses for AutomationConfig."""

from __future__ import annotations

import inspect
import os
from dataclasses import dataclass, field
from typing import ClassVar

from autoskillit.core import (
    DRY_WALKTHROUGH_VERIFIED_MARKER,
    LABEL_LIFECYCLE_REGISTRY,
    IssueLabelState,
    OutputFormat,
    get_logger,
)

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
    marker: str = DRY_WALKTHROUGH_VERIFIED_MARKER
    skill_names: set[str] = field(
        default_factory=lambda: {
            "/implement-worktree",
            "/implement-worktree-no-merge",
        }
    )
    allowed_plan_dirs: set[str] = field(default_factory=lambda: {"make-plan", "rectify"})


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
    idle_output_timeout: int = 1000
    max_suppression_seconds: int = 1800
    stream_idle_timeout_ms: int = 600000

    # Safety margin (ms) above exit_after_stop_delay_ms that
    # natural_exit_grace_seconds must cover so the drain window can absorb
    # the CLI self-exit delay without a race.
    _EXIT_GRACE_BUFFER_MS: ClassVar[int] = 500

    def __post_init__(self) -> None:
        if self.stream_idle_timeout_ms < 0:
            raise ValueError(
                f"stream_idle_timeout_ms={self.stream_idle_timeout_ms} must be >= 0 "
                "(use 0 to disable injection)."
            )
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
class CoreRunConfig:
    default_model: str = "sonnet"
    model_override: str | None = None
    provider: str = "anthropic"
    step_overrides: dict[str, str] = field(default_factory=dict)
    recipe_overrides: dict[str, dict[str, str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.default_model:
            raise ValueError("CoreRunConfig.default_model must not be empty")
        for step, model_val in self.step_overrides.items():
            if not isinstance(model_val, str):
                raise ValueError(
                    f"step_overrides[{step!r}] must be a string, got {type(model_val).__name__!r}"
                )
        for recipe, overrides in self.recipe_overrides.items():
            if not isinstance(overrides, dict):
                raise ValueError(
                    f"recipe_overrides[{recipe!r}] must be a dict, "
                    f"got {type(overrides).__name__!r}"
                )
            for step, model_val in overrides.items():
                if not isinstance(model_val, str):
                    raise ValueError(
                        f"recipe_overrides[{recipe!r}][{step!r}] must be a string, "
                        f"got {type(model_val).__name__!r}"
                    )


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
    queued_label: str = "queued"
    allowed_labels: list[str] = field(default_factory=list)

    def check_label_allowed(self, label: str) -> str | None:
        """Return None if label is permitted, or an error message string if not.

        When allowed_labels is empty, all labels are permitted (unrestricted/opt-out mode).
        Lifecycle labels (QUEUED, IN_PROGRESS, STAGED, FAIL) are always permitted.
        """
        if not self.allowed_labels:
            return None
        if self.state_for_label(label) is not None:
            return None
        if label not in self.allowed_labels:
            allowed_sorted = sorted(self.allowed_labels)
            return (
                f"Label '{label}' is not in the configured allowed labels. "
                f"Allowed: {allowed_sorted}. "
                f"Add '{label}' to github.allowed_labels in your config to permit it."
            )
        return None

    def label_for_state(self, state: IssueLabelState) -> str:
        _map: dict[IssueLabelState, str] = {
            IssueLabelState.QUEUED: self.queued_label,
            IssueLabelState.IN_PROGRESS: self.in_progress_label,
            IssueLabelState.STAGED: self.staged_label,
            IssueLabelState.FAIL: self.fail_label,
        }
        if state not in _map:
            raise ValueError(f"No label configured for state {state!r}")
        return _map[state]

    def state_for_label(self, label: str) -> IssueLabelState | None:
        for state in IssueLabelState:
            if self.label_for_state(state) == label:
                return state
        return None

    def labels_for_states(self, states: frozenset[IssueLabelState]) -> list[str]:
        return [self.label_for_state(s) for s in states]

    def resolve_label_metadata(self, label: str) -> tuple[str, str, list[str]]:
        """Return (color, description, remove_labels) for a lifecycle label.

        Uses the registry when label maps to a lifecycle state; falls back to
        IN_PROGRESS defaults for custom labels not in the registry.
        """

        state = self.state_for_label(label)
        if state is not None:
            label_def = LABEL_LIFECYCLE_REGISTRY[state]
            return (
                label_def.color,
                label_def.description,
                self.labels_for_states(label_def.removes_on_entry),
            )
        return (
            "fbca04",
            "Issue is actively being processed by a pipeline session",
            [self.fail_label],
        )

    def all_lifecycle_labels(self) -> list[str]:
        return [self.label_for_state(s) for s in IssueLabelState]

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
class DiagnosticsConfig:
    post_run_analysis: bool = False


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
class ReviewConfig:
    local_review_rounds: int = 2

    def __post_init__(self) -> None:
        if self.local_review_rounds < 0:
            raise ValueError(
                f"ReviewConfig.local_review_rounds must be >= 0, got {self.local_review_rounds}"
            )


_VALID_ADVERSARIAL_REVIEW_LEVELS: frozenset[str] = frozenset({"auto", "full", "none"})


@dataclass
class PlanConfig:
    adversarial_review_level: str = "auto"

    def __post_init__(self) -> None:
        if self.adversarial_review_level not in _VALID_ADVERSARIAL_REVIEW_LEVELS:
            raise ValueError(
                f"PlanConfig.adversarial_review_level must be one of "
                f"{sorted(_VALID_ADVERSARIAL_REVIEW_LEVELS)}, "
                f"got {self.adversarial_review_level!r}"
            )


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


_MAX_CONCURRENT_DISPATCHES = 8


@dataclass
class FleetConfig:
    default_timeout_sec: int = 3600
    max_concurrent_dispatches: int = 3  # default; ceiling is _MAX_CONCURRENT_DISPATCHES
    max_total_issues: int = 12
    enable_deadline_extension: bool = True
    max_extension_seconds: float = 7200
    idle_output_timeout: float = 1800
    acquire_timeout_sec: float = 300.0
    max_issues_per_food_truck: int = 3

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
        if self.max_concurrent_dispatches > _MAX_CONCURRENT_DISPATCHES:
            raise ValueError(
                f"max_concurrent_dispatches must be <= {_MAX_CONCURRENT_DISPATCHES},"
                f" got {self.max_concurrent_dispatches}"
            )
        if self.max_total_issues < 1:
            raise ValueError(f"max_total_issues must be >= 1, got {self.max_total_issues}")
        if self.max_extension_seconds <= 0:
            raise ValueError(
                f"max_extension_seconds must be positive, got {self.max_extension_seconds}"
            )
        if self.idle_output_timeout < 0:
            raise ValueError(
                f"idle_output_timeout must be non-negative, got {self.idle_output_timeout}"
            )
        if self.acquire_timeout_sec <= 0:
            raise ValueError(
                f"acquire_timeout_sec must be positive, got {self.acquire_timeout_sec}"
            )
        if self.max_issues_per_food_truck < 1:
            raise ValueError(
                f"max_issues_per_food_truck must be >= 1, got {self.max_issues_per_food_truck}"
            )
        if self.max_issues_per_food_truck > self.max_total_issues:
            raise ValueError(
                f"max_issues_per_food_truck must be <= max_total_issues"
                f" ({self.max_total_issues}), got {self.max_issues_per_food_truck}"
            )


@dataclass(frozen=True, slots=True)
class ProviderProfileDef:
    """Static definition of a named LLM provider profile.

    Used as an element in a provider registry. Immutable after construction.
    """

    name: str
    base_url: str | None = None
    timeout_seconds: int | None = None
    api_key_env: str | None = None
    context_window: int | None = None
    raw_env: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.timeout_seconds is not None and self.timeout_seconds < 0:
            raise ValueError(f"timeout_seconds must be non-negative, got {self.timeout_seconds}")
        if self.context_window is not None and self.context_window <= 0:
            raise ValueError(f"context_window must be positive, got {self.context_window}")


@dataclass
class ProvidersConfig:
    """Configuration for alternative LLM provider routing.

    API keys must live in .secrets.yaml or environment variables and must
    never be committed to version-controlled config files.
    """

    default_provider: str | None = None
    profiles: dict[str, dict[str, str | None]] = field(default_factory=dict)
    step_overrides: dict[str, str] = field(default_factory=dict)
    recipe_overrides: dict[str, dict[str, str]] = field(default_factory=dict)
    model_overrides: dict[str, dict[str, str]] = field(default_factory=dict)
    provider_retry_limit: int = 2

    def __post_init__(self) -> None:
        if self.provider_retry_limit < 1:
            raise ValueError(f"provider_retry_limit must be >= 1, got {self.provider_retry_limit}")
        for name, profile in self.profiles.items():
            for k, v in profile.items():
                if v is not None and not isinstance(v, str):
                    raise ValueError(
                        f"profiles[{name!r}][{k!r}] must be a string or null, "
                        f"got {type(v).__name__!r}"
                    )
        for recipe, overrides in self.recipe_overrides.items():
            if not isinstance(overrides, dict):
                raise ValueError(
                    f"recipe_overrides[{recipe!r}] must be a dict, "
                    f"got {type(overrides).__name__!r}"
                )
            for step, provider in overrides.items():
                if not isinstance(provider, str):
                    raise ValueError(
                        f"recipe_overrides[{recipe!r}][{step!r}] must be a string, "
                        f"got {type(provider).__name__!r}"
                    )
        for recipe, overrides in self.model_overrides.items():
            if not isinstance(overrides, dict):
                raise ValueError(
                    f"model_overrides[{recipe!r}] must be a dict, got {type(overrides).__name__!r}"
                )
            for step, model_val in overrides.items():
                if not isinstance(model_val, str):
                    raise ValueError(
                        f"model_overrides[{recipe!r}][{step!r}] must be a string, "
                        f"got {type(model_val).__name__!r}"
                    )
        known = set(self.profiles.keys()) | {"anthropic"}
        for step, profile_name in self.step_overrides.items():
            if profile_name not in known:
                logger.warning(
                    "step_override_references_unknown_profile",
                    step=step,
                    profile=profile_name,
                    known_profiles=sorted(known),
                )
        for recipe, step_map in self.recipe_overrides.items():
            for step, profile_name in step_map.items():
                if profile_name not in known:
                    logger.warning(
                        "recipe_override_references_unknown_profile",
                        recipe=recipe,
                        step=step,
                        profile=profile_name,
                        known_profiles=sorted(known),
                    )

    @property
    def resolved_profiles(self) -> dict[str, ProviderProfileDef]:
        result: dict[str, ProviderProfileDef] = {}
        for name, raw_dict in self.profiles.items():
            copy = {k: v for k, v in raw_dict.items() if v is not None}
            base_url = copy.pop("base_url", None)
            timeout_str = copy.pop("timeout_seconds", None)
            api_key_env = copy.pop("api_key_env", None)
            context_str = copy.pop("context_window", None)
            result[name] = ProviderProfileDef(
                name=name,
                base_url=base_url,
                timeout_seconds=int(timeout_str)
                if timeout_str is not None and timeout_str != ""
                else None,
                api_key_env=api_key_env,
                context_window=int(context_str)
                if context_str is not None and context_str != ""
                else None,
                raw_env=copy,
            )
        return result


@dataclass
class AgentBackendConfig:
    backend: str = "claude-code"

    def __post_init__(self) -> None:
        if not self.backend:
            raise ValueError("backend must not be empty")
