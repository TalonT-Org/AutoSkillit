"""Shared golden backend-admission ledger for the shipped default skill catalog.

The ledger reads shipped SKILL.md and optional exploration.yaml files with
project_root=None and an explicit SkillVisibilitySpec(). It therefore excludes
project-local overrides, user configuration, and environment-dependent visibility.

The pinned combinations cover the current production list_effective call shapes:
ORCHESTRATOR/False in workspace/skill_projection.py, cli/session/_session_order.py,
cli/fleet/_fleet_run.py, cli/prompts/_prompts.py, server/tools/_serve_helpers.py, and
server/tools/tools_fleet_dispatch/_campaign_state.py; SESSION/False in
server/_factory.py; and SESSION/True in cli/session/_session_cook.py.
There is currently no FLEET call site. Recipe packs, recipe features, enabled packs,
and project-local variations are runtime inputs outside this baseline. The explicit
inventory does not automatically discover future production call shapes.
"""

from __future__ import annotations

from autoskillit.core import (
    MANAGED_JOIN_ATTESTATION_SCHEMA_VERSION,
    ManagedJoinAttestation,
    SemanticAdaptationContext,
    SkillExecutionRole,
    SkillVisibilitySpec,
)
from autoskillit.execution.backends import all_backends
from autoskillit.workspace import DefaultSkillResolver, compile_session_skill_catalog

CatalogCombination = tuple[SkillExecutionRole, bool]
AdmissionRows = dict[str, dict[str, str]]

COOK_SESSION_COMBINATION: CatalogCombination = (SkillExecutionRole.SESSION, True)
PINNED_COMBINATIONS: tuple[CatalogCombination, ...] = (
    (SkillExecutionRole.ORCHESTRATOR, False),
    (SkillExecutionRole.SESSION, False),
    COOK_SESSION_COMBINATION,
)

