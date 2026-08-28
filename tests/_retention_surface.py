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

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from functools import partial
from pathlib import Path
from typing import TypeAlias

from structlog.testing import capture_logs

from autoskillit.core.runtime import Revocability

REPO_ROOT = Path(__file__).resolve().parent.parent

# ``Recurrence`` is test-audit vocabulary; no widely recognized external analogue was found,
# and the adjacent ``RetentionDecision``/``Revocability`` enforcement is its closest structural
# precedent.


class Recurrence(StrEnum):
    """How a safety-only skip can stop recurring for one candidate."""

    SELF_LIMITING = "self_limiting"
    RESOLVES_WITH_CONTENTION = "resolves_with_contention"
    RECURS_UNTIL_INPUT_CHANGES = "recurs_until_input_changes"


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
    recurrence: Recurrence
    converges_by: str | None = None

    def __post_init__(self) -> None:
        if len(self.justification.split()) < 6:
            raise ValueError(f"justification too short: {self.justification!r}")
        if (
            self.recurrence is Recurrence.RECURS_UNTIL_INPUT_CHANGES
            and len("".join((self.converges_by or "").split())) < 40
        ):
            raise ValueError(
                "a RECURS_UNTIL_INPUT_CHANGES safety entry needs a substantive convergence reason"
            )


def _validate_safety_decisions(
    registry: dict[str, RetentionDecision | SafetyDecision],
) -> list[str]:
    """Return precise recurrence-axis omissions for an arbitrary decision registry."""
    errors: list[str] = []
    for key, decision in registry.items():
        if not isinstance(decision, SafetyDecision):
            continue
        if decision.recurrence is None:
            errors.append(f"{key}: SafetyDecision is missing recurrence")
        elif (
            decision.recurrence is Recurrence.RECURS_UNTIL_INPUT_CHANGES
            and len("".join((decision.converges_by or "").split())) < 40
        ):
            errors.append(f"{key}: recurring SafetyDecision needs a substantive converges_by")
    return errors


def _self_limiting(justification: str) -> SafetyDecision:
    """Register a branch whose completed/excluded candidate cannot recur."""
    return SafetyDecision(justification, Recurrence.SELF_LIMITING)


def _retries_after_input_changes(justification: str) -> SafetyDecision:
    """Register a deferred branch whose external input must change before it can proceed."""
    return SafetyDecision(
        justification,
        Recurrence.RECURS_UNTIL_INPUT_CHANGES,
        converges_by=(
            "A later pass becomes eligible only after its external filesystem, ownership, or "
            "dependency input changes; this reclaimer cannot safely force that transition."
        ),
    )


def _resolves_with_contention(justification: str) -> SafetyDecision:
    """Register a branch deferred solely by a presently held lease or process reference."""
    return SafetyDecision(justification, Recurrence.RESOLVES_WITH_CONTENTION)


ReclaimerTarget: TypeAlias = tuple[str, str]
ConvergenceOperation: TypeAlias = Callable[[], object]
ConvergenceAdapter: TypeAlias = Callable[[ConvergenceOperation], object]


#: Target reclaimer functions the scanner walks: ``(repo-relative path, qualified name)``.
RECLAIMER_TARGETS: frozenset[ReclaimerTarget] = frozenset(
    {
        ("scripts/pytest_tmp_lifecycle.py", "_reap"),
        ("scripts/pytest_tmp_lifecycle.py", "_safe_candidates"),
        ("src/autoskillit/fleet/_dispatch_reaper.py", "reap_stale_dispatches"),
        (
            "src/autoskillit/workspace/session_skill_manager.py",
            "DefaultSessionSkillManager.cleanup_stale",
        ),
        ("src/autoskillit/workspace/clone_registry.py", "cleanup_candidates"),
        ("src/autoskillit/workspace/worktree.py", "remove_git_worktree"),
        ("src/autoskillit/workspace/worktree.py", "remove_worktree_sidecar"),
        ("src/autoskillit/execution/_session_retention.py", "apply_session_retention"),
        ("src/autoskillit/hooks/_capture/_sweep.py", "sweep_one"),
        ("src/autoskillit/workspace/_projection_cache.py", "prune_stale_projections"),
        ("src/autoskillit/workspace/_projection_cache.py", "_reconcile_projection_entry"),
        (
            "src/autoskillit/core/_plugin_artifact_retirement.py",
            "PluginArtifactRetirementEngine.try_reclaim",
        ),
        (
            "src/autoskillit/cli/install/_plugin_artifact.py",
            "InstalledPluginArtifactRetirementOwner.try_reclaim",
        ),
        (
            "src/autoskillit/cli/install/_plugin_artifact.py",
            "DefaultPluginRetirementCoordinator.sweep_due",
        ),
        (
            "src/autoskillit/workspace/_projected_artifact/_generation_publication.py",
            "prune_stale_generations",
        ),
        (
            "src/autoskillit/workspace/_install_state.py",
            "_enqueue_legacy_installed_plugin_versions",
        ),
        (
            "src/autoskillit/workspace/_projected_artifact/_hook_repair.py",
            "repair_broken_plugin_cache_hooks",
        ),
        (
            "src/autoskillit/workspace/_projected_artifact/_hook_repair.py",
            "repair_broken_projection_hooks",
        ),
        ("src/autoskillit/execution/_session_log_recovery.py", "recover_crashed_sessions"),
    }
)


