"""Configuration loading with layered YAML resolution.

Resolution order: defaults → user (~/.automation-mcp/config.yaml) → project (.automation-mcp/config.yaml).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class TestCheckConfig:
    command: list[str] = field(default_factory=lambda: ["task", "test-check"])
    timeout: int = 600


@dataclass
class ClassifyFixConfig:
    path_prefixes: list[str] = field(default_factory=list)


@dataclass
class ResetExecutorConfig:
    command: list[str] | None = None
    preserve_dirs: set[str] = field(default_factory=lambda: {".agent_data", "plans"})


@dataclass
class ImplementGateConfig:
    marker: str = "Dry-walkthrough verified = TRUE"
    skill_names: set[str] = field(
        default_factory=lambda: {"/implement-worktree", "/implement-worktree-no-merge"}
    )


@dataclass
class SafetyConfig:
    playground_guard: bool = True
    require_dry_walkthrough: bool = True
    test_gate_on_merge: bool = True


@dataclass
class SkillsConfig:
    resolution_order: list[str] = field(
        default_factory=lambda: ["project", "user", "bundled"]
    )


@dataclass
class AutomationConfig:
    test_check: TestCheckConfig = field(default_factory=TestCheckConfig)
    classify_fix: ClassifyFixConfig = field(default_factory=ClassifyFixConfig)
    reset_executor: ResetExecutorConfig = field(default_factory=ResetExecutorConfig)
    implement_gate: ImplementGateConfig = field(default_factory=ImplementGateConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    skills: SkillsConfig = field(default_factory=SkillsConfig)


def load_config(project_dir: Path | None = None) -> AutomationConfig:
    """Load config: project .automation-mcp/config.yaml > ~/.automation-mcp/config.yaml > defaults."""
    config = AutomationConfig()

    user_path = Path.home() / ".automation-mcp" / "config.yaml"
    if user_path.is_file():
        _merge_into(config, _load_yaml(user_path))

    if project_dir is not None:
        project_path = project_dir / ".automation-mcp" / "config.yaml"
        if project_path.is_file():
            _merge_into(config, _load_yaml(project_path))

    return config


def _load_yaml(path: Path) -> dict[str, Any]:
    """Read a YAML file and return its contents as a dict."""
    data = yaml.safe_load(path.read_text())
    return data if isinstance(data, dict) else {}


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
