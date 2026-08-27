"""Test-gating dataclasses owned by the worktree/test pipeline.

Owns: ``TestCheckConfig``, ``ClassifyFixConfig``, ``ResetWorkspaceConfig``,
``ImplementGateConfig``, ``SafetyConfig``, ``ReadDbConfig``, plus the
``_DEFAULT_COMMAND`` tuple and the unique mutable-list sentinel
``_COMMAND_UNSET`` that ``TestCheckConfig.__post_init__`` uses to detect whether
``command`` was explicitly supplied.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from autoskillit.config._dataclasses_shared import ConfigSchemaError
from autoskillit.core import DRY_WALKTHROUGH_VERIFIED_MARKER

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
