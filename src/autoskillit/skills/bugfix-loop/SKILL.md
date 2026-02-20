---
name: bugfix-loop
description: Run a test-driven bugfix loop. Resets test dir, runs tests, investigates and fixes failures in isolated worktrees. Use when user says "bugfix loop", "bugfix-loop", or "run bugfix".
---

# Bugfix Loop

Automated reset → test → investigate → fix → merge cycle.

SETUP:
  - test_dir = absolute path to the test project directory
  - helper_dir = path to the workspace for agent sessions
  - base_branch = branch to merge fixes into (detect current branch as default)

PIPELINE:
0. Verify AutoSkillit tools enabled. If not → tell user to run /mcp__autoskillit__enable_tools
0.1. Prompt user for SETUP variables (use AskUserQuestion)
1. Load the workflow: use ReadMcpResourceTool(server="autoskillit", uri="workflow://bugfix-loop")
2. Execute the workflow steps in order, mapping SETUP variables to workflow inputs:
   - inputs.test_dir = ${test_dir}
   - inputs.helper_dir = ${helper_dir}
   - inputs.base_branch = ${base_branch}
3. For run_skill calls that need test project context, pass add_dir=${test_dir}

ESCALATE: Stop and report what failed. Human intervention needed.
