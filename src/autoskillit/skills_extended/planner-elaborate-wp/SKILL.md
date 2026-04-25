---
name: planner-elaborate-wp
categories: [planner]
description: Elaborate a single work package with sub-agent-filtered context awareness (Pass 3 loop body)
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: planner-elaborate-wp] Elaborating work package...'"
          once: true
---

# planner-elaborate-wp

Pass 3 loop body. Elaborates a single work package with full context awareness of prior
WPs via sub-agent filtering of `wp_index.json`. Produces the file-level implementation
details that become GitHub issues. Runs once per work package in its own headless session.

## When to Use

- Invoked by the planner recipe's Pass 3 loop when `check_remaining` returns `has_remaining: "true"`
- One invocation per work package, sequentially in phase/assignment/WP order

## Arguments

- **$1** — Absolute path to the context file written by `check_remaining`

## Critical Constraints

**NEVER:**
- Omit any mandatory result field — all fields listed in Step 5 are required
- Write output outside `{{AUTOSKILLIT_TEMP}}/planner/work_packages/`
- Exceed 5 deliverables — WPs must be completable in one implement-worktree session
- Declare a `depends_on` pointing to a WP later in execution order (forward deps only)
- Skip the `wp_index.json` append — this is a mandatory contract, not optional

**ALWAYS:**
- Spawn sub-agents in parallel with `model: "sonnet"`
- Bound deliverables to 1–5 files
- Derive the output path from context `id` (never from a `result_path` field — it doesn't exist in context)
- Append to `wp_index.json` before emitting the output token
- Emit `wp_result_path` as the output token

## Workflow

### Step 1: Read context file

Read the context file at $1:
```json
{
  "id": "P1-A2-WP1",
  "name": "Create session table migration",
  "metadata": {
    "scope": "Database migration and model for sessions",
    "estimated_files": [
      "src/db/migrations/002_sessions.py",
      "src/db/models/session.py"
    ]
  },
  "prior_results": [
    "<path>/P1-A1-WP1_result.json",
    "<path>/P1-A1-WP2_result.json"
  ],
  "wp_index_path": "<path>/wp_index.json"
}
```

### Step 2: Load wp_index.json

Read `wp_index_path`. This is a JSON array of compact entries for all prior WPs
(~200 bytes each). At ~60 prior WPs this is ~12k tokens — scan the whole array.

Each entry has: `id`, `name`, `summary`, `phase`, `assignment`, `files_touched`,
`apis_defined`, `apis_consumed`, `depends_on`, `deliverables`, `result_path`.

### Step 3: Decide complexity mode

Use **deep mode** (5 sub-agents) when ANY of:
- `metadata.estimated_files` has more than 3 files
- The WP scope crosses module or service boundaries
- Prior sub-agents in this session have found 4+ relevant WPs

Use **standard mode** (3 sub-agents) otherwise.

### Step 4: Spawn parallel sub-agents

Launch all sub-agents concurrently with `model: "sonnet"`.

**Standard (always 3):**

1. **Dependency Scanner** — Scan the wp_index array for WPs that:
   - Have `apis_defined` that match APIs this WP will consume
   - Define data models or interfaces that this WP's scope requires
   Report: list of relevant WP IDs and what they provide.

2. **File Overlap Scanner** — Scan the wp_index array for WPs where:
   - Any entry in `files_touched` or `deliverables` overlaps with this WP's `estimated_files`
   Report: list of WP IDs that touch the same files; flag any potential conflicts.

3. **Contract Scanner** — Scan the wp_index array for WPs that:
   - Consume APIs or interfaces this WP will define (this WP must define them compatibly)
   - Have `apis_consumed` entries relevant to this WP's domain
   Report: list of WP IDs with API contracts this WP must satisfy.

**Deep mode only (additional 2):**

4. **Test Infrastructure Scanner** — Scan prior results (from `prior_results`) for:
   - Shared test fixtures, base test classes, or test factories this WP can reuse
   - Test utilities in files this WP will touch
   Report: list of WP IDs with reusable test infrastructure.

5. **Configuration Scanner** — Scan prior results for:
   - Schema definitions, config patterns, or registry entries this WP depends on
   Report: list of WP IDs that establish config/schema patterns this WP extends.

### Step 5: Fetch full results for relevant WPs

Union the WP ID lists from all sub-agents. This typically yields 3–5 IDs.
For each relevant WP ID, read the full `_result.json` file via `result_path` from the
wp_index entry. This gives ~15k tokens of targeted context regardless of total WP count.

### Step 6: Elaborate the work package

With the filtered context, produce a detailed elaboration:

- **goal**: One-paragraph precise goal statement
- **summary**: One-line summary (≤120 chars) — used in plan.md and wp_index
- **technical_steps**: Ordered implementation steps with file-level specificity
- **files_touched**: All files created or modified (superset of deliverables)
- **apis_defined**: Function/class/interface names this WP introduces
- **apis_consumed**: Function/class/interface names from prior WPs this WP calls
- **depends_on**: WP IDs this WP depends on (backward only — no forward deps)
- **deliverables**: 1–5 primary output files (the artifacts tracked by validation)
- **acceptance_criteria**: Testable completion conditions

**WP sizing guidance**: A well-sized WP touches 1–5 files and completes in a single
`implement-worktree` session. If elaboration reveals the WP needs more than 5 files,
note the scope mismatch but keep deliverables ≤ 5 — flag it in acceptance criteria.

### Step 7: Write WP result

Write to `{{AUTOSKILLIT_TEMP}}/planner/work_packages/{id}_result.json`
(relative to the current working directory) where `{id}` comes from the context file (e.g., `P1-A2-WP1`).

```json
{
  "id": "P1-A2-WP1",
  "name": "Create session table migration",
  "summary": "SQLite migration and ORM model for the sessions table",
  "goal": "Implement the database migration that creates the sessions table and the corresponding SQLAlchemy model with full CRUD interface.",
  "technical_steps": [
    "Create migrations/002_sessions.py with Alembic upgrade/downgrade",
    "Define SessionModel dataclass in src/db/models/session.py",
    "Add session_id, user_id, token, expires_at columns"
  ],
  "files_touched": [
    "src/db/migrations/002_sessions.py",
    "src/db/models/session.py"
  ],
  "apis_defined": ["SessionModel", "SessionModel.create", "SessionModel.get_by_token"],
  "apis_consumed": ["UserModel.get_by_id"],
  "depends_on": ["P1-A1-WP1"],
  "deliverables": [
    "src/db/migrations/002_sessions.py",
    "src/db/models/session.py"
  ],
  "acceptance_criteria": [
    "Migration runs with `alembic upgrade head` without error",
    "SessionModel importable from src.db.models.session",
    "create() and get_by_token() pass unit tests"
  ]
}
```

### Step 8: Append to wp_index.json — MANDATORY CONTRACT

**This step is mandatory.** If the WP result is written but wp_index.json is not
updated, every subsequent WP loses visibility into this WP's files and APIs.
There is a backstop in `check_remaining` that will add a minimal `{id, name, summary}`
entry if this step is missed — but the backstop entry lacks `files_touched` and
`apis_defined`, degrading sub-agent context quality for all remaining WPs.

Read `wp_index_path`, parse the JSON array, append the new compact entry, write back:

```json
{
  "id": "P1-A2-WP1",
  "name": "Create session table migration",
  "summary": "SQLite migration and ORM model for the sessions table",
  "phase": "P1",
  "assignment": "P1-A2",
  "files_touched": ["src/db/migrations/002_sessions.py", "src/db/models/session.py"],
  "apis_defined": ["SessionModel.create", "SessionModel.get_by_token"],
  "apis_consumed": ["UserModel.get_by_id"],
  "depends_on": ["P1-A1-WP1"],
  "deliverables": ["src/db/migrations/002_sessions.py", "src/db/models/session.py"],
  "result_path": "/absolute/path/to/P1-A2-WP1_result.json"
}
```

### Step 9: Emit output token

```
wp_result_path = <absolute path to {id}_result.json>
```
