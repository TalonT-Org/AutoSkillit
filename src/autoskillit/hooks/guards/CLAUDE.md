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
| `mcp_health_guard.py` | Detects MCP server disconnection (dead PID); non-blocking advisory |
| `open_kitchen_guard.py` | Blocks `open_kitchen` from headless sessions; writes kitchen marker |
| `planner_gh_discovery_guard.py` | Blocks GitHub issue/PR listing in planner sessions |
| `pr_create_guard.py` | Blocks `gh pr create` via `run_cmd` when kitchen is open |
| `quota_guard.py` | Blocks `run_skill` when quota threshold exceeded; fails open on missing cache |
| `recipe_write_advisor.py` | Non-blocking advisory for recipe YAML writes |
| `remove_clone_guard.py` | Blocks `remove_clone` if branch has unpushed commits |
| `review_loop_gate.py` | Blocks `wait_for_ci`/`enqueue_pr` until `check_review_loop` is called |
| `skill_cmd_guard.py` | Validates `skill_command` path argument format |
| `skill_command_guard.py` | Blocks `run_skill` with non-slash `skill_command` |
| `unsafe_install_guard.py` | Blocks `pip install -e` targeting system Python |
| `write_guard.py` | Blocks Write/Edit outside allowed prefix in read-only sessions |

## Architecture Notes

Each guard is a standalone Python script executed as a subprocess (not imported as a module). Protocol: read PreToolUse JSON from stdin, write decision JSON to stdout, exit 0. Most are stdlib-only for fast startup. Guards fail-open for malformed input. `skill_command_guard.py` has split error handling: malformed JSON fails-open (approve), unexpected runtime errors fail-closed (deny).
