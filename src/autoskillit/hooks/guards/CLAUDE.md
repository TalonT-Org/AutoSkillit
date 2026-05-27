# guards/

PreToolUse guard scripts — standalone Python processes enforcing tool-call policies.

## Files

| File | Purpose |
|------|---------|
| `__init__.py` | Package marker (no imports) |
| `ask_user_question_guard.py` | Blocks `AskUserQuestion` before kitchen is open |
| `branch_protection_guard.py` | Blocks merge/push targeting protected branches |
| `fleet_dispatch_guard.py` | Blocks `dispatch_food_truck` from headless sessions (prevents L3->L3 recursion) |
| `generated_file_write_guard.py` | Blocks Write/Edit to machine-generated files (`hooks.json`, `settings.json`) |
| `grep_pattern_lint_guard.py` | Blocks Grep with BRE `\|` syntax; surfaces corrected ERE pattern |
| `skill_orchestration_guard.py` | Blocks `run_skill`/`run_cmd`/`run_python` from L1 skill sessions |
| `mcp_health_advisor.py` | Detects MCP server disconnection (dead PID); non-blocking advisory |
| `open_kitchen_guard.py` | Blocks `open_kitchen` from headless sessions; writes kitchen marker |
| `planner_result_naming_guard.py` | Blocks Write/Edit with non-canonical planner result filenames (e.g. `P1-A1-WP2a_result.json`); denies with correction hint |
| `planner_gh_discovery_guard.py` | Blocks GitHub issue/PR listing in planner sessions |
| `artifact_download_guard.py` | Blocks `gh run download` and `gh release download` without `--dir` flag |
| `pr_create_guard.py` | Blocks `gh pr create` via `run_cmd` when kitchen is open |
| `quota_guard.py` | Blocks `run_skill` when quota threshold exceeded; fails open on missing cache |
| `recipe_write_advisor.py` | Non-blocking advisory for recipe YAML writes |
| `remove_clone_guard.py` | Blocks `remove_clone` if branch has unpushed commits |
| `review_loop_gate.py` | Blocks `wait_for_ci`/`enqueue_pr` until `check_review_loop` is called |
| `resume_ownership_guard.py` | Validates `resume_session_id` ownership at resume time; blocks unowned or L3 session resume |
| `skill_cmd_guard.py` | Validates `skill_command` path argument format |
| `skill_command_guard.py` | Blocks `run_skill` with non-slash `skill_command` |
| `unsafe_install_guard.py` | Blocks `pip install -e` targeting system Python |
| `skill_load_guard.py` | Denies native tools until Skill tool is called in non-Anthropic headless skill sessions; bypasses for Codex backend (`AUTOSKILLIT_AGENT_BACKEND=codex`) and subagents (`agent_id`) |
| `write_guard.py` | Blocks Write/Edit/Bash/apply_patch outside allowed prefix in write-scoped sessions |

## Architecture Notes

Each guard is a standalone Python script executed as a subprocess (not imported as a module). Protocol: read PreToolUse JSON from stdin, write decision JSON to stdout, exit 0. Most are stdlib-only for fast startup.

### Fail-Mode Contract

All guards fail-**open** for malformed/unparseable input (JSON decode failure = exit 0 = approve).
This prevents a broken hook from blocking the entire tool chain.

Three guards additionally fail-**closed** for valid input with unrecognized values, as a
defense-in-depth measure against privilege escalation:

| Guard | Fail-closed condition | Rationale |
|-------|----------------------|-----------|
| `skill_command_guard.py` | Unexpected runtime error (not JSON parse) | Unknown failure mode = deny rather than risk executing an unvalidated command |
| `open_kitchen_guard.py` | Unrecognized `AUTOSKILLIT_SESSION_TYPE` | Unknown session type should not gain kitchen access |
| `skill_orchestration_guard.py` | Unrecognized `AUTOSKILLIT_SESSION_TYPE` | Unknown session type should not call orchestration tools (`run_skill`, `run_cmd`, `run_python`) |

**Design principle:** Garbage-in (malformed hook input) = fail-open. Unknown-tier (valid input, unrecognized value) = fail-closed.

## Hook Payload Fields for Guard Development

### `agent_id` — Subagent Detection

Claude Code includes `agent_id` in the hook JSON payload (stdin) when the hook fires
inside a subagent (L0). The field is absent in top-level sessions. This is the standard
mechanism for detecting subagent context in hook scripts.

- `agent_id` — present ONLY in subagent context; absent at top-level
- `agent_type` — also present in subagent context (e.g., `"Explore"`, `"general-purpose"`)
- Subagents cannot spawn nested subagents, so the check is binary: top-level vs. one-level-deep
- Check: `if data.get("agent_id"): sys.exit(0)` for unconditional subagent exemption

Used by: `skill_load_guard.py`, `skill_load_post_hook.py`
