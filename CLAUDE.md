# **AutoSkillit: Development Guidelines**

Mandatory instructions for AI-assisted development in this repository.

## **1. Core Project Goal**

A Claude Code plugin that orchestrates automated skill-driven workflows using headless sessions. It provides 14 MCP tools (run_cmd, run_python, run_skill, run_skill_retry, test_check, merge_worktree, reset_test_dir, classify_fix, reset_workspace, read_db + ungated autoskillit_status, list_skill_scripts, load_skill_script, validate_script) with 10 gated behind MCP prompts for user-only activation, and 13 bundled skills registered as `/autoskillit:*` slash commands.

## **2. General Principles**

  * **Follow the Task Description**: The issue or ticket is your primary source of truth.
  * **Adhere to Task Scope**: Do not work on unassigned features or unrelated refactoring.
  * **Implement Faithfully**: Produce functionally correct implementations. Do not add unrequested features.
  * **Adhere to Project Standards**: Write clean, maintainable Python following established conventions.

## **3. Critical Rules - DO NOT VIOLATE**

### **3.0. Skill Invocations Are Orders**

  * When a message includes a `/skill-name`, execute it via the Skill tool **BEFORE** any other action. No exceptions.
  * Never skip or substitute a skill invocation based on your own judgment.

### **3.1. Code and Implementation**

  * **Do Not Oversimplify**: Implement logic with required complexity. No shortcuts that compromise correctness.
  * **Respect the Existing Architecture**: Build on established patterns. Understand existing code before modifying.
  * **Address the Root Cause**: Debug to find and fix root causes. No hardcoded workarounds.
  * **No Backward Compatibility Hacks**: No comments about dead code. Remove dead code entirely.
  * **Avoid Redundancy**: Do not duplicate logic or utilities.
  * **Use Current Package Versions**: Web search for current stable versions when adding dependencies.

### **3.2. File System**

  * **Temporary Files:** All temp files must go in the project's `temp/` directory.
  * **Do Not Add Root Files**: Never create new root files unless explicitly required.
  * **Never commit unless told to do so**

## **4. Testing Guidelines**

The project uses pytest with pytest-asyncio for async test support. Tests run in parallel via pytest-xdist (`-n 4`). All tests must be safe for parallel execution.

  * **Run tests**: `task test-all` from the project root. This is the **only** test command. Never use `pytest`, `python -m pytest`, or any other test runner directly.
  * **Always run tests at end of task**
  * **Fix failing tests immediately**
  * **Add tests for new features**
  * **Follow existing test patterns** in `tests/` — avoid test code redundancy

## **5. Pre-commit Hooks**

Install hooks after cloning: `pre-commit install`

Hooks run automatically on commit. To run manually: `pre-commit run --all-files`

Configured hooks: ruff format (auto-fix), ruff check (auto-fix), mypy type checking.

## **6. Architecture**

```
src/autoskillit/
├── __init__.py              # Package version
├── __main__.py              # python -m autoskillit entry point (delegates to cli)
├── .claude-plugin/          # Plugin metadata (plugin.json)
├── .mcp.json                # MCP server config for plugin loading
├── cli.py                   # CLI: serve, init, config show, skills, workflows
├── config.py                # Dataclass config + YAML loading (layered resolution)
├── script_loader.py         # Pipeline script discovery from .autoskillit/scripts/
├── server.py                # FastMCP server with 14 MCP tools + 2 prompts
├── skill_resolver.py        # Bundled skill listing
├── workflow_loader.py       # Workflow YAML loading, validation, listing
├── process_lifecycle.py     # Subprocess management (kill trees, temp I/O, timeouts)
├── skills/                  # 13 bundled skills (SKILL.md per skill)
│   ├── assess-and-merge/    │   ├── dry-walkthrough/
│   ├── implement-worktree/  │   ├── implement-worktree-no-merge/
│   ├── investigate/         │   ├── make-groups/
│   ├── make-plan/           │   ├── make-script-skill/
│   ├── mermaid/             │   ├── rectify/
│   ├── retry-worktree/      │   ├── review-approach/
│   └── setup-project/
└── workflows/               # 4 bundled workflow YAML definitions
    ├── audit-and-fix.yaml
    ├── bugfix-loop.yaml
    ├── implementation.yaml
    └── investigate-first.yaml

tests/
├── conftest.py              # Shared fixtures (tools enabled + default config)
├── test_cli.py              # CLI command tests
├── test_config.py           # Config loading tests
├── test_process_lifecycle.py # Subprocess integration tests
├── test_script_loader.py    # Script loader tests
├── test_server.py           # Server unit tests
├── test_skill_resolver.py   # Skill resolution tests
└── test_workflow_loader.py  # Workflow loading/validation tests

temp/                        # Temporary/working files (gitignored)
```

