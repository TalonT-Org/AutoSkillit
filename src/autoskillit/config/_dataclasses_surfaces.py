"""Surface dataclasses — skill subsets, packs, workspace layout, worktree setup.

Owns: ``SkillsConfig`` (with tier-disjoint validation in ``__post_init__``),
``SubsetsConfig``, ``PacksConfig``, ``WorkspaceConfig``, ``WorktreeSetupConfig``.
"""

from __future__ import annotations

from dataclasses import dataclass, field


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
class WorktreeSetupConfig:
    command: list[str] | None = None
