---
name: setup-project
description: Explore a target project and generate a tailored skill script and config. Use when user wants to onboard a new project to AutoSkillit, says "setup project", or wants a starting point config.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: setup-project] Exploring target project...'"
          once: true
---

# Setup Project Skill

Explore a target project and generate a tailored skill script and AutoSkillit config.

## When to Use

- User wants to onboard a new project to AutoSkillit
- User passes a project path and wants a ready-to-use skill script
- User has no `.autoskillit/config.yaml` and wants a starting point

## Arguments

```
/setup-project {project_dir}
```

- `project_dir` — Absolute path to the target project to onboard

## Critical Constraints

**NEVER:**
- Modify any files in the target project
- Run commands that change the target project without explicit user confirmation
- Create files outside `temp/setup-project/` directory
- Assume test framework — detect it from evidence
- Use Makefile or `make` in generated examples — use Taskfile/`task` if a task runner is needed

**ALWAYS:**
- Read the target project using Glob, Read, and Grep — no shell commands against target
- Detect language, test framework, build system, and CI from actual files
- Generate a skill script tailored to what was found, not a generic template
- Output both the skill script and a suggested config in the same markdown file
- Use the two-directory model (project_dir + work_dir) in generated skill scripts
- Each invocation produces a new timestamped file — never overwrite previous output

## Workflow

### Step 0: Parse Arguments and Prompt

Extract `project_dir` from the prompt. Invocation: `/setup-project {project_dir}`. If missing, abort: "Usage: `/setup-project /absolute/path/to/project`". Resolve to absolute path. Verify the directory exists.

Then prompt the user:

> "Would you like me to also scan your Claude Code conversation history for this project to identify recurring patterns that could become skill scripts?"

Store the answer for Step 1.

### Step 1: Explore Target Project (Parallel Subagents)

Launch parallel Explore subagents against `project_dir`. If the user opted into history mining, include Subagent E in the same parallel launch:

**Subagent A — Language & Build System:**
- Read `pyproject.toml`, `setup.py`, `package.json`, `go.mod`, `Cargo.toml`, `build.gradle`, `pom.xml`
- Check for `Taskfile.yml`, `justfile`
- Detect primary language(s) and package manager

**Subagent B — Test Framework:**
- Python: `pytest.ini`, `pyproject.toml [tool.pytest]`, `conftest.py`, `tests/`
- JS/TS: `jest.config.*`, `vitest.config.*`, `.mocharc.*`
- Go: `*_test.go` files
- Rust: `#[test]` in source files
- Determine the exact test command

**Subagent C — Project Structure & Critical Paths:**
- Glob source directories (`src/`, `lib/`, `pkg/`, `app/`, `internal/`)
- Identify schema definitions, migrations, core config, API routes
- These become candidates for `classify_fix.path_prefixes`
- Detect monorepo workspaces: `pnpm-workspace.yaml`, `Cargo.toml [workspace]`, `go.work`, Nx `project.json`, Lerna `lerna.json`
- Read README.md (first 100 lines) for project description

**Subagent D — Existing AutoSkillit Config:**
- Check for `.autoskillit/config.yaml` — read if present
- Check for `.claude/skills/` — list any custom skills
- Check for `CLAUDE.md` — extract project constraints
- Check for `.autoskillit/workflows/` — list any custom workflows

**Subagent E+ — Conversation History Mining (only if user opted in):**

Claude Code stores conversation history at `~/.claude/projects/<encoded-path>/` as JSONL files (one per session). The path encoding replaces `/` and `_` with `-` (e.g., `/home/user/my_project` -> `-home-user-my-project`).

1. Compute the encoded directory name from `project_dir`
2. List all `.jsonl` files in `~/.claude/projects/<encoded-name>/`, including subagent files in `<session-uuid>/subagents/agent-*.jsonl`
3. Sort by modification time (newest first)
4. Divide into batches of ~20-30 files per subagent — launch multiple parallel subagents if needed

