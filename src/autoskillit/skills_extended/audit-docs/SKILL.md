---
name: audit-docs
categories: [audit]
description: >
  Audit documentation for drift, staleness, and inconsistency against the actual
  codebase. Use when user says "audit docs", "check documentation", "docs audit",
  or "documentation review". Spawns parallel subagents to explore codebase subsystems,
  then cross-references all documentation sources against findings.
tier: 2
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: audit-docs] Auditing documentation for staleness and drift...'"
          once: true
---

# Documentation Audit Skill

Audit all documentation sources for drift, staleness, and inconsistency against actual codebase behavior.

## When to Use

- User says "audit docs", "check documentation", "docs audit", "documentation review", "docs drift", "stale docs"

## Critical Constraints

**NEVER:**
- Modify any source files
- Update an existing report — always generate a new one
- Compare doc-to-doc without first grounding claims in actual code behavior

**ALWAYS:**
- Ground every cross-reference finding in what the code actually does (not what other docs say)
- Use subagents for parallel exploration
- Write report to `{{AUTOSKILLIT_TEMP}}/audit-docs/docs_audit_{YYYY-MM-DD_HHMMSS}.md`
- Provide file:line references for every finding
- Categorize findings by severity (CRITICAL, HIGH, MEDIUM, LOW)
- Produce a usable report even when one or more subagents fail

---

## Documentation Sources

Enumerate and audit all of the following:

- **`CLAUDE.md`** — project instructions, architecture tree, file path references, tool/skill counts, layer descriptions
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
- **Missing docs** — new subsystem or module with no mention in CLAUDE.md tree or arch docs
- **Path/name drift** — hyphen-vs-underscore mismatches, renamed symbols, old module paths
- **Count mismatches** — CLAUDE.md states N tools/skills/hooks but actual count differs
- **Inter-doc contradictions** — CLAUDE.md says A, arch doc says B about the same entity

---

## Audit Workflow

1. **Pre-flight**: Verify `{{AUTOSKILLIT_TEMP}}/audit-docs/` directory exists; create it if not.

2. **Familiarization wave** — spawn 6 parallel subagents, one per subsystem group. Each subagent reports: actual module/component names, exported symbols, behavioral summary (2–5 sentences per module). If any subagent fails, record the gap and continue.

   | Agent | Subsystems | Focus |
   |---|---|---|
   | Agent 1 | `core/`, `config/` | What these modules actually expose and do |
   | Agent 2 | `execution/`, `workspace/` | Runtime orchestration and workspace lifecycle |
   | Agent 3 | `recipe/`, `migration/` | Recipe schema, rules, validation, migration engine |
   | Agent 4 | `server/` | MCP tool surface, gating, lifespan, factory |
   | Agent 5 | `cli/`, `hooks/` | CLI commands, hook scripts, what each does |
   | Agent 6 | `skills/`, `skills_extended/` | Bundled skills, categories, tiers |

3. **Doc inventory** — enumerate all documentation sources (list files found under each source category above).

4. **Cross-reference wave** — spawn 4 parallel subagents, each checking one doc domain against the familiarization findings:

   | Agent | Domain | What to check |
   |---|---|---|
   | Agent A | `CLAUDE.md` | Architecture tree accuracy, file path references, tool/skill counts, layer descriptions |
   | Agent B | `docs/architecture/**` | Component names, module paths, layer assignments |
   | Agent C | `docs/requirements/**` and `docs/specs/**` | API surface, behavioral contracts |
   | Agent D | Recipe YAML descriptions + docstrings | Step descriptions, ingredient names, parameter docs |

5. **Consolidate** — merge findings from all 4 agents, deduplicate by file:line, assign severity.

6. **Self-validation pass** — for every CRITICAL or HIGH finding, re-read the cited file line to confirm the claim; downgrade or remove if not confirmed.

7. **Write report** to `{{AUTOSKILLIT_TEMP}}/audit-docs/docs_audit_{YYYY-MM-DD_HHMMSS}.md` (relative to the current working directory) using the format below.

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
- CLAUDE.md architecture tree listing a path that doesn't exist

**HIGH:**
- Stale behavioral claim that would mislead an implementer
- Count mismatch (e.g., CLAUDE.md says N skills, actual is M)

**MEDIUM:**
- Path/name drift (hyphen vs underscore, module moved)
- Missing doc for a significant new subsystem

**LOW:**
- Minor wording drift
- Docstring describes a parameter that was renamed
