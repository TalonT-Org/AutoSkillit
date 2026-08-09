---
name: audit-docs
categories:
- audit
description: 'Audit documentation for drift, staleness, and inconsistency against the actual codebase. Use when user says
  "audit docs", "check documentation", "docs audit", or "documentation review". Spawns parallel subagents to explore codebase
  subsystems, then cross-references all documentation sources against findings.

  '
tier: 2
hooks:
  PreToolUse:
  - matcher: '*'
    hooks:
    - type: command
      command: 'echo ''[SKILL: audit-docs] Auditing documentation for staleness and drift...'''
      once: true
---

# Documentation Audit Skill

Audit all documentation sources for drift, staleness, and inconsistency against actual codebase behavior.

## When to Use

- User says "audit docs", "check documentation", "docs audit", "documentation review", "docs drift", "stale docs"

## Critical Constraints

**NEVER:**
- Fabricate, invent, or embellish information not supported by the available evidence or code.

- Modify any source files
- Update an existing report — always generate a new one
- Compare doc-to-doc without first grounding claims in actual code behavior
- Detach child delegations instead of joining them (joining every child is required)
- Start independent child delegations sequentially

**ALWAYS:**
- Ground every cross-reference finding in what the code actually does (not what other docs say)
- Start all independent child delegations before awaiting any result to maximize concurrency
- Dispatch all ready deterministic exploration vectors in one wave and join every leaf
- Write report to `{{AUTOSKILLIT_TEMP}}/audit-docs/docs_audit_{YYYY-MM-DD_HHMMSS}.md`
- Provide file:line references for every finding
- Categorize findings by severity (CRITICAL, HIGH, MEDIUM, LOW)
- Produce a usable report even when one or more subagents fail

---

## Documentation Sources

Enumerate and audit all of the following:

- **`AGENTS.md`** — shared project instructions, architecture tree, file path references, tool/skill counts, layer descriptions
- **Physical `CLAUDE.md`** — Claude-only overlay (skill invocation handling, CLAUDE.md modification policy, Pyright LSP guidance, subagent env vars); inherits shared content via `@AGENTS.md`
- **`docs/architecture/**/*.md`** — component names, module paths, layer assignments
- **`docs/requirements/**/*.md`** and **`docs/specs/**/*.md`** — API surface, behavioral contracts
- **All `README.md` files** at any depth in the repository
- **Module/class/function docstrings** in Python files under `src/`
- **Recipe YAML `description`, `summary`, and `note` fields** in `.autoskillit/recipes/` and `src/autoskillit/recipes/`

---

## Inconsistency Categories

Flag findings in these categories (maps to REQ-SKILL-004):

- **Stale claims** — doc asserts X, actual code behavior is Y
- **Orphaned references** — doc mentions a file, module, tool, class, or skill that no longer exists
- **Missing docs** — new subsystem or module with no mention in `AGENTS.md` tree or arch docs
- **Path/name drift** — hyphen-vs-underscore mismatches, renamed symbols, old module paths
- **Count mismatches** — `AGENTS.md` states N tools/skills/hooks but actual count differs
- **Inter-doc contradictions** — `AGENTS.md` says A, arch doc says B about the same entity

---

## Audit Workflow

1. **Pre-flight**: Verify `{{AUTOSKILLIT_TEMP}}/audit-docs/` directory exists; create it if not.

2. **Familiarization wave (SINGLE MESSAGE)** — dispatch the six ready evidence-only vectors below through the deterministic exploration router in one wave. Leaves collect facts only. If any leaf fails, record the gap and continue.

   **Start ALL independent child delegations before awaiting any result — one per item — and join every child before synthesis.**

   Do not output any prose between subagent dispatches. Immediately proceed to the next tool call.

