"""Cycle-free execution identity and backend-resolution types.

This module is intentionally stdlib-only and imports no sibling types.  It is
safe for both the result layer and backend/session-contract layers to consume
without introducing the ``_type_backend`` <-> ``_type_results`` cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, NamedTuple

__all__ = ["BackendPinResolution", "ChildExecutionIdentity", "ExecutionIdentity"]


class BackendPinResolution(NamedTuple):
    """An explicit backend pin and the exact config authority that selected it."""

    backend: str
    tier: str
    key_path: str
    kind: Any = None


@dataclass(frozen=True, slots=True)
class ChildExecutionIdentity:
    """Requested and observed identity for one typed child execution."""

    task_id: str
    role: str
    plan_digest: str
    definition_digest: str
    requested_backend: str = ""
    effective_backend: str = ""
    requested_model: str = ""
    effective_model: str = ""
    requested_effort: str = ""
    effective_effort: str = ""
    session_id: str = ""

    def __post_init__(self) -> None:
        if not self.task_id:
            raise ValueError("child execution identity requires a task id")
        if not self.role:
            raise ValueError("child execution identity requires a role")
        if not self.plan_digest:
            raise ValueError("child execution identity requires a router-plan digest")
        if not self.definition_digest:
            raise ValueError("child execution identity requires a definition digest")

    def to_dict(self) -> dict[str, str]:
        """Return the stable child persistence representation."""
        return {
            "task_id": self.task_id,
            "role": self.role,
            "plan_digest": self.plan_digest,
            "definition_digest": self.definition_digest,
            "requested_backend": self.requested_backend,
            "effective_backend": self.effective_backend,
            "requested_model": self.requested_model,
            "effective_model": self.effective_model,
            "requested_effort": self.requested_effort,
            "effective_effort": self.effective_effort,
            "session_id": self.session_id,
        }


@dataclass(frozen=True, slots=True)
class ExecutionIdentity:
    """Requested and observed identity for one parent and its child executions.

    ``requested_*`` values are launch intent.  ``effective_*`` values are
    populated only from backend-owned execution evidence (for Codex, rollout
    ``session_meta`` and ``turn_context`` records).  Keeping both sets prevents
    generated configuration from being mistaken for proof of what executed.
    """

    requested_parent_backend: str = ""
    effective_parent_backend: str = ""
    requested_parent_model: str = ""
    effective_parent_model: str = ""
    requested_parent_effort: str = ""
    effective_parent_effort: str = ""
    cli_version: str = ""
    override_tier: str = ""
    override_key_path: str = ""
    parent_session_id: str = ""
    children: tuple[ChildExecutionIdentity, ...] = ()

    def __post_init__(self) -> None:
        ordered = tuple(sorted(self.children, key=lambda child: child.task_id))
        task_ids = tuple(child.task_id for child in ordered)
        if len(set(task_ids)) != len(task_ids):
            raise ValueError("child execution task ids must be unique")
        object.__setattr__(self, "children", ordered)

    @classmethod
    def empty(cls) -> ExecutionIdentity:
        """Return the explicit zero-value identity used by non-specialized sessions."""
        return cls()

    def to_dict(self) -> dict[str, object]:
        """Return the stable persistence representation."""
        return {
            "requested_parent_backend": self.requested_parent_backend,
            "effective_parent_backend": self.effective_parent_backend,
            "requested_parent_model": self.requested_parent_model,
            "effective_parent_model": self.effective_parent_model,
            "requested_parent_effort": self.requested_parent_effort,
            "effective_parent_effort": self.effective_parent_effort,
            "cli_version": self.cli_version,
            "override_tier": self.override_tier,
            "override_key_path": self.override_key_path,
            "parent_session_id": self.parent_session_id,
            "children": [child.to_dict() for child in self.children],
        }