def _invoke_convergence_operation(
    target: ReclaimerTarget,
    operation: ConvergenceOperation,
) -> object:
    """Invoke the fixture-local run closure for one exact registered target."""
    if target not in RECLAIMER_TARGETS:
        raise AssertionError(f"unregistered convergence target: {target}")
    return operation()


def _observe_convergence_operation(
    target: ReclaimerTarget,
    operation: ConvergenceOperation,
) -> object:
    """Invoke the fixture-local state observer for one exact registered target."""
    if target not in RECLAIMER_TARGETS:
        raise AssertionError(f"unregistered convergence target: {target}")
    return operation()


def _convergence_adapters(
    target: ReclaimerTarget,
) -> tuple[ConvergenceAdapter, ConvergenceAdapter]:
    """Bind xdist-safe fixture closures to one qualified reclaimer identity."""
    return (
        partial(_invoke_convergence_operation, target),
        partial(_observe_convergence_operation, target),
    )


# Explicit rather than derived from RECLAIMER_TARGETS: adding or removing a target must update
# both registries, so the equality guard below has teeth. The adapters accept fixture-local
# no-argument closures, keeping temporary files and monkeypatch state out of module globals.
RECLAIMER_CONVERGENCE_CASES: Mapping[
    ReclaimerTarget,
    tuple[ConvergenceAdapter, ConvergenceAdapter],
] = {
    ("scripts/pytest_tmp_lifecycle.py", "_reap"): _convergence_adapters(
        ("scripts/pytest_tmp_lifecycle.py", "_reap")
    ),
    ("scripts/pytest_tmp_lifecycle.py", "_safe_candidates"): _convergence_adapters(
        ("scripts/pytest_tmp_lifecycle.py", "_safe_candidates")
    ),
    (
        "src/autoskillit/fleet/_dispatch_reaper.py",
        "reap_stale_dispatches",
    ): _convergence_adapters(
        ("src/autoskillit/fleet/_dispatch_reaper.py", "reap_stale_dispatches")
    ),
    (
        "src/autoskillit/workspace/session_skill_lifecycle.py",
        "DefaultSessionSkillManager.cleanup_stale",
    ): _convergence_adapters(
        (
            "src/autoskillit/workspace/session_skill_manager.py",
            "DefaultSessionSkillManager.cleanup_stale",
        )
    ),
    ("src/autoskillit/workspace/clone_registry.py", "cleanup_candidates"): _convergence_adapters(
        ("src/autoskillit/workspace/clone_registry.py", "cleanup_candidates")
    ),
    ("src/autoskillit/workspace/worktree.py", "remove_git_worktree"): _convergence_adapters(
        ("src/autoskillit/workspace/worktree.py", "remove_git_worktree")
    ),
    ("src/autoskillit/workspace/worktree.py", "remove_worktree_sidecar"): _convergence_adapters(
        ("src/autoskillit/workspace/worktree.py", "remove_worktree_sidecar")
    ),
    (
        "src/autoskillit/execution/_session_retention.py",
        "apply_session_retention",
    ): _convergence_adapters(
        ("src/autoskillit/execution/_session_retention.py", "apply_session_retention")
    ),
    ("src/autoskillit/hooks/_capture/_sweep.py", "sweep_one"): _convergence_adapters(
        ("src/autoskillit/hooks/_capture/_sweep.py", "sweep_one")
    ),
    (
        "src/autoskillit/workspace/_projection_cache.py",
        "prune_stale_projections",
    ): _convergence_adapters(
        ("src/autoskillit/workspace/_projection_cache.py", "prune_stale_projections")
    ),
    (
        "src/autoskillit/workspace/_projection_cache.py",
        "_reconcile_projection_entry",
    ): _convergence_adapters(
        ("src/autoskillit/workspace/_projection_cache.py", "_reconcile_projection_entry")
    ),
    (
        "src/autoskillit/core/_plugin_artifact_retirement.py",
        "PluginArtifactRetirementEngine.try_reclaim",
    ): _convergence_adapters(
        (
            "src/autoskillit/core/_plugin_artifact_retirement.py",
            "PluginArtifactRetirementEngine.try_reclaim",
        )
    ),
    (
        "src/autoskillit/cli/install/_plugin_artifact.py",
        "InstalledPluginArtifactRetirementOwner.try_reclaim",
    ): _convergence_adapters(
        (
            "src/autoskillit/cli/install/_plugin_artifact.py",
            "InstalledPluginArtifactRetirementOwner.try_reclaim",
        )
    ),
    (
        "src/autoskillit/cli/install/_plugin_artifact.py",
        "DefaultPluginRetirementCoordinator.sweep_due",
    ): _convergence_adapters(
        (
            "src/autoskillit/cli/install/_plugin_artifact.py",
            "DefaultPluginRetirementCoordinator.sweep_due",
        )
    ),
    (
        "src/autoskillit/workspace/_projected_artifact/_generation_publication.py",
        "prune_stale_generations",
    ): _convergence_adapters(
        (
            "src/autoskillit/workspace/_projected_artifact/_generation_publication.py",
            "prune_stale_generations",
        )
    ),
    (
        "src/autoskillit/workspace/_install_state.py",
        "_enqueue_legacy_installed_plugin_versions",
    ): _convergence_adapters(
        (
            "src/autoskillit/workspace/_install_state.py",
            "_enqueue_legacy_installed_plugin_versions",
        )
    ),
    (
        "src/autoskillit/workspace/_projected_artifact/_hook_repair.py",
        "repair_broken_plugin_cache_hooks",
    ): _convergence_adapters(
        (
            "src/autoskillit/workspace/_projected_artifact/_hook_repair.py",
            "repair_broken_plugin_cache_hooks",
        )
    ),
    (
        "src/autoskillit/workspace/_projected_artifact/_hook_repair.py",
        "repair_broken_projection_hooks",
    ): _convergence_adapters(
        (
            "src/autoskillit/workspace/_projected_artifact/_hook_repair.py",
            "repair_broken_projection_hooks",
        )
    ),
    (
        "src/autoskillit/execution/_session_log_recovery.py",
        "recover_crashed_sessions",
    ): _convergence_adapters(
        (
            "src/autoskillit/execution/_session_log_recovery.py",
            "recover_crashed_sessions",
        )
    ),
}


