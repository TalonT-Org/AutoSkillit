---
name: research-helper
description: Spawns research subagents with a documented semantic plan.
semantic_requirements:
  child_spawns:
    - role: researcher
      count: 2
---
# research-helper

Dispatches parallel research subagents and joins their results.