Each history-mining subagent:
- Reads JSONL files line by line and extracts:
  - **Skill invocations**: `type: "user"` messages where `content` contains `<command-name>/skill-name</command-name>`. Extract skill name and args.
  - **Tool call sequences**: `type: "assistant"` messages -> `message.content[]` items with `type: "tool_use"`. Extract `name` and `input`. Build ordered tool-call sequences per session.
  - **Repeated multi-step patterns**: Same tool sequence appearing across multiple sessions (e.g., always `Bash(git checkout) -> Edit -> Edit -> Bash(pytest) -> Bash(git commit)`).
  - **Common skill chains**: Skill invocations that follow a consistent order (e.g., `/investigate` -> `/rectify` -> `/implement-worktree`).
  - **Repeated `run_cmd` commands**: Exact shell commands run frequently via Bash tool.
- Returns structured findings: frequency-ranked tool sequences (min 3 occurrences), skill chains, and notable single-tool patterns

### Step 2: Synthesize Project Profile

Consolidate subagent findings into a structured profile:
- Language + package manager
- Test command (exact list form, e.g. `["pytest", "-v"]`)
- Build/lint tools
- Critical paths (for classify_fix)
- Whether a reset mechanism exists (Taskfile `clean` target, npm script, etc.)
- Existing config state (none / partial / complete)
- Discovered workflow patterns from conversation history (if opted in) — recurring tool sequences and skill chains, ranked by frequency, with candidate skill script drafts

### Step 3: Generate Skill Script

Generate a tailored implementation pipeline skill script using the SETUP/PIPELINE/ESCALATE format:

```
SETUP:
  - project_dir = {detected project path}
  - work_dir = {project_dir or separate workspace}
  - base_branch = main
  - task = "description of what to implement"

PIPELINE:
1. run_skill("/make-plan {task}", cwd=work_dir, add_dir=project_dir)
2. run_skill("/dry-walkthrough {plan_path}", cwd=work_dir)
3. run_skill_retry("/implement-worktree-no-merge {plan_path}", cwd=work_dir)
   - If context exhausted: run_skill_retry("/retry-worktree {plan_path} {worktree_path}", cwd=work_dir).
     Repeat up to 3x, then ESCALATE.
4. test_check(worktree_path)
   - PASS: merge_worktree(worktree_path, base_branch). Done.
   - FAIL: run_skill("/assess-and-merge {worktree_path} {plan_path} {base_branch}", cwd=work_dir)
     - Still failing after 3 attempts: ESCALATE

ESCALATE: Stop and report. Human intervention needed.
```

Fill in detected values: test command, project-specific notes, any detected quirks.

If `work_dir` equals `project_dir`, note that `add_dir` is not needed.

### Step 4: Generate Config Suggestion

Write a suggested `.autoskillit/config.yaml` based on findings:
```yaml
test_check:
  command: {detected test command as list}
  # timeout: 600

# Uncomment and configure if using classify_fix:
# classify_fix:
#   path_prefixes:
#     - {detected critical paths}

# Uncomment if you have a workspace reset mechanism:
# reset_workspace:
#   command: {detected reset command}
#   preserve_dirs: []
```

If config already exists, show a comparison of current vs. suggested and only highlight missing or suboptimal settings.

### Step 5: Write Output

Write to: `temp/setup-project/setup_{project_name}_{YYYY-MM-DD_HHMMSS}.md`

Output structure:
```markdown
# AutoSkillit Setup: {Project Name}

**Date:** {YYYY-MM-DD}
**Target:** {absolute project path}
**Detected:** {language}, {test framework}, {build system}

## Project Profile
{Detected facts organized as a table}

## Getting Started

### 1. Install AutoSkillit
pip install -e /path/to/autoskillit
claude mcp add autoskillit -- autoskillit

### 2. Initialize Config
autoskillit init --test-command "{detected_test_command}"
{Or: suggested config.yaml content to paste}

### 3. Enable Tools in Session
/mcp__autoskillit__enable_tools

## Skill Script: Implementation Pipeline
{The generated skill script from Step 3}

## Step-by-Step Explanation
{Brief explanation of each pipeline step}

## Notes
{Warnings: e.g., test timeout may need adjustment, critical paths are inferred}

## Discovered Patterns from Conversation History
{Only present if user opted in to history mining}
{Pattern findings with candidate skill scripts}
```

## Output

`temp/setup-project/setup_{project_name}_{YYYY-MM-DD_HHMMSS}.md`