def assert_second_pass_is_quiet(
    run: ConvergenceOperation,
    *,
    observe: ConvergenceOperation,
) -> tuple[object, object, list[dict[str, object]], list[dict[str, object]]]:
    """Run one reclaimer twice and prove pass two emits no work or warning/error."""
    with capture_logs() as first_logs:
        first_result = run()
    after_first = observe()
    with capture_logs() as second_logs:
        second_result = run()
    after_second = observe()

    noisy_second_pass = [
        entry for entry in second_logs if entry.get("log_level") in {"warning", "error"}
    ]
    assert noisy_second_pass == []
    assert after_second == after_first
    return first_result, second_result, first_logs, second_logs


#: Discovery may identify a lifecycle-shaped function that intentionally is not a retention
#: reclaimer. Every such exclusion needs a durable written reason in this audit surface.
_DELEGATED_MUTATION_REASON = (
    "This lower-level mutation helper or retirement-owner adapter does not choose which "
    "candidates to retain; its caller owns the audited eligibility and convergence policy."
)
_COMMAND_BOUNDARY_REASON = (
    "This command or composition boundary invokes lifecycle work but does not own a repeated "
    "candidate-retention decision; the called domain reclaimer owns that policy."
)
_SEPARATE_LIFECYCLE_REASON = (
    "This function manages a separate one-shot or independently bounded lifecycle whose "
    "retention contract is enforced by its domain tests rather than this reclaimer registry."
)

