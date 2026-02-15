# **Automation MCP: Development Guidelines**

Mandatory instructions for AI-assisted development in this repository.

## **1. Core Project Goal**

A standalone MCP server that orchestrates automated bug-fix loops using Claude Code headless sessions. It provides 7 tools (run_cmd, run_skill, run_skill_retry, test_check, reset_test_dir, classify_fix, reset_executor) for driving worktree-based fix-and-verify cycles.

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

The project uses pytest with pytest-asyncio for async test support.

  * **Run tests**: `pytest -v` from the project root (venv must be activated)
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
src/automation_mcp/
├── __init__.py              # Package version
├── __main__.py              # python -m automation_mcp entry point
├── server.py                # FastMCP server with 7 MCP tools
└── process_lifecycle.py     # Subprocess management (kill trees, temp I/O, timeouts)

tests/
├── conftest.py              # Shared fixtures
├── test_server.py           # Server unit tests
└── test_process_lifecycle.py # Subprocess integration tests

temp/                        # Temporary/working files (gitignored)
```

### **Key Components**

  * **server.py**: FastMCP server. All tools delegate subprocess work to `process_lifecycle.run_managed_async`. The `_check_dry_walkthrough` gate blocks `/implement-worktree` without a verified plan.
  * **process_lifecycle.py**: Self-contained subprocess utilities (no internal deps, only stdlib + psutil). Handles process tree cleanup, temp file I/O to avoid pipe blocking, and configurable timeouts.

### **MCP Tools**

| Tool | Purpose |
|------|---------|
| `run_cmd` | Execute shell commands with timeout |
| `run_skill` | Run Claude Code headless with a skill command |
| `run_skill_retry` | Run Claude Code headless with API call limit (for long-running skills) |
| `test_check` | Run test suite in a worktree, returns PASS/FAIL |
| `reset_test_dir` | Clear test directory (playground safety guard) |
| `classify_fix` | Analyze worktree diff to determine restart scope (plan vs executor) |
| `reset_executor` | Reset executor status preserving .agent_data and plans |