### **Key Components**

  * **config.py**: Dataclass hierarchy (`AutomationConfig`) with layered YAML resolution: defaults → user (`~/.autoskillit/config.yaml`) → project (`.autoskillit/config.yaml`). No config file = current hardcoded defaults.
  * **cli.py**: CLI entry point. `autoskillit` (no args) starts the MCP server. Also provides `init` (prints plugin-dir path), `config show`, `skills list`, `workflows list/show`, `workspace init`, `update`, and `doctor`.
  * **script_loader.py**: Discovers and loads pipeline scripts from `.autoskillit/scripts/`. Scripts use the workflow YAML schema (inputs, steps, routing, retry) with an added `summary` field. `list_scripts` returns `ScriptInfo` records for listing. `load_script` returns raw YAML for agent consumption.
  * **server.py**: FastMCP server. 10 gated tools require user activation via MCP prompts. 4 ungated tools (`autoskillit_status`, `list_skill_scripts`, `load_skill_script`, `validate_script`) are always available. Tools read settings from `_config` (module-level `AutomationConfig`). The `_check_dry_walkthrough` gate blocks `/autoskillit:implement-worktree` without a verified plan. `_plugin_dir` is passed to headless sessions via `--plugin-dir`. Registers `workflow://` resource handler.
  * **skill_resolver.py**: Lists bundled skills from the package `skills/` directory. `SkillResolver` (no args) scans for `SKILL.md` files.
  * **workflow_loader.py**: YAML workflow loading, validation, and listing. Discovers workflows from `.autoskillit/workflows/` (project) and bundled package directory.
  * **process_lifecycle.py**: Self-contained subprocess utilities (no internal deps, only stdlib + psutil). Handles process tree cleanup, temp file I/O to avoid pipe blocking, and configurable timeouts.

### **Plugin Structure**

The Python package directory (`src/autoskillit/`) is the plugin root:
  * `.claude-plugin/plugin.json` — plugin manifest (name, version, description)
  * `.mcp.json` — MCP server config (command: `autoskillit`)
  * `skills/` — 13 bundled skills discovered by Claude Code as `/autoskillit:*` slash commands
  * `pyproject.toml` declares `artifacts` to include dotfiles in the wheel

### **Skills**

13 bundled skills, invoked as `/autoskillit:<name>`. These are the building blocks that project-specific pipeline scripts (generated by `setup-project`) compose together.

Skills are discovered by Claude Code via the plugin structure. Headless sessions receive `--plugin-dir` automatically via `run_skill` and `run_skill_retry`. Project-specific pipeline scripts go in `.autoskillit/scripts/` as YAML files, discovered via `list_skill_scripts` and loaded via `load_skill_script`.

### **MCP Tools**

| Tool | Purpose |
|------|---------|
| `run_cmd` | Execute shell commands with timeout |
| `run_python` | Call a Python function by dotted module path (in-process) |
| `run_skill` | Run Claude Code headless with a skill command (passes `--plugin-dir`) |
| `run_skill_retry` | Run Claude Code headless with API call limit (passes `--plugin-dir`) |
| `test_check` | Run test suite in a worktree, returns PASS/FAIL |
| `merge_worktree` | Merge worktree branch after test gate passes |
| `reset_test_dir` | Clear test directory (reset guard marker) |
| `classify_fix` | Analyze worktree diff to determine restart scope (full vs partial) |
| `reset_workspace` | Reset workspace, preserving configured directories |
| `read_db` | Run read-only SQL query against SQLite database |
| `autoskillit_status` | Return version health and config status (ungated) |
| `list_skill_scripts` | List pipeline scripts from .autoskillit/scripts/ (ungated) |
| `load_skill_script` | Load a script by name as raw YAML (ungated) |
| `validate_script` | Validate a pipeline script against the workflow schema (ungated) |
| `enable_tools` (prompt) | User-only activation — type the enable_tools prompt from the MCP prompt list |
| `disable_tools` (prompt) | User-only deactivation — type the disable_tools prompt from the MCP prompt list |

### **Tool Activation**

10 tools are gated by default. At the start of a session, the user must type
the `enable_tools` prompt to activate. The exact prompt name is prefixed by
Claude Code based on how the server was loaded (e.g. `plugin_autoskillit_autoskillit`
for plugin installs). This uses MCP prompts (user-only, model cannot invoke)
and survives `--dangerously-skip-permissions`.

`autoskillit_status`, `list_skill_scripts`, `load_skill_script`, and `validate_script` are ungated — available without calling `enable_tools`.

### **Configuration**

All tool behavior is configurable via `.autoskillit/config.yaml`. No config file = hardcoded defaults (backward compatible). Run `autoskillit init` to generate a template.

**Resolution order:** defaults → `~/.autoskillit/config.yaml` (user) → `.autoskillit/config.yaml` (project). Project overrides user, user overrides defaults. Partial configs are fine — unset fields keep their defaults.

**Available settings:**

| Section | Key | Default | Description |
|---------|-----|---------|-------------|
| `test_check` | `command` | `["task", "test-all"]` | Test command for `test_check` and `merge_worktree` |
| `test_check` | `timeout` | `600` | Test command timeout in seconds |
| `classify_fix` | `path_prefixes` | `[]` | File path prefixes that trigger `full_restart` |
| `reset_workspace` | `command` | `null` | Reset command (`null` = not configured) |
| `reset_workspace` | `preserve_dirs` | `[]` | Directories preserved during reset |
| `implement_gate` | `marker` | `"Dry-walkthrough verified = TRUE"` | Required first line in plan files |
| `implement_gate` | `skill_names` | `["/autoskillit:implement-worktree", "/autoskillit:implement-worktree-no-merge"]` | Skills subject to dry-walkthrough gate |
| `safety` | `reset_guard_marker` | `".autoskillit-workspace"` | Marker file required for destructive ops |
| `safety` | `require_dry_walkthrough` | `true` | Enforce plan verification before implementation |
| `safety` | `test_gate_on_merge` | `true` | Run tests before allowing merge |
| `read_db` | `timeout` | `30` | Query timeout in seconds |
| `read_db` | `max_rows` | `10000` | Maximum rows returned per query |
