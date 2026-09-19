# skills_extended/

IL-2 skills layer — extended catalog of 138 bundled skills covering the
arch-lens, exp-lens, vis-lens, audit, planner, and diagnostic workflows.
Of the 138, only `reload-session` carries `disable-model-invocation: true`
and is NOT slash-command-invocable (it is a recovery action invoked via
its MCP tool, not a slash command); the remaining 137 are slash-command
invocable as `/autoskillit:<skill-name>`. The canonical accounting lives
in `docs/skills/catalog.md` (asserted by `tests/docs/test_doc_counts.py`
and `test_doc_counts.py:183-186`'s `_count_skills_total`).

This is the bulk skill catalog. The always-loaded kernel skills
(`open-kitchen`, `sous-chef`, `close-kitchen`) live one directory up in
`skills/` and are documented in `skills/AGENTS.md`.

## Architecture Notes

Skills here cover interactive orchestrator workflows (most) and
headless-orchestrator workflows (a few, e.g. `codex-impl-loop`,
`smoke-task`). Skills are grouped for discovery via the `categories:`
frontmatter field; canonical categories include `arch-lens`,
`exp-lens`, `vis-lens`, `audit`, `planner`, `recipe`, `fleet`, `ci`,
`diagnostic`, and `research`.

**Materialization path:** at session start, `workspace/session_skills/`
copies a relevant subset of these skills into the active workspace so
they can be invoked as slash commands (`/autoskillit:<skill-name>`).
The full set is never copied — the session-skill catalog filters by
relevance to the orchestrator mode. See `workspace/AGENTS.md` (the
canonical materialization contract) and `workspace/session_skills/`
(the implementation) for details.

**Frontmatter fields** (all optional; coverage is uneven across the
catalog — see issue #3773 F-C1-11 for the known gaps, currently
out-of-scope for this plan):

- `categories:` — discovery grouping; one or more of the canonical
  categories above. **Recommended for slash-command-invocable skills**
  (i.e. everything except `reload-session`, which is invoked via its MCP
  tool, not a slash command).
- `hooks:` — PreToolUse/PostToolUse announcements emitted when the
  skill runs. Pure observability; absence means the skill runs
  silently.
- `uses_capabilities:` — declared MCP capabilities (`run_skill`,
  `test_check`, `open_kitchen`, `github_api_write`, etc.). Drives
  capability-gated dispatch and audit trails.

## Multi-concern files

Files with no registration support their folder; the 138 immediate-child
SKILL.md files each define one skill and are enumerated individually
below by tier:

- Tier-2 (arch-lens) — 13 skills: `arch-lens-c4-container`,
  `arch-lens-concurrency`, `arch-lens-data-lineage`, `arch-lens-deployment`,
  `arch-lens-development`, `arch-lens-error-resilience`,
  `arch-lens-module-dependency`, `arch-lens-operational`,
  `arch-lens-process-flow`, `arch-lens-repository-access`,
  `arch-lens-scenarios`, `arch-lens-security`,
  `arch-lens-state-lifecycle`.
- Tier-2 (exp-lens) — 18 skills: `exp-lens-randomization-blocking`,
  `exp-lens-iterative-learning`, `exp-lens-estimand-clarity`,
  `exp-lens-error-budget`, `exp-lens-governance-risk`,
  `exp-lens-reproducibility-artifacts`,
  `exp-lens-exploratory-confirmatory`, …
- Tier-2 (vis-lens) — 12 skills: `vis-lens-methodology-norms`,
  `vis-lens-figure-table`, `vis-lens-story-arc`, …
- Tier-2 (audit) — `audit-arch`, `audit-tests`, `audit-cohesion`,
  `audit-bugs`, `audit-docs`, `audit-feature-gates`, `audit-friction`,
  `audit-defense-standards`, `audit-review-decisions`, `audit-claims`.
- Tier-2 (planner) — `planner-analyze`, `planner-generate-phases`,
  `planner-elaborate-phase`, `planner-elaborate-wps`,
  `planner-elaborate-assignments`, `planner-refine`,
  `planner-refine-phases`, `planner-refine-wps`,
  `planner-refine-assignments`, `planner-reconcile-deps`,
  `planner-validate-task-alignment`, `planner-assess-review-approach`,
  `planner-consolidate-wps`, `planner-extract-domain`.
- Tier-3 — `make-plan`, `make-groups`, `make-req`, `make-scenarios`,
  `implement-worktree`, `implement-worktree-no-merge`, `diagnose-ci`,
  `compose-pr`, `prepare-pr`, `merge-pr`, `review-pr`, … (remaining 50+
  skills, see directory listing).

> **Note:** the per-tier counts above are approximate and may drift as
> skills are added or re-tiered; the authoritative count is asserted
> by `tests/docs/test_doc_counts.py`. For the canonical visibility
> matrix see `docs/skills/visibility.md`.
