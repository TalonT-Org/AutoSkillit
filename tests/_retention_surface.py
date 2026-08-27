"""Retention-decision registry for the reclamation retention-audit AST scanner (S2-2).

Mirrors AUDITED_DESTRUCTIVE_TASKFILE_OPS's bidirectional shape: every branch the scanner
finds that skips reclaiming a candidate must have an exact entry here, and every entry here
must still match something the scanner finds -- an unregistered branch and a stale entry
both fail `tests/arch/test_reclamation_retention_audit.py::test_every_retention_branch_is_audited`.

Keys are `"<dotted_path>::L<lineno>"` -- mechanically derivable from the AST (a `continue`/
`break` statement anywhere in the target function, or a `return` statement outside any loop
and not the function's final top-level statement) -- so the scanner does not need to guess a
human-chosen semantic label. The `justification` on each entry carries the semantic meaning.

**Not every continue/break/return is a retention decision.** Per the plan: "classify a branch
as a retention decision only when its condition references a liveness, evidence, or age
predicate... Defensive skips are reported separately under a SAFETY shape." Several of the
newly-covered reclaimers return early on a successful completion (e.g. the true "reclaimed"
path in `try_reclaim`, or a "not registered with git" / "already deleted" report) purely
because that return sits inside a `try`/`with` block rather than being the function's
outermost statement -- the scanner's syntax-only walk cannot distinguish "reports success" from
"skips reclaiming", so those are registered as `SafetyDecision`s that say so explicitly, the
same treatment `fleet._dispatch_reaper::reap_stale_dispatches`'s existing entries already give
a `continue` that follows a reclaim action rather than preceding one.
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
    an inspection failure, a type/ownership guard, or a `continue`/`return` that fires
    *after* the reclaim action (or an equivalent completion) already happened, not before it.
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
    "workspace.session_skill_manager::cleanup_stale": (
        SRC_ROOT / "workspace" / "session_skill_manager.py",
        "cleanup_stale",
    ),
    "workspace.clone_registry::cleanup_candidates": (
        SRC_ROOT / "workspace" / "clone_registry.py",
        "cleanup_candidates",
    ),
    "workspace.worktree::remove_git_worktree": (
        SRC_ROOT / "workspace" / "worktree.py",
        "remove_git_worktree",
    ),
    "workspace.worktree::remove_worktree_sidecar": (
        SRC_ROOT / "workspace" / "worktree.py",
        "remove_worktree_sidecar",
    ),
    "execution._session_retention::apply_session_retention": (
        SRC_ROOT / "execution" / "_session_retention.py",
        "apply_session_retention",
    ),
    "hooks._capture._sweep::sweep_one": (
        SRC_ROOT / "hooks" / "_capture" / "_sweep.py",
        "sweep_one",
    ),
    "workspace._projection_cache::prune_stale_projections": (
        SRC_ROOT / "workspace" / "_projection_cache.py",
        "prune_stale_projections",
    ),
    "workspace._projection_cache::_reconcile_projection_entry": (
        SRC_ROOT / "workspace" / "_projection_cache.py",
        "_reconcile_projection_entry",
    ),
    "core._plugin_artifact_retirement::try_reclaim": (
        SRC_ROOT / "core" / "_plugin_artifact_retirement.py",
        "try_reclaim",
    ),
    "cli.install._plugin_artifact::try_reclaim": (
        SRC_ROOT / "cli" / "install" / "_plugin_artifact.py",
        "try_reclaim",
    ),
    "cli.install._plugin_artifact::sweep_due": (
        SRC_ROOT / "cli" / "install" / "_plugin_artifact.py",
        "sweep_due",
    ),
}


_R = "scripts.pytest_tmp_lifecycle::_reap"
_S = "scripts.pytest_tmp_lifecycle::_safe_candidates"
_D = "fleet._dispatch_reaper::reap_stale_dispatches"
_CS = "workspace.session_skill_manager::cleanup_stale"
_WGW = "workspace.worktree::remove_git_worktree"
_WWS = "workspace.worktree::remove_worktree_sidecar"
_SL = "execution._session_retention::apply_session_retention"
_SW = "hooks._capture._sweep::sweep_one"
_PP = "workspace._projection_cache::prune_stale_projections"
_PRE = "workspace._projection_cache::_reconcile_projection_entry"
_PC = "core._plugin_artifact_retirement::try_reclaim"
_CT = "cli.install._plugin_artifact::sweep_due"

AUDITED_RETENTION_DECISIONS: dict[str, RetentionDecision | SafetyDecision] = {
    # -- scripts.pytest_tmp_lifecycle::_reap --
    f"{_R}::L469": SafetyDecision(
        "Scan-level failure retains every candidate rather than treating an empty result "
        "as absence of evidence; the fail-closed contract tests/AGENTS.md documents."
    ),
    f"{_R}::L476": SafetyDecision(
        "The generation _setup is currently claiming is excluded from its own reap pass."
    ),
    f"{_R}::L480": SafetyDecision(
        "FileNotFoundError on lstat means the candidate is already gone; nothing to reclaim."
    ),
    f"{_R}::L483": SafetyDecision(
        "An OSError inspecting the candidate is an inspection failure, not eligibility evidence."
    ),
    f"{_R}::L486": SafetyDecision(
        "A symlink or non-directory entry under the platform root is a safety exclusion, "
        "never a reclamation candidate regardless of any evidence."
    ),
    f"{_R}::L489": SafetyDecision(
        "A candidate owned by a different uid is out of this reaper's authority to touch."
    ),
    f"{_R}::L499": RetentionDecision(
        Revocability.REVOCABLE,
        "A live or indeterminate owner is retained unconditionally; only provably dead may "
        "ever be reclaimed, per the three-outcome liveness contract.",
    ),
    f"{_R}::L502": RetentionDecision(
        Revocability.REVOCABLE,
        "A dead-owner generation still holding a revocable kernel reference (cwd/fd/maps) "
        "is retained -- proof of present use overrides a dead owner marker.",
    ),
    f"{_R}::L508": RetentionDecision(
        Revocability.REVOCABLE,
        "A dead owner within the grace window is retained by the normal reap pass, but is "
        "eligible for early reclamation under capacity pressure.",
        bounded_by="ReclamationBound (select_overflow eligibility)",
    ),
    f"{_R}::L515": RetentionDecision(
        Revocability.REVOCABLE,
        "A corrupt marker treated as valid-dead is still retained under a revocable "
        "reference, exactly like a parseable marker would be.",
    ),
    f"{_R}::L518": RetentionDecision(
        Revocability.REVOCABLE,
        "A corrupt marker is grace-gated on its own mtime, never demoted to the weaker "
        "markerless/legacy-age path, matching a valid dead marker within grace.",
        bounded_by="ReclamationBound (select_overflow eligibility)",
    ),
    f"{_R}::L522": RetentionDecision(
        Revocability.MONOTONIC,
        "A markerless candidate is retained by either a revocable reference or a monotonic "
        "snapshot reference -- the only branch where monotonic evidence may protect, since "
        "there is no owner marker to supply a sound liveness proof instead.",
        bounded_by="never bound-reclaimable (no owner to prove provably dead)",
    ),
    f"{_R}::L528": SafetyDecision(
        "A markerless candidate younger than legacy_age_minutes might be another "
        "concurrent _setup mid-creation; never touched by the bound, only by this age gate."
    ),
    # -- scripts.pytest_tmp_lifecycle::_safe_candidates --
    f"{_S}::L397": SafetyDecision(
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
    # -- workspace.session_skill_manager::cleanup_stale --
    f"{_CS}::L500": SafetyDecision(
        "The candidate root directory does not exist; nothing here to scan or reclaim."
    ),
    f"{_CS}::L504": SafetyDecision(
        "The session-leases bookkeeping subdirectory itself is not a session; a structural "
        "exclusion, not an eligibility decision."
    ),
    f"{_CS}::L506": SafetyDecision(
        "A non-directory entry under the candidate root is a type guard, never a session "
        "directory this function reclaims."
    ),
    f"{_CS}::L510": RetentionDecision(
        Revocability.REVOCABLE,
        "An entry with an in-process session lease held by this process is retained -- "
        "self-held-lease evidence overrides the age threshold, the domain equivalent of a "
        "live owner reference.",
    ),
    f"{_CS}::L516": RetentionDecision(
        Revocability.REVOCABLE,
        "Failure to acquire the non-blocking lease means another process currently holds "
        "a live lock on this entry, a directly observed live-owner reference.",
    ),
    f"{_CS}::L535": RetentionDecision(
        Revocability.REVOCABLE,
        "Removal did not occur because the re-checked mtime under lease is fresh again or "
        "the entry already vanished -- the mtime re-check under lease is the reclamation-"
        "defining age/liveness re-verification for this candidate.",
    ),
    # -- workspace.worktree::remove_git_worktree --
    f"{_WGW}::L73": SafetyDecision(
        "The worktree path does not exist on disk at all; nothing here to reclaim."
    ),
    f"{_WGW}::L82": SafetyDecision(
        "The git worktree remove call already succeeded; this reports a completed removal, "
        "not a retention skip."
    ),
    # -- workspace.worktree::remove_worktree_sidecar --
    f"{_WWS}::L114": SafetyDecision(
        "The sidecar directory does not exist on disk at all; nothing here to reclaim or retain."
    ),
    # -- execution._session_retention::apply_session_retention --
    f"{_SL}::L51": SafetyDecision(
        "The just-recommitted crash-recovery directory for this same dir_name is protected "
        "from being counted as expired in the same flush that created it, the session-log "
        "equivalent of a reaper excluding the generation it is currently claiming."
    ),
    f"{_SL}::L67": RetentionDecision(
        Revocability.REVOCABLE,
        "A caller-declared protected campaign id is honoured unconditionally, retaining "
        "the session directory regardless of its age, the same self-exclusion family as "
        "the dispatch reaper's protected-id set.",
    ),
    # -- hooks._capture._sweep::sweep_one --
    f"{_SW}::L616": RetentionDecision(
        Revocability.REVOCABLE,
        "The record is absent, already deleted, or its next_attempt_at is still in the "
        "future -- the schedule/age gate retains anything not yet eligible for its next "
        "sweep attempt.",
    ),
    f"{_SW}::L627": RetentionDecision(
        Revocability.REVOCABLE,
        "An issued or published capture reference has not yet reached its recorded expiry; "
        "retained until the reference-expiry deadline passes.",
    ),
    f"{_SW}::L652": RetentionDecision(
        Revocability.REVOCABLE,
        "Re-verified under the second lock: the record vanished, changed identity since "
        "the first check, or is still not due -- the same due-date gate re-applied after "
        "the lease acquisition race window.",
    ),
    f"{_SW}::L675": SafetyDecision(
        "Abandoned-record normalization determined the record is already DELETED; this "
        "reports that terminal outcome, not a retention gate."
    ),
    f"{_SW}::L715": SafetyDecision(
        "The successful-deletion completion path; not a retention skip, this line reports "
        "that reclamation succeeded."
    ),
    f"{_SW}::L717": RetentionDecision(
        Revocability.REVOCABLE,
        "A CarrierLeaseLive exception means an active lease currently holds this capture; "
        "retained until the lease is released, a directly observed live reference.",
    ),
    f"{_SW}::L739": RetentionDecision(
        Revocability.REVOCABLE,
        "A tampered record is retained for a fixed forensic hold window recorded via "
        "next_attempt_at, evidence preserved for investigation before re-eligibility.",
    ),
    f"{_SW}::L753": SafetyDecision(
        "A lifecycle or OSError during the delete attempt is an execution failure, not "
        "evidence about the candidate's liveness; retried up to max_retry_seconds."
    ),
    # -- workspace._projection_cache::prune_stale_projections --
    f"{_PP}::L663": SafetyDecision(
        "The projections root does not exist; there is nothing here to prune."
    ),
    # -- workspace._projection_cache::_reconcile_projection_entry --
    f"{_PRE}::L587": SafetyDecision(
        "A foreign user-writable cache entry is classified as deferred rather than "
        "aborting launch."
    ),
    f"{_PRE}::L590": SafetyDecision(
        "The caller-selected active projection is intentionally excluded from stale "
        "reconciliation."
    ),
    f"{_PRE}::L592": SafetyDecision(
        "A recognized non-projection namespace belongs to another lifecycle owner and "
        "remains untouched."
    ),
    f"{_PRE}::L594": SafetyDecision(
        "A projection outside the exact scanned root fails the direct-child ownership guard."
    ),
    f"{_PRE}::L602": RetentionDecision(
        Revocability.REVOCABLE,
        "Lease contention means another process currently holds an exclusive lock on this "
        "candidate, a directly observed live reference.",
    ),
    f"{_PRE}::L604": SafetyDecision(
        "Lease acquisition failed operationally, so reconciliation defers without "
        "claiming deletion authority."
    ),
    f"{_PRE}::L610": SafetyDecision(
        "Manifest validation failed while resolving the candidate's identity; an inspection "
        "failure, not evidence the candidate is still live."
    ),
    f"{_PRE}::L612": SafetyDecision(
        "Identity resolution was unavailable for this candidate; an inspection failure, "
        "not evidence of liveness."
    ),
    f"{_PRE}::L615": SafetyDecision(
        "The retirement queue could not be read to record this candidate; an infrastructure "
        "failure, not liveness evidence."
    ),
    f"{_PRE}::L617": SafetyDecision(
        "A new exact retirement record was durably created; this reports successful disposition."
    ),
    f"{_PRE}::L618": SafetyDecision(
        "The exact retirement record already exists, so no duplicate durable mutation is needed."
    ),
    f"{_PRE}::L620": SafetyDecision(
        "Install-lock or reconciliation I/O failed operationally and leaves the candidate "
        "retryable."
    ),
    # -- core._plugin_artifact_retirement::try_reclaim --
    f"{_PC}::L180": SafetyDecision(
        "The record's artifact_kind does not match this coordinator's own kind; a type/"
        "ownership guard, not a liveness decision."
    ),
    f"{_PC}::L182": RetentionDecision(
        Revocability.REVOCABLE,
        "The record's scheduled not_before time has not yet passed; retained until the "
        "grace/backoff window elapses.",
    ),
    f"{_PC}::L184": SafetyDecision(
        "This coordinator no longer claims ownership of the managed path; an ownership "
        "guard, not liveness evidence."
    ),
    f"{_PC}::L191": RetentionDecision(
        Revocability.REVOCABLE,
        "Lease contention means another process currently holds an exclusive lock on this "
        "artifact, a directly observed live reference.",
    ),
    f"{_PC}::L197": SafetyDecision(
        "Lease acquisition failed with an OSError or RuntimeError; an infrastructure "
        "failure, not evidence about the record's liveness."
    ),
    f"{_PC}::L206": SafetyDecision(
        "The retiring cache record is already absent, removed by a concurrent sweep; "
        "reports an already-completed outcome, not a retention gate."
    ),
    f"{_PC}::L208": SafetyDecision(
        "The retiring cache is not in the expected exact-v2 state; an infrastructure/"
        "consistency guard, not liveness evidence."
    ),
    f"{_PC}::L222": SafetyDecision(
        "The record is no longer present in the retiring queue, removed concurrently; "
        "reports an already-completed outcome, not a retention gate."
    ),
    f"{_PC}::L224": SafetyDecision(
        "The freshly re-read queued record no longer matches the caller's exact identity; "
        "a consistency guard against acting on stale data."
    ),
    f"{_PC}::L229": RetentionDecision(
        Revocability.REVOCABLE,
        "Re-verified under lock: the record's not_before time has not yet passed; retained "
        "until due.",
    ),
    f"{_PC}::L231": RetentionDecision(
        Revocability.REVOCABLE,
        "The managed path is the actively selected generation right now; retained because "
        "it is currently live and in use, an observed liveness reference.",
    ),
    f"{_PC}::L244": SafetyDecision(
        "Updating the retiring-cache record failed due to an unsafe cache state; an "
        "infrastructure failure, not liveness evidence."
    ),
    f"{_PC}::L249": SafetyDecision(
        "None of the managed, manifest, or staging paths exist on disk; the artifact is "
        "already gone, reporting completion rather than a retention gate."
    ),
    f"{_PC}::L252": SafetyDecision(
        "The staging path is in an ambiguous or unsafe state relative to the managed path; "
        "a consistency guard, not liveness evidence."
    ),
    f"{_PC}::L261": SafetyDecision(
        "Resolving the current on-disk identity failed as unavailable; an inspection "
        "failure, not evidence of liveness."
    ),
    f"{_PC}::L268": SafetyDecision(
        "Updating the retiring-cache record failed while rejecting an invalid identity; an "
        "infrastructure failure, not liveness evidence."
    ),
    f"{_PC}::L273": SafetyDecision(
        "On-disk identity validation failed for the current generation; a validation guard, "
        "not a liveness or age decision."
    ),
    f"{_PC}::L280": SafetyDecision(
        "Updating the retiring-cache record failed while rejecting a mismatched identity; "
        "an infrastructure failure, not liveness evidence."
    ),
    f"{_PC}::L285": SafetyDecision(
        "The current on-disk identity no longer matches the record's recorded identity; a "
        "consistency guard against reclaiming the wrong artifact."
    ),
    f"{_PC}::L292": SafetyDecision(
        "Renaming the managed path into staging failed with an OSError; an execution "
        "failure, not liveness evidence."
    ),
    f"{_PC}::L306": SafetyDecision(
        "The artifact was already removed from disk; updating the retiring-cache record "
        "afterward failed due to an unsafe cache state, an infrastructure failure."
    ),
    f"{_PC}::L312": SafetyDecision(
        "Removing the manifest or staging directory failed with an OSError; an execution "
        "failure during the delete attempt, not liveness evidence."
    ),
    f"{_PC}::L317": SafetyDecision(
        "The successful-reclaim completion path; not a retention skip, this line reports "
        "that reclamation succeeded."
    ),
    # -- cli.install._plugin_artifact::sweep_due --
    f"{_CT}::L528": SafetyDecision(
        "The retiring cache is not in a safe exact-v2 state, corrupt or future-versioned; "
        "an infrastructure guard, not a liveness decision."
    ),
    f"{_CT}::L536": SafetyDecision(
        "No registered owner claims this legacy evidence's artifact kind; a routing guard, "
        "not liveness evidence."
    ),
    f"{_CT}::L541": SafetyDecision(
        "Reading due retiring records failed under an unsafe cache state; an infrastructure "
        "failure, not evidence about any record's liveness."
    ),
    f"{_CT}::L546": SafetyDecision(
        "No registered owner claims this record's artifact kind; a routing guard, not "
        "liveness evidence."
    ),
}
