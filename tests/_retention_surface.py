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
        ("scripts/pytest_tmp_lifecycle.py", "_candidate_reap_disposition"),
        ("scripts/pytest_tmp_lifecycle.py", "_reap"),
        ("scripts/pytest_tmp_lifecycle.py", "_safe_candidates"),
        (
            "src/autoskillit/server/tools/tools_execution/_managed_leaf.py",
            "_cleanup_owned_child_resources",
        ),
        ("src/autoskillit/fleet/_dispatch_reaper.py", "reap_stale_dispatches"),
        (
            "src/autoskillit/fleet/_dispatch_reaper.py",
            "_handle_immediate_reap_disposition",
        ),
        (
            "src/autoskillit/fleet/_dispatch_reaper.py",
            "_confirm_dispatch_pid_identity",
        ),
        ("src/autoskillit/fleet/_dispatch_reaper.py", "_reap_confirmed_orphan"),
        ("src/autoskillit/fleet/_dispatch_reaper.py", "_reap_running_dispatch"),
        (
            "src/autoskillit/workspace/session_skills/_manager.py",
            "DefaultSessionSkillManager.cleanup_stale",
        ),
        ("src/autoskillit/workspace/session_skills/_manager.py", "_reclaim_stale_entry"),
        ("src/autoskillit/workspace/clone/_registry.py", "cleanup_candidates"),
        ("src/autoskillit/workspace/clone/_worktree.py", "remove_git_worktree"),
        ("src/autoskillit/workspace/clone/_worktree.py", "remove_worktree_sidecar"),
        ("src/autoskillit/execution/evidence/_session_retention.py", "apply_session_retention"),
        (
            "src/autoskillit/execution/evidence/_session_retention.py",
            "apply_execution_candidate_manifest_retention",
        ),
        ("src/autoskillit/hooks/_capture/_sweep.py", "sweep_one"),
        ("src/autoskillit/workspace/_installed/_projection_cache.py", "prune_stale_projections"),
        (
            "src/autoskillit/workspace/_installed/_projection_cache.py",
            "_reconcile_projection_entry",
        ),
        (
            "src/autoskillit/workspace/_installed/_projection_cache.py",
            "_reconcile_projection_retirement",
        ),
        (
            "src/autoskillit/core/plugins/_plugin_artifact_retirement.py",
            "PluginArtifactRetirementEngine._current_identity_status",
        ),
        (
            "src/autoskillit/core/plugins/_plugin_artifact_retirement.py",
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
            "src/autoskillit/workspace/_projected_artifact/_generation_publication.py",
            "_collect_stale_generation_candidates",
        ),
        (
            "src/autoskillit/workspace/_projected_artifact/_generation_prune.py",
            "_reconcile_generation_under_lease",
        ),
        (
            "src/autoskillit/workspace/_installed/_state.py",
            "_enqueue_legacy_installed_plugin_candidate",
        ),
        (
            "src/autoskillit/workspace/_projected_artifact/_hook_repair.py",
            "repair_broken_plugin_cache_hooks",
        ),
        (
            "src/autoskillit/workspace/_projected_artifact/_hook_repair.py",
            "repair_broken_projection_hooks",
        ),
        (
            "src/autoskillit/workspace/_projected_artifact/_hook_repair.py",
            "_repair_hook_incarnation",
        ),
        (
            "src/autoskillit/workspace/_projected_artifact/_hook_repair.py",
            "_hook_repair_needed",
        ),
        (
            "src/autoskillit/workspace/_projected_artifact/_hook_repair.py",
            "_repair_hook_payload_under_lease",
        ),
        (
            "src/autoskillit/execution/evidence/_session_log_recovery.py",
            "_eligible_enrolled_trace",
        ),
        (
            "src/autoskillit/execution/evidence/_session_log_recovery.py",
            "_decode_enrolled_trace",
        ),
        (
            "src/autoskillit/execution/evidence/_session_log_recovery.py",
            "_finalize_crashed_trace",
        ),
        (
            "src/autoskillit/execution/evidence/_session_log_recovery.py",
            "recover_crashed_sessions",
        ),
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
    (
        "scripts/pytest_tmp_lifecycle.py",
        "_candidate_reap_disposition",
    ): _convergence_adapters(("scripts/pytest_tmp_lifecycle.py", "_candidate_reap_disposition")),
    ("scripts/pytest_tmp_lifecycle.py", "_reap"): _convergence_adapters(
        ("scripts/pytest_tmp_lifecycle.py", "_reap")
    ),
    ("scripts/pytest_tmp_lifecycle.py", "_safe_candidates"): _convergence_adapters(
        ("scripts/pytest_tmp_lifecycle.py", "_safe_candidates")
    ),
    (
        "src/autoskillit/server/tools/tools_execution/_managed_leaf.py",
        "_cleanup_owned_child_resources",
    ): _convergence_adapters(
        (
            "src/autoskillit/server/tools/tools_execution/_managed_leaf.py",
            "_cleanup_owned_child_resources",
        )
    ),
    (
        "src/autoskillit/fleet/_dispatch_reaper.py",
        "reap_stale_dispatches",
    ): _convergence_adapters(
        ("src/autoskillit/fleet/_dispatch_reaper.py", "reap_stale_dispatches")
    ),
    (
        "src/autoskillit/fleet/_dispatch_reaper.py",
        "_handle_immediate_reap_disposition",
    ): _convergence_adapters(
        ("src/autoskillit/fleet/_dispatch_reaper.py", "_handle_immediate_reap_disposition")
    ),
    (
        "src/autoskillit/fleet/_dispatch_reaper.py",
        "_confirm_dispatch_pid_identity",
    ): _convergence_adapters(
        ("src/autoskillit/fleet/_dispatch_reaper.py", "_confirm_dispatch_pid_identity")
    ),
    (
        "src/autoskillit/fleet/_dispatch_reaper.py",
        "_reap_confirmed_orphan",
    ): _convergence_adapters(
        ("src/autoskillit/fleet/_dispatch_reaper.py", "_reap_confirmed_orphan")
    ),
    (
        "src/autoskillit/fleet/_dispatch_reaper.py",
        "_reap_running_dispatch",
    ): _convergence_adapters(
        ("src/autoskillit/fleet/_dispatch_reaper.py", "_reap_running_dispatch")
    ),
    (
        "src/autoskillit/workspace/session_skills/_manager.py",
        "DefaultSessionSkillManager.cleanup_stale",
    ): _convergence_adapters(
        (
            "src/autoskillit/workspace/session_skills/_manager.py",
            "DefaultSessionSkillManager.cleanup_stale",
        )
    ),
    (
        "src/autoskillit/workspace/session_skills/_manager.py",
        "_reclaim_stale_entry",
    ): _convergence_adapters(
        ("src/autoskillit/workspace/session_skills/_manager.py", "_reclaim_stale_entry")
    ),
    ("src/autoskillit/workspace/clone/_registry.py", "cleanup_candidates"): _convergence_adapters(
        ("src/autoskillit/workspace/clone/_registry.py", "cleanup_candidates")
    ),
    ("src/autoskillit/workspace/clone/_worktree.py", "remove_git_worktree"): _convergence_adapters(
        ("src/autoskillit/workspace/clone/_worktree.py", "remove_git_worktree")
    ),
    (
        "src/autoskillit/workspace/clone/_worktree.py",
        "remove_worktree_sidecar",
    ): _convergence_adapters(
        ("src/autoskillit/workspace/clone/_worktree.py", "remove_worktree_sidecar")
    ),
    (
        "src/autoskillit/execution/evidence/_session_retention.py",
        "apply_session_retention",
    ): _convergence_adapters(
        ("src/autoskillit/execution/evidence/_session_retention.py", "apply_session_retention")
    ),
    (
        "src/autoskillit/execution/evidence/_session_retention.py",
        "apply_execution_candidate_manifest_retention",
    ): _convergence_adapters(
        (
            "src/autoskillit/execution/evidence/_session_retention.py",
            "apply_execution_candidate_manifest_retention",
        )
    ),
    ("src/autoskillit/hooks/_capture/_sweep.py", "sweep_one"): _convergence_adapters(
        ("src/autoskillit/hooks/_capture/_sweep.py", "sweep_one")
    ),
    (
        "src/autoskillit/workspace/_installed/_projection_cache.py",
        "prune_stale_projections",
    ): _convergence_adapters(
        ("src/autoskillit/workspace/_installed/_projection_cache.py", "prune_stale_projections")
    ),
    (
        "src/autoskillit/workspace/_installed/_projection_cache.py",
        "_reconcile_projection_entry",
    ): _convergence_adapters(
        (
            "src/autoskillit/workspace/_installed/_projection_cache.py",
            "_reconcile_projection_entry",
        )
    ),
    (
        "src/autoskillit/workspace/_installed/_projection_cache.py",
        "_reconcile_projection_retirement",
    ): _convergence_adapters(
        (
            "src/autoskillit/workspace/_installed/_projection_cache.py",
            "_reconcile_projection_retirement",
        )
    ),
    (
        "src/autoskillit/core/plugins/_plugin_artifact_retirement.py",
        "PluginArtifactRetirementEngine._current_identity_status",
    ): _convergence_adapters(
        (
            "src/autoskillit/core/plugins/_plugin_artifact_retirement.py",
            "PluginArtifactRetirementEngine._current_identity_status",
        )
    ),
    (
        "src/autoskillit/core/plugins/_plugin_artifact_retirement.py",
        "PluginArtifactRetirementEngine.try_reclaim",
    ): _convergence_adapters(
        (
            "src/autoskillit/core/plugins/_plugin_artifact_retirement.py",
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
        "src/autoskillit/workspace/_projected_artifact/_generation_publication.py",
        "_collect_stale_generation_candidates",
    ): _convergence_adapters(
        (
            "src/autoskillit/workspace/_projected_artifact/_generation_publication.py",
            "_collect_stale_generation_candidates",
        )
    ),
    (
        "src/autoskillit/workspace/_projected_artifact/_generation_prune.py",
        "_reconcile_generation_under_lease",
    ): _convergence_adapters(
        (
            "src/autoskillit/workspace/_projected_artifact/_generation_prune.py",
            "_reconcile_generation_under_lease",
        )
    ),
    (
        "src/autoskillit/workspace/_installed/_state.py",
        "_enqueue_legacy_installed_plugin_candidate",
    ): _convergence_adapters(
        (
            "src/autoskillit/workspace/_installed/_state.py",
            "_enqueue_legacy_installed_plugin_candidate",
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
        "src/autoskillit/workspace/_projected_artifact/_hook_repair.py",
        "_repair_hook_incarnation",
    ): _convergence_adapters(
        (
            "src/autoskillit/workspace/_projected_artifact/_hook_repair.py",
            "_repair_hook_incarnation",
        )
    ),
    (
        "src/autoskillit/workspace/_projected_artifact/_hook_repair.py",
        "_hook_repair_needed",
    ): _convergence_adapters(
        (
            "src/autoskillit/workspace/_projected_artifact/_hook_repair.py",
            "_hook_repair_needed",
        )
    ),
    (
        "src/autoskillit/workspace/_projected_artifact/_hook_repair.py",
        "_repair_hook_payload_under_lease",
    ): _convergence_adapters(
        (
            "src/autoskillit/workspace/_projected_artifact/_hook_repair.py",
            "_repair_hook_payload_under_lease",
        )
    ),
    (
        "src/autoskillit/execution/evidence/_session_log_recovery.py",
        "_eligible_enrolled_trace",
    ): _convergence_adapters(
        (
            "src/autoskillit/execution/evidence/_session_log_recovery.py",
            "_eligible_enrolled_trace",
        )
    ),
    (
        "src/autoskillit/execution/evidence/_session_log_recovery.py",
        "_decode_enrolled_trace",
    ): _convergence_adapters(
        (
            "src/autoskillit/execution/evidence/_session_log_recovery.py",
            "_decode_enrolled_trace",
        )
    ),
    (
        "src/autoskillit/execution/evidence/_session_log_recovery.py",
        "_finalize_crashed_trace",
    ): _convergence_adapters(
        (
            "src/autoskillit/execution/evidence/_session_log_recovery.py",
            "_finalize_crashed_trace",
        )
    ),
    (
        "src/autoskillit/execution/evidence/_session_log_recovery.py",
        "recover_crashed_sessions",
    ): _convergence_adapters(
        (
            "src/autoskillit/execution/evidence/_session_log_recovery.py",
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
        "src/autoskillit/execution/evidence/session_log.py",
        "flush_session_log",
    ): _DELEGATED_MUTATION_REASON,
    (
        "src/autoskillit/execution/child_outcomes.py",
        "reconcile_child_outcome_snapshots",
    ): _SEPARATE_LIFECYCLE_REASON,
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
        "_sweep_orphaned_staging",
    ): _DELEGATED_MUTATION_REASON,
    (
        "src/autoskillit/workspace/_projected_artifact/_hook_repair.py",
        "_rollback_repair",
    ): _DELEGATED_MUTATION_REASON,
    (
        "src/autoskillit/workspace/_projected_artifact/_hook_repair.py",
        "_safe_incarnations",
    ): _DELEGATED_MUTATION_REASON,
    (
        "src/autoskillit/workspace/_installed/_projection_cache.py",
        "ProjectedPluginRetirementOwner.enqueue_retirement",
    ): _DELEGATED_MUTATION_REASON,
    (
        "src/autoskillit/workspace/_installed/_projection_cache.py",
        "ProjectedPluginRetirementOwner.try_reclaim",
    ): _DELEGATED_MUTATION_REASON,
    ("src/autoskillit/workspace/clone/__init__.py", "remove_clone"): _DELEGATED_MUTATION_REASON,
    (
        "src/autoskillit/workspace/session_skills/_lifecycle.py",
        "_remove_and_verify",
    ): _DELEGATED_MUTATION_REASON,
    (
        "src/autoskillit/workspace/session_skills/_materialization.py",
        "_remove_profile_staging",
    ): _DELEGATED_MUTATION_REASON,
    (
        "src/autoskillit/workspace/session_skills/_materialization.py",
        "_merge_profile_projection",
    ): _DELEGATED_MUTATION_REASON,
    # Commands and composition boundaries.
    ("src/autoskillit/cli/_workspace.py", "_clean_run_directories"): _COMMAND_BOUNDARY_REASON,
    (
        "src/autoskillit/cli/fleet/__init__.py",
        "_cleanup_campaign_artifacts",
    ): _COMMAND_BOUNDARY_REASON,
    ("src/autoskillit/cli/install/_marketplace.py", "upgrade"): _COMMAND_BOUNDARY_REASON,
    (
        "src/autoskillit/cli/update/_obligation_repair.py",
        "attempt_obligation_repair",
    ): _COMMAND_BOUNDARY_REASON,
    (
        "src/autoskillit/hooks/session_start_hook.py",
        "_sweep_kitchen_markers",
    ): _COMMAND_BOUNDARY_REASON,
    (
        "src/autoskillit/workspace/_installed/_state.py",
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
        "src/autoskillit/execution/evidence/_recording_skills.py",
        "snapshot_skill_dir",
    ): _SEPARATE_LIFECYCLE_REASON,
    (
        "src/autoskillit/execution/backends/_codex/session_reconciliation.py",
        "_CodexSessionReconciliationMixin.recover",
    ): _SEPARATE_LIFECYCLE_REASON,
    (
        "src/autoskillit/fleet/campaign_state/state.py",
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
        "src/autoskillit/hooks/_runtime/_exploration_request_record.py",
        "_cleanup_expired",
    ): _SEPARATE_LIFECYCLE_REASON,
    (
        "src/autoskillit/planner/manifests.py",
        "reconcile_wp_files",
    ): _SEPARATE_LIFECYCLE_REASON,
    (
        "src/autoskillit/recipe/cmd_rpc/_cmd_rpc_guards.py",
        "_remove_nested_worktrees",
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


_CRD = "scripts/pytest_tmp_lifecycle.py::_candidate_reap_disposition"
_R = "scripts/pytest_tmp_lifecycle.py::_reap"
_S = "scripts/pytest_tmp_lifecycle.py::_safe_candidates"
_D = "src/autoskillit/fleet/_dispatch_reaper.py::reap_stale_dispatches"
_DI = "src/autoskillit/fleet/_dispatch_reaper.py::_handle_immediate_reap_disposition"
_DPI = "src/autoskillit/fleet/_dispatch_reaper.py::_confirm_dispatch_pid_identity"
_DCO = "src/autoskillit/fleet/_dispatch_reaper.py::_reap_confirmed_orphan"
_DR = "src/autoskillit/fleet/_dispatch_reaper.py::_reap_running_dispatch"
_CS = (
    "src/autoskillit/workspace/session_skills/_manager.py"
    "::DefaultSessionSkillManager.cleanup_stale"
)
_CSE = "src/autoskillit/workspace/session_skills/_manager.py::_reclaim_stale_entry"
_WGW = "src/autoskillit/workspace/clone/_worktree.py::remove_git_worktree"
_WWS = "src/autoskillit/workspace/clone/_worktree.py::remove_worktree_sidecar"
_SL = "src/autoskillit/execution/evidence/_session_retention.py::apply_session_retention"
_ECMR = (
    "src/autoskillit/execution/evidence/_session_retention.py::"
    "apply_execution_candidate_manifest_retention"
)
_SW = "src/autoskillit/hooks/_capture/_sweep.py::sweep_one"
_PP = "src/autoskillit/workspace/_installed/_projection_cache.py::prune_stale_projections"
_PRE = "src/autoskillit/workspace/_installed/_projection_cache.py::_reconcile_projection_entry"
_PRT = (
    "src/autoskillit/workspace/_installed/_projection_cache.py::_reconcile_projection_retirement"
)
_PC = (
    "src/autoskillit/core/plugins/_plugin_artifact_retirement.py::"
    "PluginArtifactRetirementEngine.try_reclaim"
)
_PCS = (
    "src/autoskillit/core/plugins/_plugin_artifact_retirement.py::"
    "PluginArtifactRetirementEngine._current_identity_status"
)
_CT = (
    "src/autoskillit/cli/install/_plugin_artifact.py::DefaultPluginRetirementCoordinator.sweep_due"
)
_GP = (
    "src/autoskillit/workspace/_projected_artifact/"
    "_generation_publication.py::prune_stale_generations"
)
_GPC = (
    "src/autoskillit/workspace/_projected_artifact/"
    "_generation_publication.py::_collect_stale_generation_candidates"
)
_GPU = (
    "src/autoskillit/workspace/_projected_artifact/"
    "_generation_prune.py::_reconcile_generation_under_lease"
)
_IL = "src/autoskillit/workspace/_installed/_state.py::_enqueue_legacy_installed_plugin_candidate"
_HC = (
    "src/autoskillit/workspace/_projected_artifact/"
    "_hook_repair.py::repair_broken_plugin_cache_hooks"
)
_HP = (
    "src/autoskillit/workspace/_projected_artifact/_hook_repair.py::repair_broken_projection_hooks"
)
_HR = "src/autoskillit/workspace/_projected_artifact/_hook_repair.py::_repair_hook_incarnation"
_HRP = "src/autoskillit/workspace/_projected_artifact/_hook_repair.py::_hook_repair_needed"
_HRL = (
    "src/autoskillit/workspace/_projected_artifact/"
    "_hook_repair.py::_repair_hook_payload_under_lease"
)
_SR = "src/autoskillit/execution/evidence/_session_log_recovery.py::recover_crashed_sessions"
_SRE = "src/autoskillit/execution/evidence/_session_log_recovery.py::_eligible_enrolled_trace"
_SRD = "src/autoskillit/execution/evidence/_session_log_recovery.py::_decode_enrolled_trace"
_SRF = "src/autoskillit/execution/evidence/_session_log_recovery.py::_finalize_crashed_trace"

AUDITED_RETENTION_DECISIONS: dict[str, RetentionDecision | SafetyDecision] = {
    # -- scripts.pytest_tmp_lifecycle::_candidate_reap_disposition --
    f"{_CRD}::L486": RetentionDecision(
        Revocability.MONOTONIC,
        "A markerless candidate is retained by either a revocable reference or a monotonic "
        "snapshot reference -- the only branch where monotonic evidence may protect, since "
        "there is no owner marker to supply a sound liveness proof instead.",
        bounded_by="never bound-reclaimable (no owner to prove provably dead)",
    ),
    f"{_CRD}::L491": _self_limiting(
        "A markerless candidate younger than legacy_age_minutes might be another "
        "concurrent _setup mid-creation; never touched by the bound, only by this age gate."
    ),
    f"{_CRD}::L492": _self_limiting(
        "A mature unreferenced markerless candidate returns the normal deletion disposition; "
        "this branch completes eligibility evaluation without deferring reclamation."
    ),
    f"{_CRD}::L497": RetentionDecision(
        Revocability.REVOCABLE,
        "A live or indeterminate owner is retained unconditionally; only provably dead may "
        "ever be reclaimed, per the three-outcome liveness contract.",
    ),
    f"{_CRD}::L501": RetentionDecision(
        Revocability.REVOCABLE,
        "A valid-dead or corrupt-marker generation holding a revocable kernel reference "
        "is retained; proof of present use overrides the owner-marker disposition.",
    ),
    f"{_CRD}::L503": RetentionDecision(
        Revocability.REVOCABLE,
        "A valid-dead or corrupt-marker generation within grace is retained by normal reap "
        "but remains eligible for early reclamation under capacity pressure.",
        bounded_by="ReclamationBound (select_overflow eligibility)",
    ),
    # -- scripts.pytest_tmp_lifecycle::_reap --
    f"{_R}::L528": _retries_after_input_changes(
        "Scan-level failure retains every candidate rather than treating an empty result "
        "as absence of evidence; the fail-closed contract tests/AGENTS.md documents."
    ),
    f"{_R}::L535": _self_limiting(
        "The generation _setup is currently claiming is excluded from its own reap pass."
    ),
    f"{_R}::L539": _self_limiting(
        "FileNotFoundError on lstat means the candidate is already gone; nothing to reclaim."
    ),
    f"{_R}::L542": _retries_after_input_changes(
        "An OSError inspecting the candidate is an inspection failure, not eligibility evidence."
    ),
    f"{_R}::L545": _retries_after_input_changes(
        "A symlink or non-directory entry under the platform root is a safety exclusion, "
        "never a reclamation candidate regardless of any evidence."
    ),
    f"{_R}::L548": _retries_after_input_changes(
        "A candidate owned by a different uid is out of this reaper's authority to touch."
    ),
    f"{_R}::L560": _retries_after_input_changes(
        "The disposition helper classified this candidate as eligible for normal reaping; "
        "removal happens in place, and a later pass can only reconsider it after the input "
        "(owner/reference/age) changes."
    ),
    # -- scripts.pytest_tmp_lifecycle::_safe_candidates --
    f"{_S}::L419": _retries_after_input_changes(
        "Cannot normalize private-root permissions; the whole private-root scan is skipped "
        "rather than risk enumerating an untrusted-mode directory."
    ),
    # -- fleet._dispatch_reaper::reap_stale_dispatches --
    f"{_D}::L351": _self_limiting(
        "No campaign state file at all; nothing to reap for this campaign."
    ),
    f"{_D}::L356": _retries_after_input_changes(
        "The state file could not be parsed; an unreadable state must not be interpreted "
        "as zero running dispatches."
    ),
    f"{_D}::L361": _self_limiting(
        "Nothing in RUNNING status for this campaign; the candidate set is empty."
    ),
    f"{_D}::L369": RetentionDecision(
        Revocability.REVOCABLE,
        "A reaper never reaps its own campaign's siblings -- self-exclusion is a live-owner "
        "equivalent, verified by the caller's own campaign_id match, not by any /proc read.",
    ),
    # -- fleet._dispatch_reaper::_handle_immediate_reap_disposition --
    f"{_DI}::L130": RetentionDecision(
        Revocability.REVOCABLE,
        "A caller-declared protected dispatch id set is honoured unconditionally, the same "
        "self-exclusion family as the own-campaign skip-all.",
    ),
    f"{_DI}::L140": RetentionDecision(
        Revocability.REVOCABLE,
        "A dispatch younger than min_reap_age_seconds is retained -- the textbook grace "
        "period gate on process age.",
    ),
    f"{_DI}::L149": _self_limiting(
        "pid == 0 is a reap outcome (marks the dispatch dead), not an eligibility skip -- "
        "the return here follows the reclaim action, it does not precede it."
    ),
    f"{_DI}::L161": _self_limiting(
        "A boot-id mismatch is a reap outcome (marks the dispatch pid-recycled), not an "
        "eligibility skip -- the return follows the reclaim action."
    ),
    f"{_DI}::L165": _self_limiting(
        "psutil.pid_exists() false is a reap outcome (marks the dispatch dead), not an "
        "eligibility skip -- the return follows the reclaim action."
    ),
    # -- fleet._dispatch_reaper::_confirm_dispatch_pid_identity --
    f"{_DPI}::L193": _self_limiting(
        "psutil.NoSuchProcess during create_time comparison is a reap outcome (marks the "
        "dispatch dead), not an eligibility skip."
    ),
    # -- fleet._dispatch_reaper::_reap_confirmed_orphan --
    f"{_DCO}::L223": RetentionDecision(
        Revocability.REVOCABLE,
        "An active dispatch heartbeat (a live kernel-observable mtime freshness check) "
        "retains the dispatch -- the domain equivalent of a revocable kernel reference.",
    ),
    f"{_DCO}::L243": _retries_after_input_changes(
        "kill_process_tree raised; execution failure, not an eligibility gate on the "
        "candidate itself."
    ),
    f"{_DCO}::L256": RetentionDecision(
        Revocability.REVOCABLE,
        "Survivors reported by kill_process_tree's cleanup_result mean the process may "
        "still be alive -- the dispatch record is deliberately left RUNNING for a retry, "
        "an observed-liveness result standing in for a direct /proc reference check.",
    ),
    # -- fleet._dispatch_reaper::_reap_running_dispatch --
    f"{_DR}::L283": _self_limiting(
        "An immediate disposition already handled this dispatch, so the identity pipeline "
        "does not reconsider it."
    ),
    # -- workspace.session_skills._manager::cleanup_stale --
    f"{_CS}::L643": _self_limiting(
        "The candidate root vanished or was replaced before its scan; nothing there to reclaim."
    ),
    f"{_CS}::L646": _self_limiting(
        "The session-leases bookkeeping subdirectory itself is not a session; a structural "
        "exclusion, not an eligibility decision."
    ),
    f"{_CS}::L648": _self_limiting(
        "A non-directory entry under the candidate root is a type guard, never a session "
        "directory this function reclaims."
    ),
    f"{_CS}::L651": RetentionDecision(
        Revocability.REVOCABLE,
        "An entry with an in-process session lease held by this process is retained -- "
        "self-held-lease evidence overrides the age threshold, the domain equivalent of a "
        "live owner reference.",
    ),
    f"{_CSE}::L82": RetentionDecision(
        Revocability.REVOCABLE,
        "Failure to acquire the non-blocking lease means another process currently holds "
        "a live lock on this entry, a directly observed live-owner reference.",
    ),
    f"{_CS}::L660": RetentionDecision(
        Revocability.REVOCABLE,
        "Removal did not occur because the re-checked mtime under lease is fresh again or "
        "the entry already vanished -- the mtime re-check under lease is the reclamation-"
        "defining age/liveness re-verification for this candidate.",
    ),
    # -- workspace.clone._worktree::remove_git_worktree --
    f"{_WGW}::L168": _self_limiting(
        "The worktree path does not exist on disk at all; nothing here to reclaim."
    ),
    f"{_WGW}::L177": _self_limiting(
        "The git worktree remove call already succeeded; this reports a completed removal, "
        "not a retention skip."
    ),
    # -- workspace.clone._worktree::remove_worktree_sidecar --
    f"{_WWS}::L209": _self_limiting(
        "The sidecar directory does not exist on disk at all; nothing here to reclaim or retain."
    ),
    # -- execution._session_retention::apply_session_retention --
    f"{_SL}::L104": _self_limiting(
        "The just-recommitted crash-recovery directory for this same dir_name is protected "
        "from being counted as expired in the same flush that created it, the session-log "
        "equivalent of a reaper excluding the generation it is currently claiming."
    ),
    f"{_SL}::L120": RetentionDecision(
        Revocability.REVOCABLE,
        "A caller-declared protected campaign id is honoured unconditionally, retaining "
        "the session directory regardless of its age, the same self-exclusion family as "
        "the dispatch reaper's protected-id set.",
    ),
    # -- execution._session_retention::apply_execution_candidate_manifest_retention --
    f"{_ECMR}::L159": _self_limiting(
        "The manifest currently being written is excluded from the retention pass that it "
        "triggered, so it cannot be reclaimed before publication completes."
    ),
    f"{_ECMR}::L163": _self_limiting(
        "A manifest that vanished during its observed scan is already absent and requires no "
        "further retention action."
    ),
    f"{_ECMR}::L171": _retries_after_input_changes(
        "Unreadable candidate evidence is retained until its file can be read or is replaced; "
        "a parse failure is never proof that the selection may be discarded."
    ),
    f"{_ECMR}::L175": RetentionDecision(
        Revocability.REVOCABLE,
        "Candidate evidence for a protected campaign is retained unconditionally while the "
        "caller declares that campaign live.",
    ),
    f"{_ECMR}::L184": _retries_after_input_changes(
        "Without a telemetry-clear marker, or while the manifest is newer than that marker, "
        "the clear-based pass retains it until retention evidence changes."
    ),
    f"{_ECMR}::L186": RetentionDecision(
        Revocability.REVOCABLE,
        "The current, protected, or still-pending manifest name is retained as live "
        "execution evidence.",
    ),
    f"{_ECMR}::L201": _self_limiting(
        "The normal size window is satisfied, so the capacity pass stops without further "
        "candidate deletion."
    ),
    f"{_ECMR}::L203": RetentionDecision(
        Revocability.REVOCABLE,
        "Protected and pending manifest names remain durable evidence even when the size "
        "window would otherwise evict them.",
    ),
    # -- hooks._capture._sweep::sweep_one --
    f"{_SW}::L464": RetentionDecision(
        Revocability.REVOCABLE,
        "The record is absent, already deleted, or its next_attempt_at is still in the "
        "future -- the schedule/age gate retains anything not yet eligible for its next "
        "sweep attempt.",
    ),
    f"{_SW}::L475": RetentionDecision(
        Revocability.REVOCABLE,
        "An issued or published capture reference has not yet reached its recorded expiry; "
        "retained until the reference-expiry deadline passes.",
    ),
    f"{_SW}::L500": RetentionDecision(
        Revocability.REVOCABLE,
        "Re-verified under the second lock: the record vanished, changed identity since "
        "the first check, or is still not due -- the same due-date gate re-applied after "
        "the lease acquisition race window.",
    ),
    f"{_SW}::L523": _self_limiting(
        "Abandoned-record normalization determined the record is already DELETED; this "
        "reports that terminal outcome, not a retention gate."
    ),
    f"{_SW}::L556": _self_limiting(
        "The successful-deletion completion path; not a retention skip, this line reports "
        "that reclamation succeeded."
    ),
    f"{_SW}::L558": RetentionDecision(
        Revocability.REVOCABLE,
        "A CarrierLeaseLive exception means an active lease currently holds this capture; "
        "retained until the lease is released, a directly observed live reference.",
    ),
    f"{_SW}::L571": RetentionDecision(
        Revocability.REVOCABLE,
        "A tampered record is retained for a fixed forensic hold window recorded via "
        "next_attempt_at, evidence preserved for investigation before re-eligibility.",
    ),
    f"{_SW}::L585": _self_limiting(
        "A lifecycle or OSError during the delete attempt is an execution failure, not "
        "evidence about the candidate's liveness; retried up to max_retry_seconds."
    ),
    # -- workspace._installed._projection_cache::prune_stale_projections --
    f"{_PP}::L699": _retries_after_input_changes(
        "The managed-home boundary does not contain the projection owner root, so mutation "
        "is refused before enumeration."
    ),
    f"{_PP}::L702": _self_limiting(
        "The projections root does not exist; there is nothing here to prune."
    ),
    f"{_PP}::L711": _retries_after_input_changes(
        "An operational failure inspecting the projection root defers reconciliation "
        "without risking launch availability."
    ),
    # -- workspace._installed._projection_cache::_reconcile_projection_entry --
    f"{_PRE}::L469": _retries_after_input_changes(
        "A foreign user-writable cache entry is classified as deferred rather than "
        "aborting launch."
    ),
    f"{_PRE}::L472": _retries_after_input_changes(
        "The caller-selected active projection is intentionally excluded from stale "
        "reconciliation."
    ),
    f"{_PRE}::L474": _self_limiting(
        "A deterministic residue staging entry delegates to its original-key locked "
        "resume transition."
    ),
    f"{_PRE}::L482": _retries_after_input_changes(
        "A recognized non-projection namespace belongs to another lifecycle owner and "
        "remains untouched."
    ),
    f"{_PRE}::L484": _retries_after_input_changes(
        "A projection outside the exact scanned root fails the direct-child ownership guard."
    ),
    f"{_PRT}::L513": RetentionDecision(
        Revocability.REVOCABLE,
        "Lease contention means another process currently holds an exclusive lock on this "
        "candidate, a directly observed live reference.",
    ),
    f"{_PRT}::L515": _retries_after_input_changes(
        "Lease acquisition failed operationally, so reconciliation defers without "
        "claiming deletion authority."
    ),
    f"{_PRT}::L521": _self_limiting(
        "A permanently invalid projection delegates to the terminal quarantine transition "
        "under the held lease and lock."
    ),
    f"{_PRT}::L528": _retries_after_input_changes(
        "Identity resolution was unavailable for this candidate; an inspection failure, "
        "not evidence of liveness."
    ),
    f"{_PRT}::L531": _retries_after_input_changes(
        "The retirement queue could not be read to record this candidate; an infrastructure "
        "failure, not liveness evidence."
    ),
    f"{_PRT}::L533": _self_limiting(
        "A new exact retirement record was durably created; this reports successful disposition."
    ),
    f"{_PRT}::L534": _self_limiting(
        "The exact retirement record already exists, so no duplicate durable mutation is needed."
    ),
    f"{_PRT}::L536": _retries_after_input_changes(
        "Install-lock or reconciliation I/O failed operationally and leaves the candidate "
        "retryable."
    ),
    # -- core.plugins._plugin_artifact_retirement::_current_identity_status --
    f"{_PCS}::L188": _retries_after_input_changes(
        "Resolving the current on-disk identity failed as unavailable; an inspection "
        "failure, not evidence of liveness."
    ),
    f"{_PCS}::L190": _retries_after_input_changes(
        "On-disk identity validation failed for the current generation; a validation guard, "
        "not a liveness or age decision."
    ),
    f"{_PCS}::L192": _retries_after_input_changes(
        "The current on-disk identity no longer matches the record's recorded identity; a "
        "consistency guard against reclaiming the wrong artifact."
    ),
    # -- core.plugins._plugin_artifact_retirement::try_reclaim --
    f"{_PC}::L222": _retries_after_input_changes(
        "The record's artifact_kind does not match this coordinator's own kind; a type/"
        "ownership guard, not a liveness decision."
    ),
    f"{_PC}::L224": RetentionDecision(
        Revocability.REVOCABLE,
        "The record's scheduled not_before time has not yet passed; retained until the "
        "grace/backoff window elapses.",
    ),
    f"{_PC}::L226": _retries_after_input_changes(
        "This coordinator no longer claims ownership of the managed path; an ownership "
        "guard, not liveness evidence."
    ),
    f"{_PC}::L233": RetentionDecision(
        Revocability.REVOCABLE,
        "Lease contention means another process currently holds an exclusive lock on this "
        "artifact, a directly observed live reference.",
    ),
    f"{_PC}::L239": _retries_after_input_changes(
        "Lease acquisition failed with an OSError or RuntimeError; an infrastructure "
        "failure, not evidence about the record's liveness."
    ),
    f"{_PC}::L248": _self_limiting(
        "The retiring cache record is already absent, removed by a concurrent sweep; "
        "reports an already-completed outcome, not a retention gate."
    ),
    f"{_PC}::L250": _retries_after_input_changes(
        "The retiring cache is not in the expected exact-v2 state; an infrastructure/"
        "consistency guard, not liveness evidence."
    ),
    f"{_PC}::L257": _self_limiting(
        "The record is no longer present in the retiring queue, removed concurrently; "
        "reports an already-completed outcome, not a retention gate."
    ),
    f"{_PC}::L259": _self_limiting(
        "The freshly re-read queued record no longer matches the caller's exact identity; "
        "a consistency guard against acting on stale data."
    ),
    f"{_PC}::L264": RetentionDecision(
        Revocability.REVOCABLE,
        "Re-verified under lock: the record's not_before time has not yet passed; retained "
        "until due.",
    ),
    f"{_PC}::L266": RetentionDecision(
        Revocability.REVOCABLE,
        "The managed path is the actively selected generation right now; retained because "
        "it is currently live and in use, an observed liveness reference.",
    ),
    f"{_PC}::L279": _retries_after_input_changes(
        "Updating the retiring-cache record failed due to an unsafe cache state; an "
        "infrastructure failure, not liveness evidence."
    ),
    f"{_PC}::L284": _self_limiting(
        "None of the managed, manifest, or staging paths exist on disk; the artifact is "
        "already gone, reporting completion rather than a retention gate."
    ),
    f"{_PC}::L287": _retries_after_input_changes(
        "The staging path is in an ambiguous or unsafe state relative to the managed path; "
        "a consistency guard, not liveness evidence."
    ),
    f"{_PC}::L297": _retries_after_input_changes(
        "The non-destructive identity decision deferred reclamation because identity "
        "resolution was unavailable; an inspection failure, not liveness evidence."
    ),
    f"{_PC}::L300": _retries_after_input_changes(
        "Updating the retiring-cache record failed while rejecting an invalid or mismatched "
        "identity; an infrastructure failure, not liveness evidence."
    ),
    f"{_PC}::L305": _retries_after_input_changes(
        "The non-destructive identity decision rejected the current artifact identity; a "
        "validation or consistency guard, not a liveness or age decision."
    ),
    f"{_PC}::L313": _retries_after_input_changes(
        "Renaming the managed path into staging failed with an OSError; an execution "
        "failure, not liveness evidence."
    ),
    f"{_PC}::L327": _retries_after_input_changes(
        "The artifact was already removed from disk; updating the retiring-cache record "
        "afterward failed due to an unsafe cache state, an infrastructure failure."
    ),
    f"{_PC}::L333": _retries_after_input_changes(
        "Removing the manifest or staging directory failed with an OSError; an execution "
        "failure during the delete attempt, not liveness evidence."
    ),
    f"{_PC}::L338": _self_limiting(
        "The successful-reclaim completion path; not a retention skip, this line reports "
        "that reclamation succeeded."
    ),
    # -- cli.install._plugin_artifact::sweep_due --
    f"{_CT}::L533": _retries_after_input_changes(
        "The retiring cache is not in a safe exact-v2 state, corrupt or future-versioned; "
        "an infrastructure guard, not a liveness decision."
    ),
    f"{_CT}::L541": _retries_after_input_changes(
        "No registered owner claims this legacy evidence's artifact kind; a routing guard, "
        "not liveness evidence."
    ),
    f"{_CT}::L546": _retries_after_input_changes(
        "Reading due retiring records failed under an unsafe cache state; an infrastructure "
        "failure, not evidence about any record's liveness."
    ),
    f"{_CT}::L551": _retries_after_input_changes(
        "No registered owner claims this record's artifact kind; a routing guard, not "
        "liveness evidence."
    ),
    # -- workspace._projected_artifact._generation_publication::prune_stale_generations --
    f"{_GP}::L561": _self_limiting(
        "The generation store does not exist, so this invocation has no candidates to prune."
    ),
    f"{_GPC}::L519": _self_limiting(
        "A hidden, symlinked, or non-directory version entry cannot contain a generation "
        "incarnation this reclaimer owns."
    ),
    f"{_GPC}::L527": _self_limiting(
        "An unmanaged hidden entry is outside the deterministic generation-residue "
        "lifecycle namespace."
    ),
    f"{_GPC}::L529": _self_limiting(
        "A symlink or non-directory incarnation cannot be a managed generation retirement "
        "candidate."
    ),
    f"{_GPC}::L531": _self_limiting(
        "The selected generation remains active and is not a stale candidate for this pass."
    ),
    f"{_GP}::L572": _self_limiting(
        "The generation root or version directory vanished during enumeration, leaving no "
        "stable candidate set for this pass."
    ),
    f"{_GP}::L575": _self_limiting(
        "Generation enumeration hit an I/O failure, so this fail-open maintenance pass "
        "defers every candidate without making a retention decision."
    ),
    # -- workspace._projected_artifact._generation_prune::_reconcile_generation_under_lease --
    f"{_GPU}::L302": _retries_after_input_changes(
        "Post-lease containment or selection revalidation changed, so the candidate remains "
        "untouched until a later scan sees a stable eligible generation."
    ),
    f"{_GPU}::L306": _self_limiting(
        "A permanently invalid generation delegates to terminal quarantine while the writer "
        "lease remains held."
    ),
    f"{_GPU}::L315": _retries_after_input_changes(
        "Exact generation identity is temporarily unavailable, an inspection failure rather "
        "than authority to quarantine or retire the candidate."
    ),
    f"{_GPU}::L317": _retries_after_input_changes(
        "An operational identity read failure leaves the generation retryable on a later pass."
    ),
    f"{_GPU}::L320": _retries_after_input_changes(
        "The retirement queue could not be read to record this generation candidate safely."
    ),
    f"{_GPU}::L322": _self_limiting(
        "A newly created exact retirement record completes this generation's current disposition."
    ),
    # -- workspace._installed._state::_enqueue_legacy_installed_plugin_candidate --
    f"{_IL}::L458": _self_limiting(
        "The running legacy version without a selected generation remains outside retirement."
    ),
    f"{_IL}::L461": _self_limiting(
        "A durable rejected-legacy marker already records this invalid candidate's terminal "
        "disposition."
    ),
    f"{_IL}::L485": _self_limiting(
        "Another reconciler created the same durable rejection marker, completing this "
        "candidate's disposition."
    ),
    f"{_IL}::L491": _self_limiting(
        "Writing the rejected-legacy marker durably records this invalid candidate for quiet "
        "later passes."
    ),
    f"{_IL}::L493": _resolves_with_contention(
        "A shared lease is currently contended, so the legacy candidate waits for its holder."
    ),
    # -- workspace._projected_artifact._hook_repair::repair_broken_plugin_cache_hooks --
    f"{_HC}::L439": _self_limiting(
        "The plugin cache root is absent, leaving no hook incarnation to repair."
    ),
    # -- workspace._projected_artifact._hook_repair::repair_broken_projection_hooks --
    f"{_HP}::L494": _self_limiting(
        "The projections root is absent, leaving no projection hook payload to repair."
    ),
    # -- workspace._projected_artifact._hook_repair::_hook_repair_needed --
    f"{_HRP}::L272": _self_limiting(
        "An incarnation without hooks.json has no hook payload this repairer can own."
    ),
    f"{_HRP}::L275": _self_limiting(
        "The content-fingerprinted quarantine marker already records this hooks payload's "
        "terminal result."
    ),
    f"{_HRP}::L279": _self_limiting(
        "A malformed preflight payload is routed into the held-lease repair path before any "
        "durable quarantine decision is made."
    ),
    # -- workspace._projected_artifact._hook_repair::_repair_hook_incarnation --
    f"{_HR}::L371": _self_limiting(
        "The unleased preflight found no relocatable or dispatcher repair work for this payload."
    ),
    f"{_HR}::L373": _self_limiting(
        "The lease-held transaction owns the final repaired, quarantined, or quiet outcome."
    ),
    f"{_HR}::L383": _resolves_with_contention(
        "An exclusive hook lease is held by another live repairer and will release."
    ),
    f"{_HR}::L389": _retries_after_input_changes(
        "A transient hook read, write, or rollback failure leaves the candidate retryable."
    ),
    # -- workspace._projected_artifact._hook_repair::_repair_hook_payload_under_lease --
    f"{_HRL}::L296": _self_limiting(
        "The payload changed to a marked incarnation before the lease, completing its disposition."
    ),
    f"{_HRL}::L301": _self_limiting(
        "A durable quarantine marker and QUARANTINED outcome complete this invalid payload's "
        "lifecycle."
    ),
    f"{_HRL}::L307": _self_limiting(
        "The hook payload became valid under the lease and no repair remains necessary."
    ),
    f"{_HRL}::L313": _self_limiting(
        "Identity validation writes a durable quarantine marker before reporting the terminal "
        "outcome."
    ),
    # -- execution._session_log_recovery crash-recovery helpers --
    # Coordinates include the child-outcome reconciliation pass from issue #4623,
    # the typed infrastructure-outcome imports from issue #4927, and deferring
    # the #4623 pass's own child_outcomes import to inside the try block (issue
    # #4672 decomposition — module-level would circularly import back through
    # the evidence/ gateway that now wraps this file).
    f"{_SR}::L230": _retries_after_input_changes(
        "The configured trace root is absent, so no crash candidate can be discovered yet."
    ),
    f"{_SRE}::L45": _retries_after_input_changes(
        "The trace cannot be statted, so recovery waits for filesystem accessibility to return."
    ),
    f"{_SRE}::L47": _resolves_with_contention(
        "A fresh trace may still belong to its active writer and ages past this gate."
    ),
    f"{_SRE}::L58": _retries_after_input_changes(
        "An unowned trace is deliberately retained until enrollment or operator input changes."
    ),
    f"{_SRE}::L63": _self_limiting(
        "A boot-mismatched trace and enrollment are deleted as a terminal stale-process "
        "disposition."
    ),
    f"{_SRE}::L72": _resolves_with_contention(
        "The enrolled process remains live, so its trace waits for the observed owner to exit."
    ),
    f"{_SRD}::L87": _self_limiting(
        "A blank JSONL line is ignored while this same trace continues through later recovery "
        "gates."
    ),
    f"{_SRD}::L92": _self_limiting(
        "Invalid JSON breaks to permanent-corruption cleanup, which removes the trace and "
        "enrollment."
    ),
    f"{_SRD}::L95": _self_limiting(
        "A non-object JSON record breaks to permanent-corruption cleanup and removes this trace."
    ),
    f"{_SRD}::L100": _retries_after_input_changes(
        "The trace cannot be read, so recovery waits for filesystem accessibility to return."
    ),
    f"{_SRD}::L109": _self_limiting(
        "Permanent trace corruption deletes both trace and enrollment before another startup pass."
    ),
    f"{_SRD}::L121": _self_limiting(
        "An alien-command trace and its enrollment are deleted as a terminal safety disposition."
    ),
    f"{_SRF}::L139": _retries_after_input_changes(
        "A second stat failure keeps the trace retryable until the filesystem becomes available."
    ),
    f"{_SRF}::L179": _retries_after_input_changes(
        "Flush or output-index failure retains both files until output infrastructure recovers."
    ),
}
