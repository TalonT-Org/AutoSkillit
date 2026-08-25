"""Retention-decision registry for the reclamation retention-audit AST scanner (S2-2).

Mirrors AUDITED_DESTRUCTIVE_TASKFILE_OPS's bidirectional shape: every branch the scanner
finds that skips reclaiming a candidate must have an exact entry here, and every entry here
must still match something the scanner finds -- an unregistered branch and a stale entry
both fail `tests/arch/test_reclamation_retention_audit.py::test_every_retention_branch_is_audited`.

Keys are `"<dotted_path>::L<lineno>"` -- mechanically derivable from the AST (a `continue`/
`break` statement anywhere in the target function, or a `return` statement outside any loop
and not the function's final top-level statement) -- so the scanner does not need to guess a
human-chosen semantic label. The `justification` on each entry carries the semantic meaning.

**Scope, stated honestly.** The plan names eight reclaimers for this audit. Four of them --
`workspace/clone_registry.py::CloneRegistry.candidates`, `cli/_workspace.py`'s worktree-age
filter, `workspace/_projection_cache.py::prune_stale_projections`'s `active_key` comprehension
filter, `hooks/_capture/_sweep.py::sweep_one` (whose `CarrierLeaseLive` predicate is raised in
a *different* function than the one that catches it), and `core/_plugin_cache.py`'s
`PluginArtifactRetirementEngine.try_reclaim` (whose many early-return SKIP outcomes are mixed
with several early-return SUCCESS outcomes -- RECLAIMED, RECORD_REMOVED -- inside deeply
nested `try`/`finally` blocks, so a syntax-only "every return is a skip" rule would
misclassify its own successful-completion paths) -- route their retention decision through a
shape a continue/break/return walk cannot audit without either a materially more complex
classifier (cross-function dataflow tracing, semantic understanding of which enum members
are "success") or a refactor whose blast radius does not belong in this pass.

This registry therefore covers the three reclaimers whose retention logic already has the
continue/break/skip-all-return shape the scanner can verify mechanically:
`scripts/pytest_tmp_lifecycle.py::_reap`, `scripts/pytest_tmp_lifecycle.py::_safe_candidates`,
and `fleet/_dispatch_reaper.py::reap_stale_dispatches`. Extending coverage to the deferred
reclaimers is follow-up work, not silently dropped: each is named above with its file and
function so a future pass can pick it up directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from autoskillit.core.runtime import Revocability

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_ROOT = REPO_ROOT / "scripts"
SRC_ROOT = REPO_ROOT / "src" / "autoskillit"


@dataclass(frozen=True, slots=True)
class RetentionDecision:
    """A branch that skips reclamation because of a liveness/evidence/age predicate."""

    revocability: Revocability
    justification: str
    bounded_by: str | None = None

    def __post_init__(self) -> None:
        if len(self.justification.split()) < 6:
            raise ValueError(f"justification too short: {self.justification!r}")
        if self.revocability is Revocability.MONOTONIC and self.bounded_by is None:
            raise ValueError("a MONOTONIC retention entry must name a bound that overrides it")


@dataclass(frozen=True, slots=True)
class SafetyDecision:
    """A branch that skips reclamation for a reason other than liveness/evidence/age --
    an inspection failure, a type/ownership guard, or (dispatch reaper) a `continue` that
    fires *after* the reclaim action already happened this iteration, not before it.
    """

    justification: str

    def __post_init__(self) -> None:
        if len(self.justification.split()) < 6:
            raise ValueError(f"justification too short: {self.justification!r}")


#: Target reclaimer functions the scanner walks. Keys are the dotted paths used below.
RECLAIMER_TARGETS: dict[str, tuple[Path, str]] = {
    "scripts.pytest_tmp_lifecycle::_reap": (SCRIPTS_ROOT / "pytest_tmp_lifecycle.py", "_reap"),
    "scripts.pytest_tmp_lifecycle::_safe_candidates": (
        SCRIPTS_ROOT / "pytest_tmp_lifecycle.py",
        "_safe_candidates",
    ),
    "fleet._dispatch_reaper::reap_stale_dispatches": (
        SRC_ROOT / "fleet" / "_dispatch_reaper.py",
        "reap_stale_dispatches",
    ),
}


_R = "scripts.pytest_tmp_lifecycle::_reap"
_S = "scripts.pytest_tmp_lifecycle::_safe_candidates"
_D = "fleet._dispatch_reaper::reap_stale_dispatches"

AUDITED_RETENTION_DECISIONS: dict[str, RetentionDecision | SafetyDecision] = {
    # -- scripts.pytest_tmp_lifecycle::_reap --
    f"{_R}::L429": SafetyDecision(
        "Scan-level failure retains every candidate rather than treating an empty result "
        "as absence of evidence; the fail-closed contract tests/AGENTS.md documents."
    ),
    f"{_R}::L436": SafetyDecision(
        "The generation _setup is currently claiming is excluded from its own reap pass."
    ),
    f"{_R}::L440": SafetyDecision(
        "FileNotFoundError on lstat means the candidate is already gone; nothing to reclaim."
    ),
    f"{_R}::L443": SafetyDecision(
        "An OSError inspecting the candidate is an inspection failure, not eligibility evidence."
    ),
    f"{_R}::L446": SafetyDecision(
        "A symlink or non-directory entry under the platform root is a safety exclusion, "
        "never a reclamation candidate regardless of any evidence."
    ),
    f"{_R}::L449": SafetyDecision(
        "A candidate owned by a different uid is out of this reaper's authority to touch."
    ),
    f"{_R}::L459": RetentionDecision(
        Revocability.REVOCABLE,
        "A live or indeterminate owner is retained unconditionally; only provably dead may "
        "ever be reclaimed, per the three-outcome liveness contract.",
    ),
    f"{_R}::L462": RetentionDecision(
        Revocability.REVOCABLE,
        "A dead-owner generation still holding a revocable kernel reference (cwd/fd/maps) "
        "is retained -- proof of present use overrides a dead owner marker.",
    ),
    f"{_R}::L468": RetentionDecision(
        Revocability.REVOCABLE,
        "A dead owner within the grace window is retained by the normal reap pass, but is "
        "eligible for early reclamation under capacity pressure.",
        bounded_by="ReclamationBound (select_overflow eligibility)",
    ),
    f"{_R}::L475": RetentionDecision(
        Revocability.REVOCABLE,
        "A corrupt marker treated as valid-dead is still retained under a revocable "
        "reference, exactly like a parseable marker would be.",
    ),
    f"{_R}::L478": RetentionDecision(
        Revocability.REVOCABLE,
        "A corrupt marker is grace-gated on its own mtime, never demoted to the weaker "
        "markerless/legacy-age path, matching a valid dead marker within grace.",
        bounded_by="ReclamationBound (select_overflow eligibility)",
    ),
    f"{_R}::L482": RetentionDecision(
        Revocability.MONOTONIC,
        "A markerless candidate is retained by either a revocable reference or a monotonic "
        "snapshot reference -- the only branch where monotonic evidence may protect, since "
        "there is no owner marker to supply a sound liveness proof instead.",
        bounded_by="never bound-reclaimable (no owner to prove provably dead)",
    ),
    f"{_R}::L488": SafetyDecision(
        "A markerless candidate younger than legacy_age_minutes might be another "
        "concurrent _setup mid-creation; never touched by the bound, only by this age gate."
    ),
    # -- scripts.pytest_tmp_lifecycle::_safe_candidates --
    f"{_S}::L357": SafetyDecision(
        "Cannot normalize private-root permissions; the whole private-root scan is skipped "
        "rather than risk enumerating an untrusted-mode directory."
    ),
    # -- fleet._dispatch_reaper::reap_stale_dispatches --
    f"{_D}::L141": SafetyDecision(
        "No campaign state file at all; nothing to reap for this campaign."
    ),
    f"{_D}::L146": SafetyDecision(
        "The state file could not be parsed; an unreadable state must not be interpreted "
        "as zero running dispatches."
    ),
    f"{_D}::L151": SafetyDecision(
        "Nothing in RUNNING status for this campaign; the candidate set is empty."
    ),
    f"{_D}::L159": RetentionDecision(
        Revocability.REVOCABLE,
        "A reaper never reaps its own campaign's siblings -- self-exclusion is a live-owner "
        "equivalent, verified by the caller's own campaign_id match, not by any /proc read.",
    ),
    f"{_D}::L173": RetentionDecision(
        Revocability.REVOCABLE,
        "A caller-declared protected dispatch id set is honoured unconditionally, the same "
        "self-exclusion family as the own-campaign skip-all.",
    ),
    f"{_D}::L184": RetentionDecision(
        Revocability.REVOCABLE,
        "A dispatch younger than min_reap_age_seconds is retained -- the textbook grace "
        "period gate on process age.",
    ),
    f"{_D}::L192": SafetyDecision(
        "pid == 0 is a reap outcome (marks the dispatch dead), not an eligibility skip -- "
        "the continue here follows the reclaim action, it does not precede it."
    ),
    f"{_D}::L210": SafetyDecision(
        "A boot-id mismatch is a reap outcome (marks the dispatch pid-recycled), not an "
        "eligibility skip -- the continue follows the reclaim action."
    ),
    f"{_D}::L214": SafetyDecision(
        "psutil.pid_exists() false is a reap outcome (marks the dispatch dead), not an "
        "eligibility skip -- the continue follows the reclaim action."
    ),
    f"{_D}::L237": SafetyDecision(
        "psutil.NoSuchProcess during create_time comparison is a reap outcome (marks the "
        "dispatch dead), not an eligibility skip."
    ),
    f"{_D}::L252": RetentionDecision(
        Revocability.REVOCABLE,
        "An active dispatch heartbeat (a live kernel-observable mtime freshness check) "
        "retains the dispatch -- the domain equivalent of a revocable kernel reference.",
    ),
    f"{_D}::L275": SafetyDecision(
        "kill_process_tree raised; execution failure, not an eligibility gate on the "
        "candidate itself."
    ),
    f"{_D}::L287": RetentionDecision(
        Revocability.REVOCABLE,
        "Survivors reported by kill_process_tree's cleanup_result mean the process may "
        "still be alive -- the dispatch record is deliberately left RUNNING for a retry, "
        "an observed-liveness result standing in for a direct /proc reference check.",
    ),
}
