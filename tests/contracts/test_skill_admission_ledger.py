"""Golden backend-admission ledger for the shipped default skill catalog.

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

import pytest

from autoskillit.core import SkillExecutionRole, SkillVisibilitySpec
from autoskillit.execution.backends import BACKEND_REGISTRY, all_backends
from autoskillit.workspace import DefaultSkillResolver, compile_session_skill_catalog

pytestmark = pytest.mark.medium

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
        "process-issues": {"claude-code": "admitted", "codex": "required_join"},
        "sous-chef": {"claude-code": "admitted", "codex": "admitted"},
    },
    (SkillExecutionRole.SESSION, False): {
        "analyze-pipeline-health": {"claude-code": "admitted", "codex": "required_join"},
        "analyze-prs": {"claude-code": "admitted", "codex": "required_join"},
        "arch-lens-c4-container": {"claude-code": "admitted", "codex": "required_join"},
        "arch-lens-concurrency": {"claude-code": "admitted", "codex": "required_join"},
        "arch-lens-data-lineage": {"claude-code": "admitted", "codex": "required_join"},
        "arch-lens-deployment": {"claude-code": "admitted", "codex": "required_join"},
        "arch-lens-development": {"claude-code": "admitted", "codex": "required_join"},
        "arch-lens-error-resilience": {"claude-code": "admitted", "codex": "required_join"},
        "arch-lens-module-dependency": {"claude-code": "admitted", "codex": "required_join"},
        "arch-lens-operational": {"claude-code": "admitted", "codex": "required_join"},
        "arch-lens-process-flow": {"claude-code": "admitted", "codex": "required_join"},
        "arch-lens-repository-access": {"claude-code": "admitted", "codex": "required_join"},
        "arch-lens-scenarios": {"claude-code": "admitted", "codex": "required_join"},
        "arch-lens-security": {"claude-code": "admitted", "codex": "required_join"},
        "arch-lens-state-lifecycle": {"claude-code": "admitted", "codex": "required_join"},
        "audit-arch": {"claude-code": "admitted", "codex": "required_join"},
        "audit-bugs": {"claude-code": "admitted", "codex": "required_join"},
        "audit-cohesion": {"claude-code": "admitted", "codex": "required_join"},
        "audit-defense-standards": {"claude-code": "admitted", "codex": "required_join"},
        "audit-docs": {"claude-code": "admitted", "codex": "required_join"},
        "audit-friction": {"claude-code": "admitted", "codex": "required_join"},
        "audit-impl": {"claude-code": "admitted", "codex": "required_join"},
        "audit-review-decisions": {"claude-code": "admitted", "codex": "required_join"},
        "audit-tests": {"claude-code": "admitted", "codex": "required_join"},
        "build-execution-map": {"claude-code": "admitted", "codex": "required_join"},
        "bundle-local-report": {"claude-code": "admitted", "codex": "admitted"},
        "close-kitchen": {"claude-code": "admitted", "codex": "admitted"},
        "collapse-issues": {"claude-code": "admitted", "codex": "admitted"},
        "compose-pr": {"claude-code": "admitted", "codex": "required_join"},
        "design-guards": {"claude-code": "admitted", "codex": "required_join"},
        "diagnose-ci": {"claude-code": "admitted", "codex": "admitted"},
        "dry-walkthrough": {"claude-code": "admitted", "codex": "required_join"},
        "elaborate-phase": {"claude-code": "admitted", "codex": "required_join"},
        "implement-worktree": {"claude-code": "admitted", "codex": "required_join"},
        "implement-worktree-no-merge": {"claude-code": "admitted", "codex": "required_join"},
        "investigate": {"claude-code": "admitted", "codex": "required_join"},
        "issue-splitter": {"claude-code": "admitted", "codex": "admitted"},
        "make-arch-diag": {"claude-code": "admitted", "codex": "admitted"},
        "make-groups": {"claude-code": "admitted", "codex": "required_join"},
        "make-plan": {"claude-code": "admitted", "codex": "required_join"},
        "make-req": {"claude-code": "admitted", "codex": "required_join"},
        "merge-pr": {"claude-code": "admitted", "codex": "required_join"},
        "mermaid": {"claude-code": "admitted", "codex": "admitted"},
        "migrate-recipes": {"claude-code": "admitted", "codex": "admitted"},
        "open-integration-pr": {"claude-code": "admitted", "codex": "required_join"},
        "open-kitchen": {"claude-code": "admitted", "codex": "admitted"},
        "pipeline-summary": {"claude-code": "admitted", "codex": "admitted"},
        "prepare-issue": {"claude-code": "admitted", "codex": "admitted"},
        "prepare-pr": {"claude-code": "admitted", "codex": "required_join"},
        "promote-to-main": {"claude-code": "admitted", "codex": "required_join"},
        "rectify": {"claude-code": "admitted", "codex": "required_join"},
        "reload-session": {"claude-code": "admitted", "codex": "admitted"},
        "report-bug": {"claude-code": "admitted", "codex": "admitted"},
        "resolve-failures": {"claude-code": "admitted", "codex": "admitted"},
        "resolve-merge-conflicts": {"claude-code": "admitted", "codex": "admitted"},
        "resolve-review": {"claude-code": "admitted", "codex": "required_join"},
        "retry-worktree": {"claude-code": "admitted", "codex": "required_join"},
        "review-approach": {"claude-code": "admitted", "codex": "required_join"},
        "review-pr": {"claude-code": "admitted", "codex": "required_join"},
        "setup-project": {"claude-code": "admitted", "codex": "required_join"},
        "smoke-task": {"claude-code": "admitted", "codex": "admitted"},
        "triage-issues": {"claude-code": "admitted", "codex": "required_join"},
        "validate-audit": {"claude-code": "admitted", "codex": "required_join"},
        "validate-review-decisions": {"claude-code": "admitted", "codex": "required_join"},
        "validate-test-audit": {"claude-code": "admitted", "codex": "required_join"},
        "verify-diag": {"claude-code": "admitted", "codex": "required_join"},
        "write-recipe": {"claude-code": "admitted", "codex": "admitted"},
    },
    (SkillExecutionRole.SESSION, True): {
        "analyze-pipeline-health": {"claude-code": "admitted", "codex": "required_join"},
        "analyze-prs": {"claude-code": "admitted", "codex": "required_join"},
        "arch-lens-c4-container": {"claude-code": "admitted", "codex": "required_join"},
        "arch-lens-concurrency": {"claude-code": "admitted", "codex": "required_join"},
        "arch-lens-data-lineage": {"claude-code": "admitted", "codex": "required_join"},
        "arch-lens-deployment": {"claude-code": "admitted", "codex": "required_join"},
        "arch-lens-development": {"claude-code": "admitted", "codex": "required_join"},
        "arch-lens-error-resilience": {"claude-code": "admitted", "codex": "required_join"},
        "arch-lens-module-dependency": {"claude-code": "admitted", "codex": "required_join"},
        "arch-lens-operational": {"claude-code": "admitted", "codex": "required_join"},
        "arch-lens-process-flow": {"claude-code": "admitted", "codex": "required_join"},
        "arch-lens-repository-access": {"claude-code": "admitted", "codex": "required_join"},
        "arch-lens-scenarios": {"claude-code": "admitted", "codex": "required_join"},
        "arch-lens-security": {"claude-code": "admitted", "codex": "required_join"},
        "arch-lens-state-lifecycle": {"claude-code": "admitted", "codex": "required_join"},
        "audit-arch": {"claude-code": "admitted", "codex": "required_join"},
        "audit-bugs": {"claude-code": "admitted", "codex": "required_join"},
        "audit-cohesion": {"claude-code": "admitted", "codex": "required_join"},
        "audit-defense-standards": {"claude-code": "admitted", "codex": "required_join"},
        "audit-docs": {"claude-code": "admitted", "codex": "required_join"},
        "audit-friction": {"claude-code": "admitted", "codex": "required_join"},
        "audit-impl": {"claude-code": "admitted", "codex": "required_join"},
        "audit-review-decisions": {"claude-code": "admitted", "codex": "required_join"},
        "audit-tests": {"claude-code": "admitted", "codex": "required_join"},
        "build-execution-map": {"claude-code": "admitted", "codex": "required_join"},
        "bundle-local-report": {"claude-code": "admitted", "codex": "admitted"},
        "close-kitchen": {"claude-code": "admitted", "codex": "admitted"},
        "collapse-issues": {"claude-code": "admitted", "codex": "admitted"},
        "compose-pr": {"claude-code": "admitted", "codex": "required_join"},
        "design-guards": {"claude-code": "admitted", "codex": "required_join"},
        "diagnose-ci": {"claude-code": "admitted", "codex": "admitted"},
        "dry-walkthrough": {"claude-code": "admitted", "codex": "required_join"},
        "elaborate-phase": {"claude-code": "admitted", "codex": "required_join"},
        "implement-worktree": {"claude-code": "admitted", "codex": "required_join"},
        "implement-worktree-no-merge": {"claude-code": "admitted", "codex": "required_join"},
        "investigate": {"claude-code": "admitted", "codex": "required_join"},
        "issue-splitter": {"claude-code": "admitted", "codex": "admitted"},
        "make-arch-diag": {"claude-code": "admitted", "codex": "admitted"},
        "make-campaign": {"claude-code": "admitted", "codex": "admitted"},
        "make-groups": {"claude-code": "admitted", "codex": "required_join"},
        "make-plan": {"claude-code": "admitted", "codex": "required_join"},
        "make-req": {"claude-code": "admitted", "codex": "required_join"},
        "merge-pr": {"claude-code": "admitted", "codex": "required_join"},
        "mermaid": {"claude-code": "admitted", "codex": "admitted"},
        "migrate-recipes": {"claude-code": "admitted", "codex": "admitted"},
        "open-integration-pr": {"claude-code": "admitted", "codex": "required_join"},
        "open-kitchen": {"claude-code": "admitted", "codex": "admitted"},
        "pipeline-summary": {"claude-code": "admitted", "codex": "admitted"},
        "planner-analyze": {"claude-code": "admitted", "codex": "required_join"},
        "planner-assess-review-approach": {"claude-code": "admitted", "codex": "required_join"},
        "planner-consolidate-wps": {"claude-code": "admitted", "codex": "required_join"},
        "planner-elaborate-assignments": {"claude-code": "admitted", "codex": "required_join"},
        "planner-elaborate-phase": {"claude-code": "admitted", "codex": "required_join"},
        "planner-elaborate-wps": {"claude-code": "admitted", "codex": "required_join"},
        "planner-extract-domain": {"claude-code": "admitted", "codex": "required_join"},
        "planner-generate-phases": {"claude-code": "admitted", "codex": "admitted"},
        "planner-reconcile-deps": {"claude-code": "admitted", "codex": "admitted"},
        "planner-refine": {"claude-code": "admitted", "codex": "required_join"},
        "planner-refine-assignments": {"claude-code": "admitted", "codex": "required_join"},
        "planner-refine-phases": {"claude-code": "admitted", "codex": "required_join"},
        "planner-refine-wps": {"claude-code": "admitted", "codex": "required_join"},
        "planner-validate-task-alignment": {"claude-code": "admitted", "codex": "required_join"},
        "prepare-issue": {"claude-code": "admitted", "codex": "admitted"},
        "prepare-pr": {"claude-code": "admitted", "codex": "required_join"},
        "promote-to-main": {"claude-code": "admitted", "codex": "required_join"},
        "rectify": {"claude-code": "admitted", "codex": "required_join"},
        "reload-session": {"claude-code": "admitted", "codex": "admitted"},
        "report-bug": {"claude-code": "admitted", "codex": "admitted"},
        "resolve-failures": {"claude-code": "admitted", "codex": "admitted"},
        "resolve-merge-conflicts": {"claude-code": "admitted", "codex": "admitted"},
        "resolve-review": {"claude-code": "admitted", "codex": "required_join"},
        "retry-worktree": {"claude-code": "admitted", "codex": "required_join"},
        "review-approach": {"claude-code": "admitted", "codex": "required_join"},
        "review-pr": {"claude-code": "admitted", "codex": "required_join"},
        "setup-project": {"claude-code": "admitted", "codex": "required_join"},
        "smoke-task": {"claude-code": "admitted", "codex": "admitted"},
        "triage-issues": {"claude-code": "admitted", "codex": "required_join"},
        "validate-audit": {"claude-code": "admitted", "codex": "required_join"},
        "validate-review-decisions": {"claude-code": "admitted", "codex": "required_join"},
        "validate-test-audit": {"claude-code": "admitted", "codex": "required_join"},
        "verify-diag": {"claude-code": "admitted", "codex": "required_join"},
        "write-recipe": {"claude-code": "admitted", "codex": "admitted"},
    },
}


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

    for backend in all_backends():
        compilation = compile_session_skill_catalog(source_catalog, backend)
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


_COMBINATION_IDS = tuple(
    f"{role.value}-cook-{str(cook_session).lower()}" for role, cook_session in PINNED_COMBINATIONS
)


@pytest.mark.parametrize(
    "combination",
    PINNED_COMBINATIONS,
    ids=_COMBINATION_IDS,
)
def test_no_silent_admission_additions(combination: CatalogCombination) -> None:
    live = _live_admission_rows(combination)
    golden = SKILL_ADMISSION_LEDGER[combination]
    additions = {
        skill_name: live[skill_name] for skill_name in sorted(live.keys() - golden.keys())
    }
    assert not additions, f"skills missing from SKILL_ADMISSION_LEDGER: {additions!r}"


@pytest.mark.parametrize(
    "combination",
    PINNED_COMBINATIONS,
    ids=_COMBINATION_IDS,
)
def test_no_silent_admission_removals(combination: CatalogCombination) -> None:
    live = _live_admission_rows(combination)
    golden = SKILL_ADMISSION_LEDGER[combination]
    removals = sorted(golden.keys() - live.keys())
    assert not removals, f"stale skills in SKILL_ADMISSION_LEDGER: {removals!r}"


@pytest.mark.parametrize(
    "combination",
    PINNED_COMBINATIONS,
    ids=_COMBINATION_IDS,
)
def test_no_silent_status_changes(combination: CatalogCombination) -> None:
    live = _live_admission_rows(combination)
    golden = SKILL_ADMISSION_LEDGER[combination]
    changes = sorted(
        (
            skill_name,
            backend_name,
            golden[skill_name][backend_name],
            live[skill_name][backend_name],
        )
        for skill_name in live.keys() & golden.keys()
        for backend_name in live[skill_name].keys() & golden[skill_name].keys()
        if live[skill_name][backend_name] != golden[skill_name][backend_name]
    )
    assert not changes, (
        "backend admission status changed without a matching "
        f"SKILL_ADMISSION_LEDGER edit: {changes!r}"
    )


def test_ledger_dimensions_match_registry_and_pinned_combinations() -> None:
    assert tuple(SKILL_ADMISSION_LEDGER) == PINNED_COMBINATIONS
    expected_backends = tuple(sorted(BACKEND_REGISTRY))
    for rows in SKILL_ADMISSION_LEDGER.values():
        for backend_statuses in rows.values():
            assert tuple(backend_statuses) == expected_backends


def test_ledger_is_sorted() -> None:
    assert tuple(SKILL_ADMISSION_LEDGER) == PINNED_COMBINATIONS
    for rows in SKILL_ADMISSION_LEDGER.values():
        assert tuple(rows) == tuple(sorted(rows))
        for backend_statuses in rows.values():
            assert tuple(backend_statuses) == tuple(sorted(backend_statuses))
