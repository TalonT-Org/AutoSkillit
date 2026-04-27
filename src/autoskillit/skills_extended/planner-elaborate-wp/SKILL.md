---
name: planner-elaborate-wp
categories: [planner]
description: Elaborate a single work package using direct codebase analysis (L0 worker for parallel WP elaboration)
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: planner-elaborate-wp] Elaborating work package...'"
          once: true
---

# planner-elaborate-wp

L0 worker for parallel WP elaboration. Elaborates a single work package using direct
codebase analysis (Grep/Glob/Read). Returns structured JSON to the L1 dispatcher — does
NOT write files or spawn sub-agents.

## When to Use

- Invoked by the L1 dispatcher (`planner-elaborate-wps`) as a headless L0 worker
- One invocation per work package, potentially parallel within a phase

## Input

The L0 worker receives all context via the prompt from the L1 dispatcher. No context file
is read from disk. The prompt includes:

- **WP metadata**: `id`, `name`, `scope`, `estimated_files`
- **Sibling WPs** for this phase (short-form: `id`, `name`, `scope`) — for `depends_on` reference
- **Assignment context**: `goal`, `technical_approach`
- **Phase context**: `goal`, `scope`

## Critical Constraints

**NEVER:**
- Spawn sub-agents (you ARE the L0 worker)
- Write files directly — return JSON, L1 writes
- Exceed 5 deliverables per WP
- Declare forward `depends_on` (backward only — reference sibling WP IDs earlier in order)
- Append to `wp_index.json` (L1 handles indexing)
- Emit `wp_result_path` tokens (L1 handles output tokens)

**ALWAYS:**
- Use Grep/Glob/Read for codebase analysis
- Return JSON between backtick fences (` ```json ` / ` ``` `)
- Include all mandatory result fields
- Bound deliverables to 1–5 files

## Workflow

### Step 1: Parse input context

Extract WP metadata from the prompt:
- `id` (e.g., `"P1-A2-WP1"`)
- `name` (e.g., `"Create session table migration"`)
- `scope` (e.g., `"Database migration and model for sessions"`)
- `estimated_files` (e.g., `["src/db/migrations/002_sessions.py", "src/db/models/session.py"]`)
- Sibling WPs (for backward dependency references)
- Assignment/phase context (for goal alignment)

### Step 2: Direct codebase analysis

Scan the codebase using Grep, Glob, and Read:

1. **File discovery**: Glob for files matching `estimated_files` paths. If files exist, read
   them to understand current implementation. If they don't exist, identify the parent
   directory and read neighboring files for patterns.
2. **API analysis**: Grep for function signatures, class definitions, and imports in files
   this WP will touch. Identify APIs consumed (imports from other modules) and APIs defined
   (new public functions/classes).
3. **Dependency detection**: Check sibling WPs for overlapping files or API contracts.
   Reference only backward siblings (earlier in execution order) for `depends_on`.

### Step 3: Elaborate the work package

Produce a detailed elaboration with all mandatory fields:

- **id**: The WP ID from input context
- **name**: The WP name from input context
- **goal**: One-paragraph precise goal statement
- **summary**: One-line summary (≤120 chars)
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

### Step 4: Return structured JSON

Return the result as JSON between backtick fences. Do NOT write files.

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
    "Migration runs with alembic upgrade head without error",
    "SessionModel importable from src.db.models.session",
    "create() and get_by_token() pass unit tests"
  ]
}
```
