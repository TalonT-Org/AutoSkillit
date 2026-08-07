---
name: research-helper
description: Spawns research subagents with a documented semantic plan.
semantic_requirements:
  logical_roles:
    - name: researcher
      purpose: Investigate one research angle in parallel.
  child_spawns:
    - role: researcher
      count: 2
---
# research-helper

Dispatches parallel research subagents and joins their results.
