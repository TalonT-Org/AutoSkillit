---
name: implementation-pipeline
description: Plan, verify, implement, test, and merge a task end-to-end. Use when user says "run pipeline", "implementation pipeline", "plan and implement", or "auto implement".
---

# Implementation Pipeline

Automated plan → verify → implement → test → merge pipeline.

SETUP:
  - work_dir = working directory for agent sessions
  - target_dir = absolute path to the target project (if different from work_dir)
  - base_branch = branch to merge into (detect current branch as default)
  - task = description of what to implement

PIPELINE:
0. Verify AutoSkillit tools enabled. If not → tell user to run /mcp__autoskillit__enable_tools
0.1. Prompt user for SETUP variables (use AskUserQuestion)
1. Load the workflow: use ReadMcpResourceTool(server="autoskillit", uri="workflow://implementation")
2. Execute the workflow steps in order, mapping SETUP variables to workflow inputs:
   - inputs.task_description = ${task}
   - inputs.work_dir = ${work_dir}
   - inputs.base_branch = ${base_branch}
3. If work_dir equals target_dir, omit add_dir from run_skill calls

ESCALATE: Stop and report what failed. Human intervention needed.
