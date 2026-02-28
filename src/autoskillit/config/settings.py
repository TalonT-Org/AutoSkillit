"""Configuration loading with layered YAML resolution.

Resolution order: defaults > user (~/.autoskillit/config.yaml)
> project (.autoskillit/config.yaml).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autoskillit.core import load_yaml


@dataclass
class TestCheckConfig:
    command: list[str] = field(default_factory=lambda: ["task", "test-all"])
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


def load_config(project_dir: Path | None = None) -> AutomationConfig:
    """Load layered config: defaults < user config < project config."""
    config = AutomationConfig()

    user_path = Path.home() / ".autoskillit" / "config.yaml"
    if user_path.is_file():
        data = load_yaml(user_path)
        if isinstance(data, dict):
            _merge_into(config, data)

    if project_dir is not None:
        project_path = project_dir / ".autoskillit" / "config.yaml"
        if project_path.is_file():
            data = load_yaml(project_path)
            if isinstance(data, dict):
                _merge_into(config, data)

    return config


def _merge_into(config: AutomationConfig, data: dict[str, Any]) -> None:
    """Apply YAML dict values onto dataclass fields."""
    for section_field in dataclasses.fields(config):
        section_data = data.get(section_field.name)
        if not isinstance(section_data, dict):
            continue
        section_obj = getattr(config, section_field.name)
        for sub_field in dataclasses.fields(section_obj):
            if sub_field.name not in section_data:
                continue
            value = section_data[sub_field.name]
            # Convert lists to sets where the dataclass type is set
            if isinstance(getattr(section_obj, sub_field.name), set) and isinstance(value, list):
                value = set(value)
            setattr(section_obj, sub_field.name, value)