SKILL_ADMISSION_LEDGER: dict[CatalogCombination, AdmissionRows] = {
    (SkillExecutionRole.ORCHESTRATOR, False): {
        "process-issues": {"claude-code": "admitted", "codex": "admitted"},
        "sous-chef": {"claude-code": "admitted", "codex": "admitted"},
    },
    (SkillExecutionRole.SESSION, False): {
        "analyze-pipeline-health": {"claude-code": "admitted", "codex": "admitted"},
        "analyze-prs": {"claude-code": "admitted", "codex": "admitted"},
        "arch-lens-c4-container": {"claude-code": "admitted", "codex": "admitted"},
        "arch-lens-concurrency": {"claude-code": "admitted", "codex": "admitted"},
        "arch-lens-data-lineage": {"claude-code": "admitted", "codex": "admitted"},
        "arch-lens-deployment": {"claude-code": "admitted", "codex": "admitted"},
        "arch-lens-development": {"claude-code": "admitted", "codex": "admitted"},
        "arch-lens-error-resilience": {"claude-code": "admitted", "codex": "admitted"},
        "arch-lens-module-dependency": {"claude-code": "admitted", "codex": "admitted"},
        "arch-lens-operational": {"claude-code": "admitted", "codex": "admitted"},
        "arch-lens-process-flow": {"claude-code": "admitted", "codex": "admitted"},
        "arch-lens-repository-access": {"claude-code": "admitted", "codex": "admitted"},
        "arch-lens-scenarios": {"claude-code": "admitted", "codex": "admitted"},
        "arch-lens-security": {"claude-code": "admitted", "codex": "admitted"},
        "arch-lens-state-lifecycle": {"claude-code": "admitted", "codex": "admitted"},
        "audit-arch": {"claude-code": "admitted", "codex": "admitted"},
        "audit-bugs": {"claude-code": "admitted", "codex": "admitted"},
        "audit-cohesion": {"claude-code": "admitted", "codex": "admitted"},
        "audit-defense-standards": {"claude-code": "admitted", "codex": "admitted"},
        "audit-docs": {"claude-code": "admitted", "codex": "admitted"},
        "audit-friction": {"claude-code": "admitted", "codex": "admitted"},
        "audit-impl": {"claude-code": "admitted", "codex": "admitted"},
        "audit-review-decisions": {"claude-code": "admitted", "codex": "admitted"},
        "audit-tests": {"claude-code": "admitted", "codex": "admitted"},
        "build-execution-map": {"claude-code": "admitted", "codex": "admitted"},
        "bundle-local-report": {"claude-code": "admitted", "codex": "admitted"},
        "close-kitchen": {"claude-code": "admitted", "codex": "admitted"},
        "collapse-issues": {"claude-code": "admitted", "codex": "admitted"},
        "compose-pr": {"claude-code": "admitted", "codex": "admitted"},
        "design-guards": {"claude-code": "admitted", "codex": "admitted"},
        "diagnose-ci": {"claude-code": "admitted", "codex": "admitted"},
        "dry-walkthrough": {"claude-code": "admitted", "codex": "admitted"},
        "elaborate-phase": {"claude-code": "admitted", "codex": "admitted"},
        "implement-worktree": {"claude-code": "admitted", "codex": "admitted"},
        "implement-worktree-no-merge": {"claude-code": "admitted", "codex": "admitted"},
        "investigate": {"claude-code": "admitted", "codex": "admitted"},
        "issue-splitter": {"claude-code": "admitted", "codex": "admitted"},
        "make-arch-diag": {"claude-code": "admitted", "codex": "admitted"},
        "make-groups": {"claude-code": "admitted", "codex": "admitted"},
        "make-plan": {"claude-code": "admitted", "codex": "admitted"},
        "make-req": {"claude-code": "admitted", "codex": "admitted"},
        "merge-pr": {"claude-code": "admitted", "codex": "admitted"},
        "mermaid": {"claude-code": "admitted", "codex": "admitted"},
        "migrate-recipes": {"claude-code": "admitted", "codex": "admitted"},
        "open-integration-pr": {"claude-code": "admitted", "codex": "admitted"},
        "open-kitchen": {"claude-code": "admitted", "codex": "admitted"},
        "pipeline-summary": {"claude-code": "admitted", "codex": "admitted"},
        "prepare-issue": {"claude-code": "admitted", "codex": "admitted"},
        "prepare-pr": {"claude-code": "admitted", "codex": "admitted"},
        "promote-to-main": {"claude-code": "admitted", "codex": "admitted"},
        "rectify": {"claude-code": "admitted", "codex": "admitted"},
        "reload-session": {"claude-code": "admitted", "codex": "admitted"},
        "report-bug": {"claude-code": "admitted", "codex": "admitted"},
        "resolve-failures": {"claude-code": "admitted", "codex": "admitted"},
        "resolve-merge-conflicts": {"claude-code": "admitted", "codex": "admitted"},
        "resolve-review": {"claude-code": "admitted", "codex": "admitted"},
        "retry-worktree": {"claude-code": "admitted", "codex": "admitted"},
        "review-approach": {"claude-code": "admitted", "codex": "admitted"},
        "review-pr": {"claude-code": "admitted", "codex": "admitted"},
        "setup-project": {"claude-code": "admitted", "codex": "admitted"},
        "smoke-task": {"claude-code": "admitted", "codex": "admitted"},
        "triage-issues": {"claude-code": "admitted", "codex": "admitted"},
        "validate-audit": {"claude-code": "admitted", "codex": "admitted"},
        "validate-review-decisions": {"claude-code": "admitted", "codex": "admitted"},
        "validate-test-audit": {"claude-code": "admitted", "codex": "admitted"},
        "verify-diag": {"claude-code": "admitted", "codex": "admitted"},
        "write-recipe": {"claude-code": "admitted", "codex": "admitted"},
    },
    (SkillExecutionRole.SESSION, True): {
        "analyze-pipeline-health": {"claude-code": "admitted", "codex": "admitted"},
        "analyze-prs": {"claude-code": "admitted", "codex": "admitted"},
        "arch-lens-c4-container": {"claude-code": "admitted", "codex": "admitted"},
        "arch-lens-concurrency": {"claude-code": "admitted", "codex": "admitted"},
        "arch-lens-data-lineage": {"claude-code": "admitted", "codex": "admitted"},
        "arch-lens-deployment": {"claude-code": "admitted", "codex": "admitted"},
        "arch-lens-development": {"claude-code": "admitted", "codex": "admitted"},
        "arch-lens-error-resilience": {"claude-code": "admitted", "codex": "admitted"},
        "arch-lens-module-dependency": {"claude-code": "admitted", "codex": "admitted"},
        "arch-lens-operational": {"claude-code": "admitted", "codex": "admitted"},
        "arch-lens-process-flow": {"claude-code": "admitted", "codex": "admitted"},
        "arch-lens-repository-access": {"claude-code": "admitted", "codex": "admitted"},
        "arch-lens-scenarios": {"claude-code": "admitted", "codex": "admitted"},
        "arch-lens-security": {"claude-code": "admitted", "codex": "admitted"},
        "arch-lens-state-lifecycle": {"claude-code": "admitted", "codex": "admitted"},
        "audit-arch": {"claude-code": "admitted", "codex": "admitted"},
        "audit-bugs": {"claude-code": "admitted", "codex": "admitted"},
        "audit-cohesion": {"claude-code": "admitted", "codex": "admitted"},
        "audit-defense-standards": {"claude-code": "admitted", "codex": "admitted"},
        "audit-docs": {"claude-code": "admitted", "codex": "admitted"},
        "audit-friction": {"claude-code": "admitted", "codex": "admitted"},
        "audit-impl": {"claude-code": "admitted", "codex": "admitted"},
        "audit-review-decisions": {"claude-code": "admitted", "codex": "admitted"},
        "audit-tests": {"claude-code": "admitted", "codex": "admitted"},
        "build-execution-map": {"claude-code": "admitted", "codex": "admitted"},
        "bundle-local-report": {"claude-code": "admitted", "codex": "admitted"},
        "close-kitchen": {"claude-code": "admitted", "codex": "admitted"},
        "collapse-issues": {"claude-code": "admitted", "codex": "admitted"},
        "compose-pr": {"claude-code": "admitted", "codex": "admitted"},
        "design-guards": {"claude-code": "admitted", "codex": "admitted"},
        "diagnose-ci": {"claude-code": "admitted", "codex": "admitted"},
        "dry-walkthrough": {"claude-code": "admitted", "codex": "admitted"},
        "elaborate-phase": {"claude-code": "admitted", "codex": "admitted"},
        "implement-worktree": {"claude-code": "admitted", "codex": "admitted"},
        "implement-worktree-no-merge": {"claude-code": "admitted", "codex": "admitted"},
        "investigate": {"claude-code": "admitted", "codex": "admitted"},
        "issue-splitter": {"claude-code": "admitted", "codex": "admitted"},
        "make-arch-diag": {"claude-code": "admitted", "codex": "admitted"},
        "make-campaign": {"claude-code": "admitted", "codex": "admitted"},
        "make-groups": {"claude-code": "admitted", "codex": "admitted"},
        "make-plan": {"claude-code": "admitted", "codex": "admitted"},
        "make-req": {"claude-code": "admitted", "codex": "admitted"},
        "merge-pr": {"claude-code": "admitted", "codex": "admitted"},
        "mermaid": {"claude-code": "admitted", "codex": "admitted"},
        "migrate-recipes": {"claude-code": "admitted", "codex": "admitted"},
        "open-integration-pr": {"claude-code": "admitted", "codex": "admitted"},
        "open-kitchen": {"claude-code": "admitted", "codex": "admitted"},
        "pipeline-summary": {"claude-code": "admitted", "codex": "admitted"},
        "planner-analyze": {"claude-code": "admitted", "codex": "admitted"},
        "planner-assess-review-approach": {"claude-code": "admitted", "codex": "admitted"},
        "planner-consolidate-wps": {"claude-code": "admitted", "codex": "admitted"},
        "planner-elaborate-assignments": {"claude-code": "admitted", "codex": "admitted"},
        "planner-elaborate-phase": {"claude-code": "admitted", "codex": "admitted"},
        "planner-elaborate-wps": {"claude-code": "admitted", "codex": "admitted"},
        "planner-extract-domain": {"claude-code": "admitted", "codex": "admitted"},
        "planner-generate-phases": {"claude-code": "admitted", "codex": "admitted"},
        "planner-reconcile-deps": {"claude-code": "admitted", "codex": "admitted"},
        "planner-refine": {"claude-code": "admitted", "codex": "admitted"},
        "planner-refine-assignments": {"claude-code": "admitted", "codex": "admitted"},
        "planner-refine-phases": {"claude-code": "admitted", "codex": "admitted"},
        "planner-refine-wps": {"claude-code": "admitted", "codex": "admitted"},
        "planner-validate-task-alignment": {"claude-code": "admitted", "codex": "admitted"},
        "prepare-issue": {"claude-code": "admitted", "codex": "admitted"},
        "prepare-pr": {"claude-code": "admitted", "codex": "admitted"},
        "promote-to-main": {"claude-code": "admitted", "codex": "admitted"},
        "rectify": {"claude-code": "admitted", "codex": "admitted"},
        "reload-session": {"claude-code": "admitted", "codex": "admitted"},
        "report-bug": {"claude-code": "admitted", "codex": "admitted"},
        "resolve-failures": {"claude-code": "admitted", "codex": "admitted"},
        "resolve-merge-conflicts": {"claude-code": "admitted", "codex": "admitted"},
        "resolve-review": {"claude-code": "admitted", "codex": "admitted"},
        "retry-worktree": {"claude-code": "admitted", "codex": "admitted"},
        "review-approach": {"claude-code": "admitted", "codex": "admitted"},
        "review-pr": {"claude-code": "admitted", "codex": "admitted"},
        "setup-project": {"claude-code": "admitted", "codex": "admitted"},
        "smoke-task": {"claude-code": "admitted", "codex": "admitted"},
        "triage-issues": {"claude-code": "admitted", "codex": "admitted"},
        "validate-audit": {"claude-code": "admitted", "codex": "admitted"},
        "validate-review-decisions": {"claude-code": "admitted", "codex": "admitted"},
        "validate-test-audit": {"claude-code": "admitted", "codex": "admitted"},
        "verify-diag": {"claude-code": "admitted", "codex": "admitted"},
        "write-recipe": {"claude-code": "admitted", "codex": "admitted"},
    },
}


