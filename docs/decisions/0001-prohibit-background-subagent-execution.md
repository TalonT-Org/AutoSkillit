# ADR-0001: Prohibit Background Subagent Execution in All Skills

**Status:** Accepted
**Date:** 2026-05-01
**Issue:** [#1582](https://github.com/TalonT-Org/AutoSkillit/issues/1582)

## Context

Skills that spawn subagents via the Agent/Task tool have no universal rule governing
whether those subagents run in the foreground (blocking) or background (non-blocking).
When left to LLM discretion, background execution is occasionally chosen and produces
silent failures:

- **Lost results**: Background agents complete after the parent has moved on; findings
  are never incorporated into skill output.
- **Race conditions**: The parent acts on incomplete information while background agents
  are still gathering evidence.
- **Unobservable failures**: A failed background agent goes unnoticed — the parent is
  not blocked waiting for it.
- **Synthesis responsibility violation**: Synthesis requires all results to be available
  simultaneously. Background execution makes this impossible.

## Decision

**All skills that instruct sessions to spawn subagents MUST include the explicit rule in
their NEVER block:**

> Run subagents in the background (`run_in_background: true` is prohibited)

This applies to all SKILL.md files in `skills/` and `skills_extended/` that contain
Agent/Task tool spawning instructions.

This rule does NOT prohibit parallel execution. Multiple foreground subagents launched
in a single message execute concurrently — the parent blocks until all complete, then
synthesizes their combined results.

## Rationale

The foreground-parallel pattern (multiple Agent calls in one message, no
`run_in_background`) provides all the throughput benefits of parallelism while
guaranteeing the parent has complete information before synthesis. There is no valid
use case for background subagents in skill execution — if results are not needed, the
subagent should not be spawned at all.

## Enforcement

The compliance test `test_no_background_subagent_in_spawning_skills` in
`tests/skills/test_skill_compliance.py` automatically detects skills with spawn
indicators and asserts the prohibition string is present. This test runs as part of
`task test-all` and `task test-check`.

## Scope

Applies to 54 SKILL.md files. Listed by group:

**Core spawning skills (23):** implement-worktree, implement-worktree-no-merge,
investigate, make-plan, make-groups, validate-audit, scope, rectify, process-issues,
dry-walkthrough, generate-report, implement-experiment, plan-experiment,
plan-visualization, run-experiment, review-approach, review-design, setup-project,
triage-issues, audit-claims, review-pr, review-research-pr, retry-worktree

**Planner L1+L0 skills (8):** planner-analyze, planner-extract-domain,
planner-elaborate-wps, planner-elaborate-assignments, planner-refine-phases,
planner-refine-assignments, planner-refine-wps, planner-elaborate-phase

**Resolve skills (3):** resolve-review, resolve-claims-review, resolve-research-review

**Audit/design skills (7):** audit-arch, audit-cohesion, audit-defense-standards,
design-guards, audit-tests, audit-bugs, stage-data

**Arch-lens skills (13):** arch-lens-state-lifecycle, arch-lens-c4-container,
arch-lens-concurrency, arch-lens-data-lineage, arch-lens-deployment,
arch-lens-development, arch-lens-error-resilience, arch-lens-module-dependency,
arch-lens-operational, arch-lens-process-flow, arch-lens-repository-access,
arch-lens-scenarios, arch-lens-security

**Exp-lens skills (18):** exp-lens-fair-comparison, exp-lens-unit-interference,
exp-lens-causal-assumptions, exp-lens-benchmark-representativeness,
exp-lens-comparator-construction, exp-lens-error-budget, exp-lens-estimand-clarity,
exp-lens-exploratory-confirmatory, exp-lens-governance-risk, exp-lens-iterative-learning,
exp-lens-measurement-validity, exp-lens-pipeline-integrity,
exp-lens-randomization-blocking, exp-lens-reproducibility-artifacts,
exp-lens-sensitivity-robustness, exp-lens-severity-testing, exp-lens-validity-threats,
exp-lens-variance-stability

**Other (5):** analyze-prs, build-execution-map, verify-diag, audit-impl, elaborate-phase
