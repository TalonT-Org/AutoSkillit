---
name: setup-project
description: Explore a target project and generate tailored skill scripts and config through an interactive workflow. Use when user wants to onboard a new project to AutoSkillit, says "setup project", or wants a starting point config.
hooks:
  PreToolUse:
    - matcher: "*"
      hooks:
        - type: command
          command: "echo '[SKILL: setup-project] Exploring target project...'"
          once: true
---

# Setup Project Skill

Explore a target project and generate tailored skill scripts and AutoSkillit config through an interactive, workflow-first UX.

## When to Use

- User wants to onboard a new project to AutoSkillit
- User passes a project path and wants ready-to-use skill scripts
- User has no `.autoskillit/config.yaml` and wants a starting point

## Arguments

```
/setup-project {project_dir}
```

- `project_dir` — Absolute path to the target project to onboard

## Critical Constraints

**NEVER:**
- Modify any files in the target project without user confirmation at the summary gate
- Run commands that change the target project
- Create files outside `temp/setup-project/` directory (until the summary gate)
- Assume test framework — detect it from evidence
- Use Makefile or `make` in generated examples — use Taskfile/`task` if a task runner is needed
- Suggest `reset_guard_marker` config — that's a workspace concern, not project setup
- Include install instructions or "Getting Started" sections — user is already running the skill
- Hardcode `base_branch = main` — detect the current branch

**ALWAYS:**
- Read the target project using Glob, Read, and Grep — no shell commands against target
- Detect language, test framework, build system, and CI from actual files
- Present candidate workflows one by one for user approval before generating scripts
- Show a summary confirmation gate before writing anything to disk
- Use the two-directory model (project_dir + work_dir) in generated skill scripts

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
- Check for `.autoskillit/skills/` and `.claude/skills/` — list any custom skills
- Check for `CLAUDE.md` — extract project constraints
- Check for `.autoskillit/workflows/` — list any custom workflows

**Subagent E — Current Git Branch:**
- Run `git -C {project_dir} branch --show-current`
- This becomes the default `base_branch` in generated scripts

**Subagent F+ — Conversation History Mining (only if user opted in):**

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
  - **Common skill chains**: Skill invocations that follow a consistent order (e.g., `/autoskillit:investigate` -> `/autoskillit:rectify` -> `/autoskillit:implement-worktree`).
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
- Current git branch (for `base_branch` default)
- Discovered workflow patterns from conversation history (if opted in) — recurring tool sequences and skill chains, ranked by frequency, with candidate skill script drafts

### Step 3: Write Analysis to temp/

Before presenting anything interactively, write the full analysis (project profile, workflow patterns, candidate workflows, shell command patterns) to:

```
temp/setup-project/analysis_{project_name}_{YYYY-MM-DD_HHMMSS}.md
```

Tell the user: "Full analysis saved to {path} for your review."

### Step 4: Present Candidate Workflows

Interactive flow. For each candidate workflow discovered:

1. **Always offer the standard implementation pipeline first** (plan → dry-walkthrough → implement → test → merge), even if not discovered in history. This is the core AutoSkillit workflow.

2. For each candidate workflow (including the standard one):
   - Present the workflow chain and explain what it automates
   - Ask the user: "Would you like me to generate a skill script for this workflow?"
   - If yes: LOAD `/autoskillit:make-script-skill` using the Skill tool to generate the script. The agent already has full context from the exploration phases (workflow name, detected variables like project_dir/work_dir/base_branch, tool call sequence, routing logic) — no explicit parameter passing is needed. make-script-skill uses that context directly to produce a clean script.
   - Explain what a skill script is (paste into a Claude Code session with AutoSkillit tools enabled, the agent follows it step by step), show the generated script content
   - Track the user's approval — do NOT write to disk yet
   - Move to the next candidate workflow

Fill in detected values: test command, base branch, project-specific notes, any detected quirks. Use the two-directory model (project_dir + work_dir) in generated scripts. If `work_dir` equals `project_dir`, note that `add_dir` is not needed.

### Step 5: Config Updates

Interactive config suggestion flow:

1. Show the current config vs. suggested config diff
2. For each suggested change, ask the user if they want to apply it
3. Track approvals — do NOT write to disk yet
4. Do NOT suggest `reset_guard_marker` — that's a workspace concern, not project setup

If no config exists, present the suggested config in full. If config exists, only highlight missing or suboptimal settings.

Suggested config template:
```yaml
test_check:
  command: {detected test command as list}
  # timeout: 600

# classify_fix:
#   path_prefixes:
#     - {detected critical paths}

# reset_workspace:
#   command: {detected reset command}
#   preserve_dirs: []
```

### Step 6: Summary Confirmation Gate

Following the Terraform plan→apply pattern, show a summary of everything approved before touching disk:

1. For each approved skill script, ask where to save it:
   - `.autoskillit/skill_scripts/{name}.md` — AutoSkillit script (used with `run_skill`)
   - `.autoskillit/skills/{name}/SKILL.md` — Claude Code skill (invokable as `/{name}`)
2. List all approved skill scripts with their chosen save paths
3. List all approved config changes
4. Ask one final question: "Write all of the above?"
5. If confirmed:
   - Create target directories as needed
   - Write all approved skill scripts to their chosen paths
   - Apply all approved config changes
6. If declined: abort without writing anything

This prevents incremental approval fatigue and gives the user a single clear decision point.

### Step 7: Output Summary

Write a concise summary to terminal:
- Which scripts were saved and where
- Which config changes were applied
- Any notes or warnings (e.g., test timeout may need adjustment, critical paths are inferred)

Do NOT include:
- Install instructions (user is already running the skill)
- "Getting Started" sections
- Repeated content from earlier steps

## Output

Artifacts created:
- `temp/setup-project/analysis_{project_name}_{YYYY-MM-DD_HHMMSS}.md` — full analysis (always)
- `.autoskillit/skill_scripts/{name}.md` or `.autoskillit/skills/{name}/SKILL.md` — approved skill scripts (user chooses path)
- `.autoskillit/config.yaml` — updated config (if changes approved)
