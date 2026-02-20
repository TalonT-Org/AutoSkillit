---
name: investigate-first
description: Deep investigation of a problem, architectural rectification, then implement and merge the fix. Use when user says "investigate first", "investigate and fix", or "investigate pipeline".
---

# Investigate-First Pipeline

Automated investigate → rectify → verify → implement → test → merge pipeline.

SETUP:
  - work_dir = working directory for agent sessions
  - base_branch = branch to merge fixes into (detect current branch as default)
  - problem = description of the bug, error, or question to investigate
  - target_dir = (optional) additional project directory for context

PIPELINE:
0. Verify AutoSkillit tools enabled. If not → tell user to run /mcp__autoskillit__enable_tools
0.1. Prompt user for SETUP variables (use AskUserQuestion)
1. Load the workflow: use ReadMcpResourceTool(server="autoskillit", uri="workflow://investigate-first")
2. Execute the workflow steps in order, mapping SETUP variables to workflow inputs:
   - inputs.problem = ${problem}
   - inputs.work_dir = ${work_dir}
   - inputs.target_dir = ${target_dir}
   - inputs.base_branch = ${base_branch}
3. For run_skill calls when target_dir is provided, pass add_dir=${target_dir}

ESCALATE: Stop and report what failed. Human intervention needed.