ACKNOWLEDGED_NON_RECLAIMERS: dict[ReclaimerTarget, str] = {
    # Mutation helpers and retirement-owner adapters.
    ("scripts/pytest_tmp_lifecycle.py", "_remove_candidate"): _DELEGATED_MUTATION_REASON,
    (
        "src/autoskillit/cli/_install_snapshot/_snapshot.py",
        "_InstallSnapshot._remove",
    ): _DELEGATED_MUTATION_REASON,
    (
        "src/autoskillit/cli/fleet/__init__.py",
        "_remove_clone_fn",
    ): _DELEGATED_MUTATION_REASON,
    (
        "src/autoskillit/cli/install/_plugin_artifact.py",
        "InstalledPluginArtifactRetirementOwner.enqueue_retirement",
    ): _DELEGATED_MUTATION_REASON,
    (
        "src/autoskillit/execution/evidence_reader.py",
        "_remove_directory",
    ): _DELEGATED_MUTATION_REASON,
    (
        "src/autoskillit/execution/process/_process_tether.py",
        "remove_tether",
    ): _DELEGATED_MUTATION_REASON,
    (
        "src/autoskillit/execution/session/_managed_headless_session_lineage_indexes.py",
        "_remove_index",
    ): _DELEGATED_MUTATION_REASON,
    (
        "src/autoskillit/execution/session_log.py",
        "flush_session_log",
    ): _DELEGATED_MUTATION_REASON,
    (
        "src/autoskillit/workspace/_projected_artifact/_generation_publication.py",
        "GenerationArtifactRetirementOwner.enqueue_retirement",
    ): _DELEGATED_MUTATION_REASON,
    (
        "src/autoskillit/workspace/_projected_artifact/_generation_publication.py",
        "GenerationArtifactRetirementOwner.try_reclaim",
    ): _DELEGATED_MUTATION_REASON,
    (
        "src/autoskillit/workspace/_projected_artifact/_generation_publication.py",
        "_reconcile_generation_candidate",
    ): _DELEGATED_MUTATION_REASON,
    (
        "src/autoskillit/workspace/_projected_artifact/_generation_publication.py",
        "_sweep_orphaned_staging",
    ): _DELEGATED_MUTATION_REASON,
    (
        "src/autoskillit/workspace/_projected_artifact/_hook_repair.py",
        "_rollback_repair",
    ): _DELEGATED_MUTATION_REASON,
    (
        "src/autoskillit/workspace/_projection_cache.py",
        "ProjectedPluginRetirementOwner.enqueue_retirement",
    ): _DELEGATED_MUTATION_REASON,
    (
        "src/autoskillit/workspace/_projection_cache.py",
        "ProjectedPluginRetirementOwner.try_reclaim",
    ): _DELEGATED_MUTATION_REASON,
    ("src/autoskillit/workspace/clone.py", "remove_clone"): _DELEGATED_MUTATION_REASON,
    (
        "src/autoskillit/workspace/session_skill_lifecycle.py",
        "_remove_and_verify",
    ): _DELEGATED_MUTATION_REASON,
    (
        "src/autoskillit/workspace/session_skill_materialization.py",
        "_remove_generated_home_skill_entry",
    ): _DELEGATED_MUTATION_REASON,
    # Commands and composition boundaries.
    ("src/autoskillit/cli/_workspace.py", "run_workspace_clean"): _COMMAND_BOUNDARY_REASON,
    ("src/autoskillit/cli/fleet/__init__.py", "fleet_status"): _COMMAND_BOUNDARY_REASON,
    ("src/autoskillit/cli/install/_marketplace.py", "upgrade"): _COMMAND_BOUNDARY_REASON,
    (
        "src/autoskillit/cli/update/_obligation_repair.py",
        "attempt_obligation_repair",
    ): _COMMAND_BOUNDARY_REASON,
    (
        "src/autoskillit/hooks/session_start_hook.py",
        "main",
    ): _COMMAND_BOUNDARY_REASON,
    (
        "src/autoskillit/workspace/_install_state.py",
        "reconcile_install_artifacts",
    ): _COMMAND_BOUNDARY_REASON,
    # Separately bounded lifecycle operations.
    (
        "src/autoskillit/cli/session/_session_reload.py",
        "consume_reload_sentinel",
    ): _SEPARATE_LIFECYCLE_REASON,
    (
        "src/autoskillit/core/runtime/kitchen_state.py",
        "sweep_stale_markers",
    ): _SEPARATE_LIFECYCLE_REASON,
    (
        "src/autoskillit/core/runtime/private_file.py",
        "reconcile_initialization_links",
    ): _SEPARATE_LIFECYCLE_REASON,
    (
        "src/autoskillit/core/runtime/readiness.py",
        "cleanup_readiness_sentinel",
    ): _SEPARATE_LIFECYCLE_REASON,
    (
        "src/autoskillit/execution/_recording_skills.py",
        "_assert_agent_safe_skill_tree",
    ): _SEPARATE_LIFECYCLE_REASON,
    (
        "src/autoskillit/execution/_recording_skills.py",
        "build_skills_manifest",
    ): _SEPARATE_LIFECYCLE_REASON,
    (
        "src/autoskillit/execution/_recording_skills.py",
        "snapshot_skill_dir",
    ): _SEPARATE_LIFECYCLE_REASON,
    (
        "src/autoskillit/execution/backends/_codex_session_storage.py",
        "CodexSessionStore.recover",
    ): _SEPARATE_LIFECYCLE_REASON,
    (
        "src/autoskillit/fleet/state.py",
        "build_protected_campaign_ids",
    ): _SEPARATE_LIFECYCLE_REASON,
    (
        "src/autoskillit/hook_registry/_quarantine.py",
        "validate_plugin_cache_hooks",
    ): _SEPARATE_LIFECYCLE_REASON,
    (
        "src/autoskillit/hooks/_capture/_migration.py",
        "remove_transaction",
    ): _SEPARATE_LIFECYCLE_REASON,
    (
        "src/autoskillit/hooks/_exploration_request_record.py",
        "_cleanup_expired",
    ): _SEPARATE_LIFECYCLE_REASON,
    (
        "src/autoskillit/planner/manifests.py",
        "reconcile_wp_files",
    ): _SEPARATE_LIFECYCLE_REASON,
    (
        "src/autoskillit/server/tools/tools_fleet_reset.py",
        "_cleanup_resume_gate_state",
    ): _SEPARATE_LIFECYCLE_REASON,
    (
        "src/autoskillit/smoke_utils/_review_design.py",
        "pre_iteration_cleanup",
    ): _SEPARATE_LIFECYCLE_REASON,
}