<!-- autoskillit:exploration-vector id="familiarize-core-config" -->
   **Objective:** Establish what `src/autoskillit/core/` and `config/` expose and do. **Entry point:** their package gateways and registries. **Tool/source guidance:** trace definitions, imports, calls, and direct consumers; cite `file:line` evidence. **Scope boundary:** facts only, no documentation judgment or mutation. **Ignore list:** tests, generated files, temp artifacts, and unrelated packages. **Expected typed output:** `Verdict: answered | partial | blocked`, evidence records with symbol, behavior, and `file:line`, plus coverage gaps.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="familiarize-execution-workspace" -->
   **Objective:** Establish runtime orchestration and workspace lifecycle behavior in `execution/` and `workspace/`. **Entry point:** package gateways and backend/session/worktree entry paths. **Tool/source guidance:** trace imports, calls, references, and affected consumers with `file:line` evidence. **Scope boundary:** facts only; no severity, consolidation, or mutation. **Ignore list:** tests, logs, generated files, and unrelated packages. **Expected typed output:** `Verdict: answered | partial | blocked`, evidence records, and coverage gaps.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="familiarize-recipe-migration" -->
   **Objective:** Establish recipe schema, validation, rules, and migration behavior. **Entry point:** `recipe/` and `migration/` package gateways. **Tool/source guidance:** trace definitions, imports, calls, and consumers with `file:line` evidence. **Scope boundary:** facts only; no documentation comparison or mutation. **Ignore list:** tests, generated recipe artifacts, temp files, and unrelated packages. **Expected typed output:** `Verdict: answered | partial | blocked`, evidence records, and coverage gaps.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="familiarize-server" -->
   **Objective:** Establish the MCP tool surface, visibility gates, lifespan, and factory wiring in `server/`. **Entry point:** server factory, state, and tool registration modules. **Tool/source guidance:** trace declarations, definitions, imports, calls, and consumers with `file:line` evidence. **Scope boundary:** facts only; no severity or mutation. **Ignore list:** tests, generated schemas, temp files, and non-server implementation detail. **Expected typed output:** `Verdict: answered | partial | blocked`, evidence records, and coverage gaps.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="familiarize-cli-hooks" -->
   **Objective:** Establish CLI commands and hook-script behavior. **Entry point:** CLI registration and the hook registry. **Tool/source guidance:** trace definitions, calls, references, and affected consumers with `file:line` evidence. **Scope boundary:** facts only; no documentation judgment or mutation. **Ignore list:** tests, generated files, temp artifacts, and unrelated packages. **Expected typed output:** `Verdict: answered | partial | blocked`, evidence records, and coverage gaps.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="familiarize-skills" -->
   **Objective:** Establish the bundled `skills/` and `skills_extended/` catalog, categories, tiers, and consumers. **Entry point:** skill directories and catalog loaders. **Tool/source guidance:** trace declarations, references, and affected consumers with `file:line` evidence. **Scope boundary:** facts only; no cross-document judgment or mutation. **Ignore list:** tests, temp projections, generated files, and project-local skills. **Expected typed output:** `Verdict: answered | partial | blocked`, evidence records, and coverage gaps.
<!-- /autoskillit:exploration-vector -->

3. **Doc inventory** — enumerate all documentation sources (list files found under each source category above).

4. **Cross-reference wave** — after the parent assembles the documentation inventory, dispatch the four ready evidence-only vectors below through the deterministic router in one wave. Each leaf checks one domain against code-grounded familiarization evidence:

