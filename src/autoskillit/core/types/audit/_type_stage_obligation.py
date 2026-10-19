"""Recipe-owned outcomes that a plan set can require after implementation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import assert_never

__all__ = ["StageObligation", "StageObligationDef"]


class StageObligation(StrEnum):
    PR_OPENED = "pr_opened"
    PR_LEFT_UNMERGED = "pr_left_unmerged"

    def unavailable_reason(self, *, open_pr: bool, auto_merge: bool) -> str | None:
        match self:
            case StageObligation.PR_OPENED:
                if not open_pr:
                    return "open_pr is false, so this run cannot open a pull request"
                return None
            case StageObligation.PR_LEFT_UNMERGED:
                if not open_pr:
                    return "open_pr is false, so this run cannot leave a pull request unmerged"
                if auto_merge:
                    return "auto_merge is true, so this run merges the pull request"
                return None
            case unreachable:
                assert_never(unreachable)

    def violation(
        self, *, pr_state: str | None, pr_base: str | None, base_branch: str
    ) -> str | None:
        if pr_state is None:
            return "no pull request could be observed"
        if pr_base != base_branch:
            target = pr_base if pr_base is not None else "unknown"
            return f"pull request targets {target}, not {base_branch}"
        match self:
            case StageObligation.PR_OPENED:
                if pr_state in {"OPEN", "MERGED"}:
                    return None
                return f"pull request is {pr_state}, not OPEN or MERGED"
            case StageObligation.PR_LEFT_UNMERGED:
                if pr_state == "OPEN":
                    return None
                return f"pull request is {pr_state}, not OPEN"
            case unreachable:
                assert_never(unreachable)


@dataclass(frozen=True, slots=True)
class StageObligationDef:
    requirement_id: str
    obligation: StageObligation

    def __post_init__(self) -> None:
        if not isinstance(self.requirement_id, str) or not self.requirement_id:
            raise ValueError("StageObligationDef.requirement_id must be a non-empty string")
        if not isinstance(self.obligation, StageObligation):
            raise ValueError("StageObligationDef.obligation must be a StageObligation")

    def to_dict(self) -> dict[str, str]:
        return {"requirement_id": self.requirement_id, "obligation": self.obligation.value}