_R = "scripts/pytest_tmp_lifecycle.py::_reap"
_S = "scripts/pytest_tmp_lifecycle.py::_safe_candidates"
_D = "src/autoskillit/fleet/_dispatch_reaper.py::reap_stale_dispatches"
_CS = (
    "src/autoskillit/workspace/session_skill_manager.py::DefaultSessionSkillManager.cleanup_stale"
)
_WGW = "src/autoskillit/workspace/worktree.py::remove_git_worktree"
_WWS = "src/autoskillit/workspace/worktree.py::remove_worktree_sidecar"
_SL = "src/autoskillit/execution/_session_retention.py::apply_session_retention"
_SW = "src/autoskillit/hooks/_capture/_sweep.py::sweep_one"
_PP = "src/autoskillit/workspace/_projection_cache.py::prune_stale_projections"
_PRE = "src/autoskillit/workspace/_projection_cache.py::_reconcile_projection_entry"
_PC = (
    "src/autoskillit/core/_plugin_artifact_retirement.py::"
    "PluginArtifactRetirementEngine.try_reclaim"
)
_CT = (
    "src/autoskillit/cli/install/_plugin_artifact.py::DefaultPluginRetirementCoordinator.sweep_due"
)
_GP = (
    "src/autoskillit/workspace/_projected_artifact/"
    "_generation_publication.py::prune_stale_generations"
)
_IL = "src/autoskillit/workspace/_install_state.py::_enqueue_legacy_installed_plugin_versions"
_HC = (
    "src/autoskillit/workspace/_projected_artifact/"
    "_hook_repair.py::repair_broken_plugin_cache_hooks"
)
_HP = (
    "src/autoskillit/workspace/_projected_artifact/_hook_repair.py::repair_broken_projection_hooks"
)
_SR = "src/autoskillit/execution/_session_log_recovery.py::recover_crashed_sessions"