def _production_managed_codex_context() -> SemanticAdaptationContext:
    """Return the attested direct-mode shape used for managed Codex admission."""
    return SemanticAdaptationContext(
        managed_join_attestation=ManagedJoinAttestation(
            schema_version=MANAGED_JOIN_ATTESTATION_SCHEMA_VERSION,
            backend="codex",
            launch_context="direct",
            parent_session_id="managed-admission-ledger",
            activation_epoch=0,
            direct_tool_mode=True,
            resolved_model="gpt-5.6-sol",
            resolved_reasoning_effort="high",
            codex_catalog_digest="c" * 64,
            fixed_batch_tool_registry_digest="a" * 64,
            hook_registry_digest="b" * 64,
            skill_load_applies=True,
            guards_apply=True,
            provenance="autoskillit-server",
        )
    )


def _live_admission_rows(combination: CatalogCombination) -> AdmissionRows:
    role, cook_session = combination
    source_catalog = DefaultSkillResolver().list_effective(
        None,
        role,
        visibility=SkillVisibilitySpec(),
        cook_session=cook_session,
    )
    source_names = {skill.name for skill in source_catalog.skills}
    rows: AdmissionRows = {name: {} for name in source_names}

    managed_codex_context = _production_managed_codex_context()
    for backend in all_backends():
        compilation = compile_session_skill_catalog(
            source_catalog,
            backend,
            adaptation_context=(managed_codex_context if backend.name == "codex" else None),
        )
        admitted = {skill.name: "admitted" for skill in compilation.catalog.skills}
        unavailable = {item.skill: item.operation.value for item in compilation.unavailable}

        assert admitted.keys().isdisjoint(unavailable)
        assert admitted.keys() | unavailable.keys() == source_names
        for partition in (admitted, unavailable):
            for skill_name, status in partition.items():
                rows[skill_name][backend.name] = status

    return {
        skill_name: {
            backend_name: rows[skill_name][backend_name]
            for backend_name in sorted(rows[skill_name])
        }
        for skill_name in sorted(rows)
    }
