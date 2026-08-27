"""Workflow dataclasses — review/plan/branch/CI/migration knobs.

Owns: ``ReviewConfig``, ``PlanConfig``, ``BranchingConfig``, ``CIConfig``,
``MigrationConfig``, plus the ``_VALID_ADVERSARIAL_REVIEW_LEVELS`` allowlist
that ``PlanConfig.__post_init__`` validates against.
"""

from __future__ import annotations

from dataclasses import dataclass, field


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
class MigrationConfig:
    suppressed: list[str] = field(default_factory=list)