AUDITED_RETENTION_DECISIONS: dict[str, RetentionDecision | SafetyDecision] = {
    # -- scripts.pytest_tmp_lifecycle::_reap --
    f"{_R}::L469": _retries_after_input_changes(
        "Scan-level failure retains every candidate rather than treating an empty result "
        "as absence of evidence; the fail-closed contract tests/AGENTS.md documents."
    ),
    f"{_R}::L476": _self_limiting(
        "The generation _setup is currently claiming is excluded from its own reap pass."
    ),
    f"{_R}::L480": _self_limiting(
        "FileNotFoundError on lstat means the candidate is already gone; nothing to reclaim."
    ),
    f"{_R}::L483": _retries_after_input_changes(
        "An OSError inspecting the candidate is an inspection failure, not eligibility evidence."
    ),
    f"{_R}::L486": _retries_after_input_changes(
        "A symlink or non-directory entry under the platform root is a safety exclusion, "
        "never a reclamation candidate regardless of any evidence."
    ),
    f"{_R}::L489": _retries_after_input_changes(
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
    f"{_R}::L528": _self_limiting(
        "A markerless candidate younger than legacy_age_minutes might be another "
        "concurrent _setup mid-creation; never touched by the bound, only by this age gate."
    ),
    # -- scripts.pytest_tmp_lifecycle::_safe_candidates --
    f"{_S}::L397": _retries_after_input_changes(
        "Cannot normalize private-root permissions; the whole private-root scan is skipped "
        "rather than risk enumerating an untrusted-mode directory."
    ),
    # -- fleet._dispatch_reaper::reap_stale_dispatches --
    f"{_D}::L141": _self_limiting(
        "No campaign state file at all; nothing to reap for this campaign."
    ),
    f"{_D}::L146": _retries_after_input_changes(
        "The state file could not be parsed; an unreadable state must not be interpreted "
        "as zero running dispatches."
    ),
    f"{_D}::L151": _self_limiting(
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
    f"{_D}::L192": _self_limiting(
        "pid == 0 is a reap outcome (marks the dispatch dead), not an eligibility skip -- "
        "the continue here follows the reclaim action, it does not precede it."
    ),
    f"{_D}::L210": _self_limiting(
        "A boot-id mismatch is a reap outcome (marks the dispatch pid-recycled), not an "
        "eligibility skip -- the continue follows the reclaim action."
    ),
    f"{_D}::L214": _self_limiting(
        "psutil.pid_exists() false is a reap outcome (marks the dispatch dead), not an "
        "eligibility skip -- the continue follows the reclaim action."
    ),
    f"{_D}::L237": _self_limiting(
        "psutil.NoSuchProcess during create_time comparison is a reap outcome (marks the "
        "dispatch dead), not an eligibility skip."
    ),
    f"{_D}::L252": RetentionDecision(
        Revocability.REVOCABLE,
        "An active dispatch heartbeat (a live kernel-observable mtime freshness check) "
        "retains the dispatch -- the domain equivalent of a revocable kernel reference.",
    ),
    f"{_D}::L275": _retries_after_input_changes(
        "kill_process_tree raised; execution failure, not an eligibility gate on the "
        "candidate itself."
    ),
    f"{_D}::L287": RetentionDecision(
        Revocability.REVOCABLE,
        "Survivors reported by kill_process_tree's cleanup_result mean the process may "
        "still be alive -- the dispatch record is deliberately left RUNNING for a retry, "
        "an observed-liveness result standing in for a direct /proc reference check.",
    ),
    # -- workspace.session_skills::cleanup_stale --
    f"{_CS}::L500": _self_limiting(
        "The candidate root directory does not exist; nothing here to scan or reclaim."
    ),
    f"{_CS}::L504": _self_limiting(
        "The session-leases bookkeeping subdirectory itself is not a session; a structural "
        "exclusion, not an eligibility decision."
    ),
    f"{_CS}::L506": _self_limiting(
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
    f"{_WGW}::L73": _self_limiting(
        "The worktree path does not exist on disk at all; nothing here to reclaim."
    ),
    f"{_WGW}::L82": _self_limiting(
        "The git worktree remove call already succeeded; this reports a completed removal, "
        "not a retention skip."
    ),
    # -- workspace.worktree::remove_worktree_sidecar --
    f"{_WWS}::L114": _self_limiting(
        "The sidecar directory does not exist on disk at all; nothing here to reclaim or retain."
    ),
    # -- execution._session_retention::apply_session_retention --
    f"{_SL}::L51": _self_limiting(
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
    f"{_SW}::L675": _self_limiting(
        "Abandoned-record normalization determined the record is already DELETED; this "
        "reports that terminal outcome, not a retention gate."
    ),
    f"{_SW}::L715": _self_limiting(
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
    f"{_SW}::L753": _self_limiting(
        "A lifecycle or OSError during the delete attempt is an execution failure, not "
        "evidence about the candidate's liveness; retried up to max_retry_seconds."
    ),
    # -- workspace._projection_cache::prune_stale_projections --
    f"{_PP}::L801": _retries_after_input_changes(
        "The managed-home boundary does not contain the projection owner root, so mutation "
        "is refused before enumeration."
    ),
    f"{_PP}::L804": _self_limiting(
        "The projections root does not exist; there is nothing here to prune."
    ),
    f"{_PP}::L813": _retries_after_input_changes(
        "An operational failure inspecting the projection root defers reconciliation "
        "without risking launch availability."
    ),
    # -- workspace._projection_cache::_reconcile_projection_entry --
    f"{_PRE}::L592": _retries_after_input_changes(
        "A foreign user-writable cache entry is classified as deferred rather than "
        "aborting launch."
    ),
    f"{_PRE}::L595": _retries_after_input_changes(
        "The caller-selected active projection is intentionally excluded from stale "
        "reconciliation."
    ),
    f"{_PRE}::L597": _self_limiting(
        "A deterministic residue staging entry delegates to its original-key locked "
        "resume transition."
    ),
    f"{_PRE}::L605": _retries_after_input_changes(
        "A recognized non-projection namespace belongs to another lifecycle owner and "
        "remains untouched."
    ),
    f"{_PRE}::L607": _retries_after_input_changes(
        "A projection outside the exact scanned root fails the direct-child ownership guard."
    ),
    f"{_PRE}::L615": RetentionDecision(
        Revocability.REVOCABLE,
        "Lease contention means another process currently holds an exclusive lock on this "
        "candidate, a directly observed live reference.",
    ),
    f"{_PRE}::L617": _retries_after_input_changes(
        "Lease acquisition failed operationally, so reconciliation defers without "
        "claiming deletion authority."
    ),
    f"{_PRE}::L623": _self_limiting(
        "A permanently invalid projection delegates to the terminal quarantine transition "
        "under the held lease and lock."
    ),
    f"{_PRE}::L630": _retries_after_input_changes(
        "Identity resolution was unavailable for this candidate; an inspection failure, "
        "not evidence of liveness."
    ),
    f"{_PRE}::L633": _retries_after_input_changes(
        "The retirement queue could not be read to record this candidate; an infrastructure "
        "failure, not liveness evidence."
    ),
    f"{_PRE}::L635": _self_limiting(
        "A new exact retirement record was durably created; this reports successful disposition."
    ),
    f"{_PRE}::L636": _self_limiting(
        "The exact retirement record already exists, so no duplicate durable mutation is needed."
    ),
    f"{_PRE}::L638": _retries_after_input_changes(
        "Install-lock or reconciliation I/O failed operationally and leaves the candidate "
        "retryable."
    ),
    # -- core._plugin_artifact_retirement::try_reclaim --
    f"{_PC}::L180": _retries_after_input_changes(
        "The record's artifact_kind does not match this coordinator's own kind; a type/"
        "ownership guard, not a liveness decision."
    ),
    f"{_PC}::L182": RetentionDecision(
        Revocability.REVOCABLE,
        "The record's scheduled not_before time has not yet passed; retained until the "
        "grace/backoff window elapses.",
    ),
    f"{_PC}::L184": _retries_after_input_changes(
        "This coordinator no longer claims ownership of the managed path; an ownership "
        "guard, not liveness evidence."
    ),
    f"{_PC}::L191": RetentionDecision(
        Revocability.REVOCABLE,
        "Lease contention means another process currently holds an exclusive lock on this "
        "artifact, a directly observed live reference.",
    ),
    f"{_PC}::L197": _retries_after_input_changes(
        "Lease acquisition failed with an OSError or RuntimeError; an infrastructure "
        "failure, not evidence about the record's liveness."
    ),
    f"{_PC}::L206": _self_limiting(
        "The retiring cache record is already absent, removed by a concurrent sweep; "
        "reports an already-completed outcome, not a retention gate."
    ),
    f"{_PC}::L208": _retries_after_input_changes(
        "The retiring cache is not in the expected exact-v2 state; an infrastructure/"
        "consistency guard, not liveness evidence."
    ),
    f"{_PC}::L222": _self_limiting(
        "The record is no longer present in the retiring queue, removed concurrently; "
        "reports an already-completed outcome, not a retention gate."
    ),
    f"{_PC}::L224": _self_limiting(
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
    f"{_PC}::L244": _retries_after_input_changes(
        "Updating the retiring-cache record failed due to an unsafe cache state; an "
        "infrastructure failure, not liveness evidence."
    ),
    f"{_PC}::L249": _self_limiting(
        "None of the managed, manifest, or staging paths exist on disk; the artifact is "
        "already gone, reporting completion rather than a retention gate."
    ),
    f"{_PC}::L252": _retries_after_input_changes(
        "The staging path is in an ambiguous or unsafe state relative to the managed path; "
        "a consistency guard, not liveness evidence."
    ),
    f"{_PC}::L261": _retries_after_input_changes(
        "Resolving the current on-disk identity failed as unavailable; an inspection "
        "failure, not evidence of liveness."
    ),
    f"{_PC}::L268": _retries_after_input_changes(
        "Updating the retiring-cache record failed while rejecting an invalid identity; an "
        "infrastructure failure, not liveness evidence."
    ),
    f"{_PC}::L273": _retries_after_input_changes(
        "On-disk identity validation failed for the current generation; a validation guard, "
        "not a liveness or age decision."
    ),
    f"{_PC}::L280": _retries_after_input_changes(
        "Updating the retiring-cache record failed while rejecting a mismatched identity; "
        "an infrastructure failure, not liveness evidence."
    ),
    f"{_PC}::L285": _retries_after_input_changes(
        "The current on-disk identity no longer matches the record's recorded identity; a "
        "consistency guard against reclaiming the wrong artifact."
    ),
    f"{_PC}::L292": _retries_after_input_changes(
        "Renaming the managed path into staging failed with an OSError; an execution "
        "failure, not liveness evidence."
    ),
    f"{_PC}::L306": _retries_after_input_changes(
        "The artifact was already removed from disk; updating the retiring-cache record "
        "afterward failed due to an unsafe cache state, an infrastructure failure."
    ),
    f"{_PC}::L312": _retries_after_input_changes(
        "Removing the manifest or staging directory failed with an OSError; an execution "
        "failure during the delete attempt, not liveness evidence."
    ),
    f"{_PC}::L317": _self_limiting(
        "The successful-reclaim completion path; not a retention skip, this line reports "
        "that reclamation succeeded."
    ),
    # -- cli.install._plugin_artifact::sweep_due --
    f"{_CT}::L528": _retries_after_input_changes(
        "The retiring cache is not in a safe exact-v2 state, corrupt or future-versioned; "
        "an infrastructure guard, not a liveness decision."
    ),
    f"{_CT}::L536": _retries_after_input_changes(
        "No registered owner claims this legacy evidence's artifact kind; a routing guard, "
        "not liveness evidence."
    ),
    f"{_CT}::L541": _retries_after_input_changes(
        "Reading due retiring records failed under an unsafe cache state; an infrastructure "
        "failure, not evidence about any record's liveness."
    ),
    f"{_CT}::L546": _retries_after_input_changes(
        "No registered owner claims this record's artifact kind; a routing guard, not "
        "liveness evidence."
    ),
    # -- workspace._projected_artifact._generation_publication::prune_stale_generations --
    f"{_GP}::L805": _self_limiting(
        "The generation store does not exist, so this invocation has no candidates to prune."
    ),
    f"{_GP}::L812": _self_limiting(
        "A hidden version directory belongs to staging or bookkeeping, not generation retirement."
    ),
    f"{_GP}::L814": _self_limiting(
        "A non-directory version entry cannot contain a generation incarnation this "
        "reclaimer owns."
    ),
    f"{_GP}::L819": _self_limiting(
        "An unmanaged hidden entry is outside the deterministic generation-residue "
        "lifecycle namespace."
    ),
    f"{_GP}::L821": _self_limiting(
        "A symlink generation entry is excluded before any retirement mutation for "
        "containment safety."
    ),
    f"{_GP}::L823": _self_limiting(
        "A non-directory incarnation cannot be a managed generation retirement candidate."
    ),
    f"{_GP}::L825": _self_limiting(
        "The selected generation remains active and is not a stale candidate for this pass."
    ),
    # -- workspace._install_state::_enqueue_legacy_installed_plugin_versions --
    f"{_IL}::L413": _self_limiting(
        "The running legacy version without a selected generation remains outside retirement."
    ),
    f"{_IL}::L416": _self_limiting(
        "A durable rejected-legacy marker already records this invalid candidate's terminal "
        "disposition."
    ),
    f"{_IL}::L437": _self_limiting(
        "Another reconciler created the same durable rejection marker, completing this "
        "candidate's disposition."
    ),
    f"{_IL}::L443": _self_limiting(
        "Writing the rejected-legacy marker durably records this invalid candidate for quiet "
        "later passes."
    ),
    f"{_IL}::L445": _resolves_with_contention(
        "A shared lease is currently contended, so the legacy candidate waits for its holder."
    ),
    # -- workspace._projected_artifact._hook_repair::repair_broken_plugin_cache_hooks --
    f"{_HC}::L232": _self_limiting(
        "The plugin cache root is absent, leaving no hook incarnation to repair."
    ),
    f"{_HC}::L247": _self_limiting(
        "An incarnation without hooks.json has no hook payload this repairer can own."
    ),
    f"{_HC}::L244": _self_limiting(
        "The content-fingerprinted quarantine marker already records this hooks payload's "
        "terminal result."
    ),
    f"{_HC}::L257": _self_limiting(
        "A valid unbroken hook payload requires no repair or further lifecycle mutation."
    ),
    f"{_HC}::L262": _self_limiting(
        "The payload changed to a marked incarnation before the lease, so its disposition "
        "is complete."
    ),
    f"{_HC}::L276": _self_limiting(
        "A durable quarantine marker and QUARANTINED outcome complete this invalid payload's "
        "lifecycle."
    ),
    f"{_HC}::L274": _self_limiting(
        "The hook payload became valid under the lease and no repair remains necessary."
    ),
    f"{_HC}::L292": _self_limiting(
        "Identity validation writes a durable quarantine marker before reporting the terminal "
        "outcome."
    ),
    f"{_HC}::L335": _resolves_with_contention(
        "An exclusive hook lease is held by another live repairer and will release."
    ),
    f"{_HC}::L344": _retries_after_input_changes(
        "A transient hook read, write, or rollback failure leaves the candidate retryable."
    ),
    # -- workspace._projected_artifact._hook_repair::repair_broken_projection_hooks --
    f"{_HP}::L365": _self_limiting(
        "The projections root is absent, leaving no projection hook payload to repair."
    ),
    f"{_HP}::L379": _self_limiting(
        "A projection without hooks.json has no hook payload this repairer can own."
    ),
    f"{_HP}::L376": _self_limiting(
        "The content-fingerprinted quarantine marker already records this hooks payload's "
        "terminal result."
    ),
    f"{_HP}::L389": _self_limiting(
        "A valid unbroken projection hook payload requires no repair or lifecycle mutation."
    ),
    f"{_HP}::L394": _self_limiting(
        "The payload changed to a marked incarnation before the lease, completing its disposition."
    ),
    f"{_HP}::L408": _self_limiting(
        "A durable quarantine marker and QUARANTINED outcome complete this invalid payload's "
        "lifecycle."
    ),
    f"{_HP}::L406": _self_limiting(
        "The hook payload became valid under the lease and no repair remains necessary."
    ),
    f"{_HP}::L465": _resolves_with_contention(
        "An exclusive projection hook lease is held by another live repairer and will release."
    ),
    f"{_HP}::L474": _retries_after_input_changes(
        "A transient projection hook read, write, or rollback failure leaves the candidate "
        "retryable."
    ),
    # -- execution._session_log_recovery::recover_crashed_sessions --
    f"{_SR}::L36": _retries_after_input_changes(
        "The configured trace root is absent, so no crash candidate can be discovered yet."
    ),
    f"{_SR}::L45": _retries_after_input_changes(
        "The trace cannot be statted, so recovery waits for filesystem accessibility to return."
    ),
    f"{_SR}::L47": _resolves_with_contention(
        "A fresh trace may still belong to its active writer and ages past this gate."
    ),
    f"{_SR}::L60": _retries_after_input_changes(
        "An unowned trace is deliberately retained until enrollment or operator input changes."
    ),
    f"{_SR}::L67": _self_limiting(
        "A boot-mismatched trace and enrollment are deleted as a terminal stale-process "
        "disposition."
    ),
    f"{_SR}::L78": _resolves_with_contention(
        "The enrolled process remains live, so its trace waits for the observed owner to exit."
    ),
    f"{_SR}::L89": _self_limiting(
        "A blank JSONL line is ignored while this same trace continues through later recovery "
        "gates."
    ),
    f"{_SR}::L94": _self_limiting(
        "Invalid JSON breaks to permanent-corruption cleanup, which removes the trace and "
        "enrollment."
    ),
    f"{_SR}::L97": _self_limiting(
        "A non-object JSON record breaks to permanent-corruption cleanup and removes this trace."
    ),
    f"{_SR}::L102": _retries_after_input_changes(
        "The trace cannot be read, so recovery waits for filesystem accessibility to return."
    ),
    f"{_SR}::L112": _self_limiting(
        "Permanent trace corruption deletes both trace and enrollment before another startup pass."
    ),
    f"{_SR}::L134": _self_limiting(
        "An alien-command trace and its enrollment are deleted as a terminal safety disposition."
    ),
    f"{_SR}::L140": _retries_after_input_changes(
        "A second stat failure keeps the trace retryable until the filesystem becomes available."
    ),
    f"{_SR}::L171": _retries_after_input_changes(
        "Flush or output-index failure retains both files until output infrastructure recovers."
    ),
}