<!-- autoskillit:exploration-vector id="crossref-agent-guides" -->
   **Objective:** Collect code-grounded evidence for claims in `AGENTS.md` and the physical `CLAUDE.md` overlay. **Entry point:** those guide files and the symbols/paths they name. **Tool/source guidance:** trace references and affected consumers; return both claim and actual `file:line` evidence. **Scope boundary:** evidence only; the parent decides absence, severity, and findings. **Ignore list:** tests, generated files, temp artifacts, and stylistic wording differences. **Expected typed output:** `Verdict: answered | partial | blocked`, claim/evidence associations, and coverage gaps.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="crossref-architecture" -->
   **Objective:** Collect code-grounded evidence for component, module-path, and layer claims in `docs/architecture/**`. **Entry point:** architecture documents and named package gateways. **Tool/source guidance:** trace imports, references, and affected consumers with paired `file:line` evidence. **Scope boundary:** evidence only; no consolidation, severity, or mutation. **Ignore list:** tests, generated files, temp artifacts, and prose-only differences. **Expected typed output:** `Verdict: answered | partial | blocked`, claim/evidence associations, and coverage gaps.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="crossref-requirements-specs" -->
   **Objective:** Collect code-grounded evidence for API and behavior claims in `docs/requirements/**` and `docs/specs/**`. **Entry point:** requirement/specification identifiers and their named definitions. **Tool/source guidance:** trace definitions, references, and affected consumers with paired `file:line` evidence. **Scope boundary:** evidence only; the parent decides contradictions and severity. **Ignore list:** tests, generated files, temp artifacts, and requirements without an implemented scope. **Expected typed output:** `Verdict: answered | partial | blocked`, claim/evidence associations, and coverage gaps.
<!-- /autoskillit:exploration-vector -->

<!-- autoskillit:exploration-vector id="crossref-recipes-docstrings" -->
   **Objective:** Collect code-grounded evidence for recipe YAML descriptions and Python docstrings. **Entry point:** recipe `description`, `summary`, and `note` fields plus public module/class/function docstrings under `src/`. **Tool/source guidance:** trace declarations, definitions, references, and affected consumers with paired `file:line` evidence. **Scope boundary:** evidence only; no deduplication, severity, report writing, or mutation. **Ignore list:** tests, generated recipe artifacts, temp files, and comments that are not docstrings. **Expected typed output:** `Verdict: answered | partial | blocked`, claim/evidence associations, and coverage gaps.
<!-- /autoskillit:exploration-vector -->

5. **Consolidate (parent only)** — assemble the doc inventory, run secondary absence checks against collector completeness, merge all leaf evidence, deduplicate by file:line, and assign severity. Preserve conflicts and every `partial` or `blocked` coverage gap.

6. **Self-validation pass (parent only)** — for every CRITICAL or HIGH finding, re-read the cited file line to confirm the claim; downgrade or remove if not confirmed.

7. **Write report (parent only)** to `{{AUTOSKILLIT_TEMP}}/audit-docs/docs_audit_{YYYY-MM-DD_HHMMSS}.md` (relative to the current working directory) using the format below. Every mutation remains in the parent.

8. **Output summary** — print finding counts by severity to terminal.

---

## Report Format

```
# Documentation Audit Report — {YYYY-MM-DD HH:MM}

## Summary
| Severity | Count |
|----------|-------|
| CRITICAL | N     |
| HIGH     | N     |
| MEDIUM   | N     |
| LOW      | N     |

## Findings

### CRITICAL

#### [DOC-001] {Title}
- **File:** `path/to/doc.md:42`
- **Claim:** "..."
- **Actual:** "..."
- **Fix:** ...

...

## Coverage Gaps
{If any familiarization subagent failed, list affected subsystems here}
```

---

## Exclusions

Do NOT flag:
- Test files (`tests/`)
- Generated files (`{{AUTOSKILLIT_TEMP}}/`, `uv.lock`, `*.pyc`)
- Comment-only files or changelog entries
- External tool output or CI logs
- Doc-to-doc wording differences that don't contradict each other factually

---

## Severity Guidelines

**CRITICAL:**
- Orphaned references to deleted modules/tools
- `AGENTS.md` architecture tree listing a path that doesn't exist

**HIGH:**
- Stale behavioral claim that would mislead an implementer
- Count mismatch (e.g., `AGENTS.md` says N skills, actual is M)

**MEDIUM:**
- Path/name drift (hyphen vs underscore, module moved)
- Missing doc for a significant new subsystem

**LOW:**
- Minor wording drift
- Docstring describes a parameter that was renamed
